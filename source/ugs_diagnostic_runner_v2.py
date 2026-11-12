#!/usr/bin/env python3
"""UGS diagnostic v2: bounded phase events and direct workers only.
Native tracing is disabled before launch; no CUDA calls or tracer processes.
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



REVISION = "ugs-diagnostic-v2-events-only"


def run(command, *, project_dir, trace_log, events_file, trace_tool, phase_name):
    """Run directly: no tracer, proxy, ptrace or native diagnostic subprocess."""
    if trace_tool != "none":
        raise DiagnosticError("NATIVE_TRACE_DISABLED: 持续原生追踪无已验收开销上限；请显式使用--trace-tool none并重新验收清单")
    if not command or any(not isinstance(x,str) or "\x00" in x for x in command):
        raise DiagnosticError("command must be a nonempty argv list of strings")
    if not isinstance(phase_name,str) or not _NAME_RE.fullmatch(phase_name):
        raise DiagnosticError("invalid phase name")
    identity = _identity_from_env()
    project = _canonical_project_dir(project_dir)
    trace_name = _direct_child(project, trace_log)
    events_name = _direct_child(project, events_file)
    if trace_name == events_name: raise DiagnosticError("trace and events must differ")
    fd = _open_project_dir(project)
    try:
        empty_fd = _open_exclusive_at(fd, trace_name)
        os.close(empty_fd)  # Preserve interface: explicit zero-byte native evidence file.
    finally:
        os.close(fd)
    events_path = project / events_name
    base = {"phase":phase_name,"pid":os.getpid(),"identity":identity,
            "trace_tool":"none","trace_scope":"native tracing disabled", "diagnostic_revision":REVISION}
    def event(name, **extra):
        ns,utc=_utc_now()
        return _append_event_at(project,events_path,{**base,"event":name,"utc":utc,
             "utc_ns":ns,"monotonic_ns":time.monotonic_ns(),**extra})
    if not event("phase_started"): raise DiagnosticError("could not append stage-start event")
    env=os.environ.copy();env["UGS_DIAG_PROJECT_DIR"]=str(project);env["UGS_DIAG_EVENTS_FILE"]=str(events_path)
    try:
        child=subprocess.Popen(list(command),stdin=None,stdout=None,stderr=None,env=env,close_fds=True)
    except OSError as exc:
        event("phase_failed",error_type=type(exc).__name__,worker_exit_verified=False,exit_code=None)
        raise DiagnosticError("could not start worker: " + str(exc)) from exc
    rc=child.wait()
    event("phase_finished",exit_code=rc,worker_exit_verified=True,trace_bytes=0,
          trace_saturated=False,trace_bytes_seen=0,trace_bytes_dropped=0)
    if rc<0: os.kill(os.getpid(),-rc)
    return rc


def _main(argv=None):
    parser=argparse.ArgumentParser(description="UGS诊断v2：仅阶段标记；拒绝持续原生跟踪")
    for arg in ("project-dir","trace-log","events-file","trace-tool","phase"):
        parser.add_argument("--"+arg,required=True)
    parser.add_argument("command",nargs=argparse.REMAINDER)
    a=parser.parse_args(argv)
    command=a.command[1:] if a.command[:1]==["--"] else a.command
    try:
        return run(command,project_dir=a.project_dir,trace_log=a.trace_log,events_file=a.events_file,
                   trace_tool=a.trace_tool,phase_name=a.phase)
    except DiagnosticError as exc:
        print("UGS diagnostic runner: "+str(exc),file=sys.stderr,flush=True);return 2


if __name__=="__main__": sys.exit(_main())
