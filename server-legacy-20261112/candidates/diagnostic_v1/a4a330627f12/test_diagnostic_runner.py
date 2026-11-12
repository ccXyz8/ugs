"""Isolated CPU checks for bounded diagnostics; no CUDA or UGS service calls."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

SOURCE = Path(__file__).resolve().parent
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

import ugs_diagnostic_runner as diag


IDENTITY = {
    "UGS_JOB_ID": "job-cpu-test",
    "UGS_RUN_ID": "run-cpu-test",
    "UGS_STAGE_ID": "cpu-check",
    "UGS_GENERATION": "generation-cpu-test",
    "UGS_STAGE_NONCE": "nonce-cpu-test",
    "UGS_MANIFEST_SHA256": "a" * 64,
    "RANK": "0",
    "LOCAL_RANK": "0",
    "CUDA_VISIBLE_DEVICES": "",
}


class DiagnosticRunnerCPU(unittest.TestCase):
    def env(self, project):
        env = os.environ.copy()
        env.update(IDENTITY)
        env["UGS_DIAG_PROJECT_DIR"] = str(project)
        env["UGS_DIAG_EVENTS_FILE"] = str(project / "events.jsonl")
        return env

    def command(self, project, trace_name, code, tool="none", phase="cpu-check"):
        return [sys.executable, "-B", str(SOURCE / "ugs_diagnostic_runner.py"),
                "--project-dir", str(project), "--trace-log", str(project / trace_name),
                "--events-file", str(project / "events.jsonl"), "--trace-tool", tool,
                "--phase", phase, "--", sys.executable, "-B", "-c", code]

    def test_worker_exitcode_and_original_stdout_stderr_are_preserved(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-cpu-") as raw:
            project = Path(raw).resolve()
            result = subprocess.run(self.command(
                project, "cpu.trace", "import sys;print('stdout-kept');print('stderr-kept',file=sys.stderr);sys.exit(23)"),
                env=self.env(project), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 23)
            self.assertIn("stdout-kept", result.stdout)
            self.assertIn("stderr-kept", result.stderr)
            rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["event"], "phase_finished")
            self.assertEqual(rows[-1]["exit_code"], 23)
            self.assertTrue(rows[-1]["worker_exit_verified"])
            self.assertEqual(rows[0]["utc_ns"].__class__, int)
            self.assertEqual(rows[0]["monotonic_ns"].__class__, int)
            self.assertEqual(rows[0]["identity"]["UGS_MANIFEST_SHA256"], "a" * 64)

    def test_trace_sink_caps_data_appends_marker_and_keeps_draining(self):
        with tempfile.TemporaryFile() as output:
            sink = diag._TraceSink(output.fileno())
            sink.consume(b"a" * (diag.MAX_TRACE_BYTES + 100))
            sink.consume(b"b" * 50)
            output.seek(0)
            data = output.read()
            self.assertLessEqual(len(data), diag.MAX_TRACE_BYTES)
            self.assertIn(diag.TRACE_SATURATION_MARKER, data)
            self.assertTrue(sink.saturated)
            self.assertEqual(sink.total_seen, diag.MAX_TRACE_BYTES + 150)
            self.assertGreater(sink.total_seen - sink.data_written, 0)

    def test_fake_ltrace_uses_private_trace_pipe_and_keeps_worker_stderr_separate(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-ltrace-cpu-") as raw:
            project = Path(raw).resolve()
            bindir = project / "bin"
            bindir.mkdir()
            fake = bindir / "ltrace"
            fake.write_text(
                "#!" + sys.executable + "\n"
                "import os,subprocess,sys\n"
                "args=sys.argv[1:]\n"
                "assert args[args.index('-e')+1]=='__ugs_no_plt__'\n"
                "assert '-x' in args and '-f' in args and '-ttt' in args and '-T' in args\n"
                "path=args[args.index('-o')+1]\n"
                "with open(path,'wb',buffering=0) as f:\n"
                "  chunk=b'TRACE-ONLY-'*8192\n"
                "  for _ in range(80): f.write(chunk)\n"
                "cmd=args[args.index('--')+1:]\n"
                "p=subprocess.Popen(cmd,close_fds=False)\n"
                "p.wait()\n"
                "sys.exit(0)\n", encoding="utf-8")
            fake.chmod(0o755)
            env = self.env(project)
            env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
            result = subprocess.run(self.command(
                project, "native.trace", "import sys;print('worker-stdout');print('worker-stderr',file=sys.stderr);sys.exit(7)",
                tool="ltrace", phase="train-init"), env=env,
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 7)
            self.assertIn("worker-stdout", result.stdout)
            self.assertIn("worker-stderr", result.stderr)
            trace = (project / "native.trace").read_bytes()
            self.assertLessEqual(len(trace), diag.MAX_TRACE_BYTES)
            self.assertTrue(trace.endswith(diag.TRACE_SATURATION_MARKER))
            self.assertNotIn(b"worker-stderr", trace)
            rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
            self.assertEqual(rows[-1]["exit_code"], 7)
            self.assertEqual(rows[-1]["trace_tool_exit_code"], 0)
            self.assertTrue(rows[-1]["trace_saturated"])
            self.assertGreater(rows[-1]["trace_bytes_dropped"], 0)

    def test_native_trace_limit_is_eight_per_job_across_run_ids(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-budget-") as raw:
            project = Path(raw).resolve()
            bindir = project / "bin"
            bindir.mkdir()
            fake = bindir / "ltrace"
            fake.write_text(
                "#!" + sys.executable + "\n"
                "import subprocess,sys\n"
                "a=sys.argv[1:];p=a[a.index('-o')+1]\n"
                "with open(p,'wb') as f:f.write(b'native-trace\\n')\n"
                "child=subprocess.Popen(a[a.index('--')+1:],close_fds=False)\n"
                "child.wait();sys.exit(0)\n", encoding="utf-8")
            fake.chmod(0o755)
            for index in range(9):
                env = self.env(project)
                env["PATH"] = str(bindir) + os.pathsep + env.get("PATH", "")
                env["UGS_RUN_ID"] = "run-%d" % index
                result = subprocess.run(self.command(
                    project, "native-%d.trace" % index, "pass", tool="ltrace", phase="train-init"),
                    env=env, capture_output=True, text=True, timeout=15)
                self.assertEqual(result.returncode, 0 if index < 8 else 2, result.stderr)
            rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
            starts = [row for row in rows if row["event"] == "phase_started" and row["trace_tool"] in ("ltrace", "strace")]
            self.assertEqual(len(starts), 8)
            self.assertEqual(len({row["identity"]["UGS_RUN_ID"] for row in starts}), 8)
            self.assertLessEqual(sum((project / ("native-%d.trace" % i)).stat().st_size for i in range(8)),
                                 32 * 1024 * 1024)

    def test_concurrent_event_appends_and_64k_byte_limit(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-concurrent-") as raw:
            project = Path(raw).resolve()
            code = ("import sys;sys.path.insert(0,%r);import ugs_diagnostic_runner as d;"
                    "d.configure_diagnostics(sys.argv[1],sys.argv[2]);"
                    "[d.mark('concurrent-mark') for _ in range(10)]") % str(SOURCE)
            def writer(index):
                env = self.env(project)
                env["UGS_RUN_ID"] = "run-worker-%d" % index
                return subprocess.run([sys.executable, "-B", "-c", code, str(project),
                                       str(project / "events.jsonl")], env=env,
                                      capture_output=True, text=True, timeout=15)
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(writer, range(4)))
            self.assertTrue(all(result.returncode == 0 and not result.stderr for result in results),
                            [(result.returncode, result.stderr) for result in results])
            rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
            self.assertEqual(len(rows), 40)
            self.assertEqual(len({row["pid"] for row in rows}), 4)
            self.assertLessEqual((project / "events.jsonl").stat().st_size, diag.MAX_EVENT_BYTES)

        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-event-cap-") as raw:
            project = Path(raw).resolve()
            env = self.env(project)
            with mock.patch.dict(os.environ, env, clear=True):
                identity = diag._identity_from_env()
                utc_ns, utc = diag._utc_now()
                pending = {"event": "mark", "name": "would-exceed-event-cap",
                           "pid": os.getpid(), "utc": utc, "utc_ns": utc_ns,
                           "monotonic_ns": 1234567890123456, "identity": identity}
                pending_bytes = (json.dumps(pending, ensure_ascii=True, sort_keys=True,
                                            separators=(",", ":")) + "\n").encode()
                target_size = diag.MAX_EVENT_BYTES - len(pending_bytes) + 1
                seed = {"event": "mark", "pid": 42, "identity": identity, "padding": ""}
                seed_base = json.dumps(seed, ensure_ascii=True, sort_keys=True,
                                       separators=(",", ":")).encode()
                padding = target_size - len(seed_base) - 1
                seed["padding"] = "x" * padding
                seed_bytes = (json.dumps(seed, ensure_ascii=True, sort_keys=True,
                                          separators=(",", ":")) + "\n").encode()
                self.assertEqual(len(seed_bytes), target_size)
                (project / "events.jsonl").write_bytes(seed_bytes)
                before = (project / "events.jsonl").stat().st_size
                self.assertFalse(diag.mark("would-exceed-event-cap"))
            self.assertEqual((project / "events.jsonl").stat().st_size, before)
            self.assertLessEqual(before, diag.MAX_EVENT_BYTES)

    def test_trace_symlink_is_rejected_without_touching_target_or_running_child(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-symlink-") as raw:
            project = Path(raw).resolve()
            outside = project.parent / (project.name + "-sentinel")
            outside.write_text("preserve")
            try:
                (project / "cpu.trace").symlink_to(outside)
                result = subprocess.run(self.command(
                    project, "cpu.trace", "from pathlib import Path;Path(%r).write_text('yes')" % str(project / "ran")),
                    env=self.env(project), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertIn("symlink", result.stderr)
                self.assertEqual(outside.read_text(), "preserve")
                self.assertFalse((project / "ran").exists())
                self.assertTrue((project / "cpu.trace").is_symlink())
            finally:
                outside.unlink(missing_ok=True)

    def test_event_symlink_is_rejected_without_touching_target_or_running_child(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-event-symlink-") as raw:
            project = Path(raw).resolve()
            outside = project.parent / (project.name + "-events-sentinel")
            outside.write_text("preserve")
            try:
                (project / "events.jsonl").symlink_to(outside)
                result = subprocess.run(self.command(
                    project, "trace.log", "from pathlib import Path;Path(%r).write_text('yes')" % str(project / "ran")),
                    env=self.env(project), capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(outside.read_text(), "preserve")
                self.assertFalse((project / "ran").exists())
                self.assertTrue((project / "events.jsonl").is_symlink())
                self.assertTrue((project / "trace.log").exists())
            finally:
                outside.unlink(missing_ok=True)

    def test_shared_event_append_is_locked_bounded_and_context_marks_are_timestamped(self):
        with tempfile.TemporaryDirectory(prefix="ugs-diagnostic-events-") as raw:
            project = Path(raw).resolve()
            old = os.environ.copy()
            try:
                os.environ.update(self.env(project))
                diag.configure_diagnostics(project, project / "events.jsonl")
                with diag.phase("verify_train"):
                    self.assertTrue(diag.mark("verify_train.checkpoint"))
                rows = [json.loads(line) for line in (project / "events.jsonl").read_text().splitlines()]
                self.assertEqual([row["event"] for row in rows], ["phase_begin", "mark", "phase_end"])
                self.assertTrue(all(isinstance(row["utc_ns"], int) and isinstance(row["monotonic_ns"], int) for row in rows))
                self.assertLessEqual((project / "events.jsonl").stat().st_size, diag.MAX_EVENT_BYTES)
            finally:
                os.environ.clear()
                os.environ.update(old)


if __name__ == "__main__":
    unittest.main()
