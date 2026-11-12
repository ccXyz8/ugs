#!/usr/bin/env python3
"""Bounded, project-scoped tracing for future UGS adapters.

The adapter must call this only after its initial and preload authorization,
around the worker or a CPU export/check command. This module never invokes a
verifier, changes worker registration, retries, or edits the wrapped command.

Example adapter use (after preload authorization):

    from ugs_diagnostic_runner import configure_diagnostics, mark, phase, run
    configure_diagnostics(project_dir, events_file)
    mark("cuda_set_device.begin")
    torch.cuda.set_device(rank)
    mark("cuda_set_device.end")
    with phase("verify_train"):
        verify_train(...)
    return run([...], project_dir=..., trace_log=..., events_file=...,
               trace_tool="ltrace", phase_name="gpu-worker")

Identity is read only from the UGS_* whitelist below. Trace/event paths are
required, absolute, direct children of the explicit project directory, and
never fall back to a service or temporary directory. Each native trace is at
most 4 MiB, with at most eight native trace invocations per job_id (32 MiB
total); the shared phase event file is limited to 64 KiB, 128 rows, and 16
process IDs. Stdout/stderr stay attached to the project's original log. ltrace
records selected native symbols only; strace records selected syscalls only.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _datetime
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Iterator, Optional, Sequence

MAX_TRACE_BYTES = 4 * 1024 * 1024
TRACE_SATURATION_MARKER = (
    b"\n[DIAGNOSTIC TRACE SATURATED; further trace output was drained and discarded]\n"
)
MAX_EVENT_BYTES = 64 * 1024
MAX_EVENT_ROWS = 128
MAX_EVENT_PIDS = 16
MAX_EVENTS_PER_PID = 32
MAX_NATIVE_TRACES_PER_CHAIN = 8
IDENTITY_ENV = (
    "UGS_JOB_ID",
    "UGS_RUN_ID",
    "UGS_STAGE_ID",
    "UGS_GENERATION",
    "UGS_STAGE_NONCE",
    "UGS_MANIFEST_SHA256",
)
CONTEXT_ENV = ("RANK", "LOCAL_RANK", "CUDA_VISIBLE_DEVICES")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

# Explicitly limited to CUDA initialization, device selection, and allocation
# APIs. Kernel launch APIs are intentionally absent.
_CUDA_DRIVER_SYMBOLS = (
    "cuInit",
    "cuDeviceGet",
    "cuDeviceGetCount",
    "cuDevicePrimaryCtxGetState",
    "cuDevicePrimaryCtxRetain",
    "cuDevicePrimaryCtxRelease",
    "cuDevicePrimaryCtxReset",
    "cuCtxCreate",
    "cuCtxCreate_v2",
    "cuCtxSetCurrent",
    "cuCtxDestroy",
    "cuCtxDestroy_v2",
    "cuMemAlloc",
    "cuMemAlloc_v2",
    "cuMemAllocAsync",
    "cuMemFree",
    "cuMemFree_v2",
    "cuMemFreeAsync",
    "cuGetProcAddress",
    "cuGetProcAddress_v2",
)
_CUDA_RUNTIME_SYMBOLS = (
    "cudaGetDeviceCount",
    "cudaGetDevice",
    "cudaSetDevice",
    "cudaFree",
    "cudaFreeAsync",
    "cudaMalloc",
    "cudaMallocAsync",
    "cudaMallocManaged",
    "cudaHostAlloc",
    "cudaDeviceReset",
    "cudaGetDriverEntryPoint",
    "cudaGetDriverEntryPointByVersion",
)


class DiagnosticError(ValueError):
    """A pre-launch configuration or path error."""


def _utc_now() -> tuple[int, str]:
    ns = time.time_ns()
    sec, frac = divmod(ns, 1_000_000_000)
    base = _datetime.datetime.fromtimestamp(sec, _datetime.timezone.utc)
    return ns, base.strftime("%Y-%m-%dT%H:%M:%S") + f".{frac:09d}Z"


def _identity_from_env() -> dict[str, Optional[str]]:
    identity: dict[str, Optional[str]] = {name: os.environ.get(name) for name in IDENTITY_ENV}
    missing = [name for name, value in identity.items() if not value]
    if missing:
        raise DiagnosticError("Missing UGS diagnostic identity: " + ",".join(missing))
    for name, value in identity.items():
        assert value is not None
        if len(value) > 512 or any(ord(ch) < 32 for ch in value):
            raise DiagnosticError("Invalid UGS diagnostic identity field: " + name)
    for name in CONTEXT_ENV:
        value = os.environ.get(name)
        if value is not None and (len(value) > 512 or any(ord(ch) < 32 for ch in value)):
            raise DiagnosticError("Invalid process context field: " + name)
        identity[name] = value
    return identity


def _canonical_project_dir(project_dir: os.PathLike[str] | str) -> Path:
    raw = Path(project_dir)
    if not raw.is_absolute():
        raise DiagnosticError("project directory must be an explicit absolute path")
    try:
        resolved = raw.resolve(strict=True)
    except OSError as exc:
        raise DiagnosticError("project directory is unavailable: " + str(exc)) from exc
    if resolved != raw or not resolved.is_dir():
        raise DiagnosticError("project directory must exist and contain no symlink path")
    return resolved


def _direct_child(project_dir: Path, file_path: os.PathLike[str] | str) -> str:
    raw = Path(file_path)
    if not raw.is_absolute():
        raise DiagnosticError("diagnostic output paths must be explicit absolute paths")
    if raw.parent != project_dir or raw.name in ("", ".", ".."):
        raise DiagnosticError("diagnostic output must be a direct child of project_dir")
    if not _NAME_RE.fullmatch(raw.name):
        raise DiagnosticError("invalid diagnostic output filename")
    return raw.name


def _open_project_dir(project_dir: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(str(project_dir), flags)
    except OSError as exc:
        raise DiagnosticError("cannot open explicit project directory: " + str(exc)) from exc
    if not stat.S_ISDIR(os.fstat(fd).st_mode):
        os.close(fd)
        raise DiagnosticError("diagnostic runner requires a directory file descriptor")
    return fd


def _open_exclusive_at(dir_fd: int, name: str) -> int:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        return os.open(name, flags, 0o600, dir_fd=dir_fd)
    except FileExistsError as exc:
        raise DiagnosticError("diagnostic output exists (or is a symlink): " + name) from exc
    except OSError as exc:
        raise DiagnosticError("cannot create diagnostic output " + name + ": " + str(exc)) from exc


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        count = os.write(fd, view)
        if count <= 0:
            raise OSError("short diagnostic write")
        view = view[count:]


def _event_path_from_env() -> tuple[Path, Path]:
    project_raw = os.environ.get("UGS_DIAG_PROJECT_DIR", "")
    events_raw = os.environ.get("UGS_DIAG_EVENTS_FILE", "")
    if not project_raw or not events_raw:
        raise DiagnosticError("UGS_DIAG_PROJECT_DIR and UGS_DIAG_EVENTS_FILE are required")
    project = _canonical_project_dir(project_raw)
    name = _direct_child(project, events_raw)
    return project, project / name


def configure_diagnostics(project_dir: os.PathLike[str] | str,
                          events_file: os.PathLike[str] | str) -> None:
    """Set explicit paths and establish the shared file before workers spawn."""
    project = _canonical_project_dir(project_dir)
    name = _direct_child(project, events_file)
    _identity_from_env()
    dir_fd = _open_project_dir(project)
    fd = -1
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(name, flags, 0o600, dir_fd=dir_fd)
        except FileExistsError:
            fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=dir_fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise DiagnosticError("event path is not a private regular file")
        if st.st_size > MAX_EVENT_BYTES:
            raise DiagnosticError("shared diagnostic event file exceeds 64 KiB")
    except OSError as exc:
        raise DiagnosticError("cannot establish shared event file: " + str(exc)) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        os.close(dir_fd)
    os.environ["UGS_DIAG_PROJECT_DIR"] = str(project)
    os.environ["UGS_DIAG_EVENTS_FILE"] = str(project / name)


def _append_event_at(project: Path, events_path: Path, event: dict[str, object]) -> bool:
    """Append one bounded shared event; diagnostics never interrupt the caller."""
    dir_fd = -1
    fd = -1
    operation = "open_project_directory"
    try:
        dir_fd = _open_project_dir(project)
        flags = os.O_RDWR | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        operation = "open_shared_event_file"
        try:
            fd = os.open(events_path.name, flags, 0o600, dir_fd=dir_fd)
        except FileNotFoundError:
            # A concurrent configure/first mark may race the initial O_CREAT.
            # Retry once against the still-open, identity-checked directory.
            fd = os.open(events_path.name, flags, 0o600, dir_fd=dir_fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise DiagnosticError("event path is not a private regular file")
        operation = "lock_shared_event_file"
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.lseek(fd, 0, os.SEEK_SET)
        existing = b""
        while len(existing) <= MAX_EVENT_BYTES:
            chunk = os.read(fd, min(8192, MAX_EVENT_BYTES + 1 - len(existing)))
            if not chunk:
                break
            existing += chunk
        existing_size = len(existing)
        if existing_size > MAX_EVENT_BYTES or (existing and not existing.endswith(b"\n")):
            raise DiagnosticError("event file is over limit or has an incomplete tail")
        rows = [line for line in existing.splitlines() if line]
        prior_pids: set[int] = set()
        per_pid: dict[int, int] = {}
        for line in rows:
            record = json.loads(line)
            pid = record.get("pid")
            if type(pid) is int:
                prior_pids.add(pid)
                per_pid[pid] = per_pid.get(pid, 0) + 1
        identity = event.get("identity")
        if isinstance(identity, dict):
            chain = identity.get("UGS_JOB_ID")
            traces = 0
            for line in rows:
                record = json.loads(line)
                prior_identity = record.get("identity")
                if (record.get("event") == "phase_started" and
                        record.get("trace_tool") in ("ltrace", "strace") and
                        isinstance(prior_identity, dict) and
                        prior_identity.get("UGS_JOB_ID") == chain):
                    traces += 1
            if event.get("event") == "phase_started" and event.get("trace_tool") in ("ltrace", "strace"):
                if traces >= MAX_NATIVE_TRACES_PER_CHAIN:
                    raise DiagnosticError("native trace invocation limit reached (8 per chain)")
        pid = int(event["pid"])
        new_pid = pid not in prior_pids
        if len(rows) >= MAX_EVENT_ROWS:
            raise DiagnosticError("shared diagnostic event row limit reached")
        if new_pid and len(prior_pids) >= MAX_EVENT_PIDS:
            raise DiagnosticError("shared diagnostic PID limit reached")
        if per_pid.get(pid, 0) >= MAX_EVENTS_PER_PID:
            raise DiagnosticError("per-process diagnostic event limit reached")
        data = (json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if existing_size + len(data) > MAX_EVENT_BYTES:
            raise DiagnosticError("shared diagnostic event byte limit reached")
        operation = "append_shared_event"
        _write_all(fd, data)
        os.fsync(fd)
        return True
    except Exception as exc:
        print("UGS diagnostic event write failed during " + operation + ": " + str(exc),
              file=sys.stderr, flush=True)
        return False
    finally:
        if fd >= 0:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
        if dir_fd >= 0:
            os.close(dir_fd)


def _append_event(event: dict[str, object]) -> bool:
    try:
        project, path = _event_path_from_env()
        _identity_from_env()
    except Exception as exc:
        print("UGS diagnostic event unavailable: " + str(exc), file=sys.stderr, flush=True)
        return False
    return _append_event_at(project, path, event)


def mark(name: str) -> bool:
    """Write a timestamped point marker; a logging failure is non-fatal."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        print("UGS diagnostic mark ignored: invalid marker name", file=sys.stderr, flush=True)
        return False
    try:
        identity = _identity_from_env()
    except Exception as exc:
        print("UGS diagnostic mark unavailable: " + str(exc), file=sys.stderr, flush=True)
        return False
    utc_ns, utc = _utc_now()
    return _append_event({
        "event": "mark",
        "name": name,
        "pid": os.getpid(),
        "utc": utc,
        "utc_ns": utc_ns,
        "monotonic_ns": time.monotonic_ns(),
        "identity": identity,
    })


@contextlib.contextmanager
def phase(name: str) -> Iterator[None]:
    """Record begin/end around a local phase, without changing its result."""
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name):
        print("UGS diagnostic phase ignored: invalid phase name", file=sys.stderr, flush=True)
        yield
        return
    try:
        identity = _identity_from_env()
    except Exception as exc:
        print("UGS diagnostic phase unavailable: " + str(exc), file=sys.stderr, flush=True)
        yield
        return
    pid = os.getpid()
    _emit_phase_event("phase_begin", name, identity, pid, None)
    error_type: Optional[str] = None
    try:
        yield
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        _emit_phase_event("phase_end", name, identity, pid, error_type)


def _emit_phase_event(kind: str, name: str, identity: dict[str, Optional[str]], pid: int,
                      error_type: Optional[str]) -> bool:
    utc_ns, utc = _utc_now()
    event: dict[str, object] = {
        "event": kind,
        "name": name,
        "pid": pid,
        "utc": utc,
        "utc_ns": utc_ns,
        "monotonic_ns": time.monotonic_ns(),
        "identity": identity,
    }
    if error_type:
        event["error_type"] = error_type
    return _append_event(event)


def _ltrace_expression() -> str:
    selectors = [f"{name}@libcuda.so*" for name in _CUDA_DRIVER_SYMBOLS]
    selectors.extend(f"{name}@libcudart.so*" for name in _CUDA_RUNTIME_SYMBOLS)
    return "+".join(selectors)


def _trace_argv(tool: str, command: Sequence[str], trace_fd: int) -> list[str]:
    target = ["--", *command]
    proc_fd = Path("/proc/self/fd")
    pipe_path = str(proc_fd / str(trace_fd)) if proc_fd.is_dir() else f"/dev/fd/{trace_fd}"
    if tool == "ltrace":
        exe = shutil.which("ltrace")
        if not exe:
            raise DiagnosticError("ltrace is unavailable; refusing to run without requested trace")
        return [exe, "-f", "-ttt", "-T", "-e", "__ugs_no_plt__",
                "-x", _ltrace_expression(), "-o", pipe_path, *target]
    if tool == "strace":
        exe = shutil.which("strace")
        if not exe:
            raise DiagnosticError("strace is unavailable; refusing to run without requested trace")
        return [exe, "-f", "-ttt", "-T", "-k", "-e",
                "trace=open,openat,ioctl,close", "-o", pipe_path, *target]
    if tool == "none":
        return list(command)
    raise DiagnosticError("unsupported trace tool")


def _worker_proxy(argv: Sequence[str]) -> int:
    """Run the real worker and send its exact wait status on the inherited pipe."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--result-fd", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(list(argv))
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    result: dict[str, object]
    if not command:
        result = {"worker_started": False, "exit_code": None, "error_type": "EmptyCommand"}
    else:
        try:
            child = subprocess.Popen(command, stdin=None, stdout=None, stderr=None,
                                     close_fds=True)
            result = {"worker_started": True, "exit_code": child.wait()}
        except OSError as exc:
            result = {"worker_started": False, "exit_code": None,
                      "error_type": type(exc).__name__}
    try:
        _write_all(args.result_fd, (json.dumps(result, separators=(",", ":")) + "\n").encode())
    except OSError as exc:
        print("UGS worker exit side-channel write failed: " + str(exc), file=sys.stderr, flush=True)
        return 125
    return 0


def _trace_argv_with_proxy(tool: str, command: Sequence[str], trace_fd: int,
                           result_fd: int) -> list[str]:
    proxy = [sys.executable, "-B", str(Path(__file__).resolve()), "__worker_proxy",
             "--result-fd", str(result_fd), "--", *command]
    return _trace_argv(tool, proxy, trace_fd)


def _read_worker_result(fd: int) -> dict[str, object]:
    data = bytearray()
    while len(data) <= 1024:
        chunk = os.read(fd, min(256, 1025 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
    if len(data) > 1024:
        raise DiagnosticError("worker exit side-channel exceeded 1 KiB")
    record = json.loads(data)
    if (not isinstance(record, dict) or record.get("worker_started") is not True or
            type(record.get("exit_code")) is not int):
        raise DiagnosticError("worker exit side-channel missing or invalid")
    return record


def _scope(tool: str) -> str:
    if tool == "ltrace":
        return ("selected libcuda/libcudart initialization, device and allocation symbols; "
                "best-effort native symbol tracing, not exhaustive CUDA API coverage")
    if tool == "strace":
        return ("open/openat/ioctl/close syscalls with stacks; syscall evidence only, "
                "not CUDA API tracing")
    return "tracing disabled"


class _TraceSink:
    def __init__(self, fd: int):
        self.fd = fd
        self.bytes_written = 0
        self.data_written = 0
        self.total_seen = 0
        self.saturated = False
        self.write_error: Optional[str] = None
        self.data_limit = MAX_TRACE_BYTES - len(TRACE_SATURATION_MARKER)

    def consume(self, chunk: bytes) -> None:
        self.total_seen += len(chunk)
        if self.write_error:
            return
        if self.saturated:
            return
        room = self.data_limit - self.bytes_written
        keep = min(max(room, 0), len(chunk))
        if keep:
            try:
                _write_all(self.fd, chunk[:keep])
                self.bytes_written += keep
                self.data_written += keep
            except OSError as exc:
                self.write_error = str(exc)
                print("UGS trace log write failed; continuing to drain: " + self.write_error,
                      file=sys.stderr, flush=True)
                return
        if keep < len(chunk):
            try:
                _write_all(self.fd, TRACE_SATURATION_MARKER)
                self.bytes_written += len(TRACE_SATURATION_MARKER)
                self.saturated = True
            except OSError as exc:
                self.write_error = str(exc)
                print("UGS trace saturation marker write failed; continuing to drain: " + self.write_error,
                      file=sys.stderr, flush=True)


def run(command: Sequence[str], *, project_dir: os.PathLike[str] | str,
        trace_log: os.PathLike[str] | str, events_file: os.PathLike[str] | str,
        trace_tool: str, phase_name: str) -> int:
    """Run one authorized child without capturing its stdout or stderr."""
    if not command or any(not isinstance(x, str) or "\x00" in x for x in command):
        raise DiagnosticError("command must be a nonempty argv list of strings")
    if trace_tool not in ("ltrace", "strace", "none"):
        raise DiagnosticError("trace-tool must be ltrace, strace, or none")
    if not isinstance(phase_name, str) or not _NAME_RE.fullmatch(phase_name):
        raise DiagnosticError("invalid stage phase name")
    identity = _identity_from_env()
    project = _canonical_project_dir(project_dir)
    trace_name = _direct_child(project, trace_log)
    events_name = _direct_child(project, events_file)
    if trace_name == events_name:
        raise DiagnosticError("trace log and event file must be different files")
    dir_fd = _open_project_dir(project)
    trace_fd = -1
    read_fd = -1
    write_fd = -1
    result_read_fd = -1
    result_write_fd = -1
    proc: Optional[subprocess.Popen[bytes]] = None
    started_written = False
    start_utc_ns, start_utc = _utc_now()
    start_mono_ns = time.monotonic_ns()
    base_event = {
        "phase": phase_name,
        "pid": os.getpid(),
        "identity": identity,
        "trace_tool": trace_tool,
        "trace_scope": _scope(trace_tool),
    }
    events_path = project / events_name

    def record_prelaunch_failure(exc: BaseException) -> None:
        if not started_written:
            return
        end_utc_ns, end_utc = _utc_now()
        _append_event_at(project, events_path, {
            **base_event, "event": "phase_failed", "utc": end_utc,
            "utc_ns": end_utc_ns, "monotonic_ns": time.monotonic_ns(),
            "error_type": type(exc).__name__, "exit_code": None,
            "worker_exit_verified": False,
        })

    try:
        trace_fd = _open_exclusive_at(dir_fd, trace_name)
        started = {
            **base_event, "event": "phase_started", "utc": start_utc,
            "utc_ns": start_utc_ns, "monotonic_ns": start_mono_ns,
        }
        if not _append_event_at(project, events_path, started):
            raise DiagnosticError("could not append bounded stage-start event")
        started_written = True
        child_env = os.environ.copy()
        child_env["UGS_DIAG_PROJECT_DIR"] = str(project)
        child_env["UGS_DIAG_EVENTS_FILE"] = str(events_path)

        if trace_tool != "none":
            if os.name != "posix" or not (Path("/proc/self/fd").is_dir() or Path("/dev/fd").is_dir()):
                raise DiagnosticError("traced mode requires POSIX inherited-fd paths")
            read_fd, write_fd = os.pipe()
            result_read_fd, result_write_fd = os.pipe()
            os.set_inheritable(write_fd, True)
            os.set_inheritable(result_write_fd, True)
            argv = _trace_argv_with_proxy(trace_tool, command, write_fd, result_write_fd)
            proc = subprocess.Popen(argv, stdin=None, stdout=None, stderr=None, env=child_env,
                                    close_fds=True, pass_fds=(write_fd, result_write_fd))
            os.close(write_fd)
            write_fd = -1
            os.close(result_write_fd)
            result_write_fd = -1
            sink = _TraceSink(trace_fd)
            while True:
                chunk = os.read(read_fd, 65536)
                if not chunk:
                    break
                sink.consume(chunk)
            os.close(read_fd)
            read_fd = -1
            trace_tool_returncode = proc.wait()
            try:
                result_record = _read_worker_result(result_read_fd)
                returncode = result_record["exit_code"]
                worker_exit_verified = True
                runner_error = None
            except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                print("UGS worker exit side-channel invalid; returning failure: " + str(exc),
                      file=sys.stderr, flush=True)
                returncode = 125
                worker_exit_verified = False
                runner_error = type(exc).__name__
            os.close(result_read_fd)
            result_read_fd = -1
            trace_bytes = sink.bytes_written
            saturated = sink.saturated
            write_error = sink.write_error
            dropped_bytes = max(0, sink.total_seen - sink.data_written)
        else:
            argv = list(command)
            proc = subprocess.Popen(argv, stdin=None, stdout=None, stderr=None, env=child_env, close_fds=True)
            returncode = proc.wait()
            trace_bytes = 0
            saturated = False
            write_error = None
            dropped_bytes = 0
            trace_tool_returncode = returncode
            worker_exit_verified = True
            runner_error = None

        end_utc_ns, end_utc = _utc_now()
        end_mono_ns = time.monotonic_ns()
        _append_event_at(project, events_path, {
            **base_event, "event": "phase_finished", "utc": end_utc,
            "utc_ns": end_utc_ns, "monotonic_ns": end_mono_ns,
            "exit_code": returncode, "trace_bytes": trace_bytes,
            "trace_saturated": saturated, "trace_log_error": write_error,
            "trace_bytes_seen": sink.total_seen if trace_tool != "none" else 0,
            "trace_bytes_dropped": dropped_bytes,
            "trace_tool_exit_code": trace_tool_returncode,
            "worker_exit_verified": worker_exit_verified,
            "runner_error": runner_error,
        })
        if returncode < 0:
            # Preserve signal-style termination for the parent/guardian.
            os.kill(os.getpid(), -returncode)
        return returncode
    except DiagnosticError as exc:
        record_prelaunch_failure(exc)
        raise
    except OSError as exc:
        if proc is None:
            record_prelaunch_failure(exc)
            raise DiagnosticError("diagnostic runner could not start child: " + str(exc)) from exc
        print("UGS diagnostic I/O error; child was not signalled: " + str(exc),
              file=sys.stderr, flush=True)
        # Keep the pipe reader open and drain any remaining trace before wait;
        # closing it early could deliver SIGPIPE to the tracer/worker tree.
        if read_fd >= 0:
            while True:
                try:
                    chunk = os.read(read_fd, 65536)
                except InterruptedError:
                    continue
                except OSError as drain_error:
                    print("UGS trace pipe remains open after read error: " + str(drain_error),
                          file=sys.stderr, flush=True)
                    break
                if not chunk:
                    break
                try:
                    if 'sink' in locals():
                        sink.consume(chunk)
                except Exception as sink_error:
                    print("UGS trace sink failed; continuing to drain: " + str(sink_error),
                          file=sys.stderr, flush=True)
        tool_exit = proc.wait()
        end_utc_ns, end_utc = _utc_now()
        _append_event_at(project, events_path, {
            **base_event, "event": "phase_finished", "utc": end_utc,
            "utc_ns": end_utc_ns, "monotonic_ns": time.monotonic_ns(),
            "exit_code": 125, "trace_tool_exit_code": tool_exit,
            "worker_exit_verified": False, "runner_error": type(exc).__name__,
            "trace_bytes": getattr(locals().get("sink"), "bytes_written", 0),
            "trace_bytes_seen": getattr(locals().get("sink"), "total_seen", 0),
            "trace_bytes_dropped": 0,
        })
        return 125
    finally:
        for fd in (read_fd, write_fd, result_read_fd, result_write_fd):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        for fd in (trace_fd,):
            if fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
        os.close(dir_fd)


def _main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "__worker_proxy":
        return _worker_proxy(argv[1:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", required=True)
    parser.add_argument("--trace-log", required=True)
    parser.add_argument("--events-file", required=True)
    parser.add_argument("--trace-tool", choices=("ltrace", "strace", "none"), required=True)
    parser.add_argument("--phase", dest="phase_name", required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = list(args.command)
    if command and command[0] == "--":
        command.pop(0)
    try:
        return run(command, project_dir=args.project_dir, trace_log=args.trace_log,
                   events_file=args.events_file, trace_tool=args.trace_tool,
                   phase_name=args.phase_name)
    except DiagnosticError as exc:
        print("UGS diagnostic runner: " + str(exc), file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(_main())
