#!/usr/bin/env python3
"""Read-only CPU-release launch check. This release grants NO GPU launches."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sys
ROOT = Path("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler")
VERSION = "ugs-verify-v1-cpu-only"
def canonical(x):
    return json.dumps(x, sort_keys=True, separators=(",", ":")).encode()
def read_state():
    # Existing files only: no mkdir, journal append, or lock creation.
    with (ROOT / "state.lock").open("rb") as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        revision = 0
        state = None
        with (ROOT / "events.jsonl").open("rb") as stream:
            for line in stream:
                if not line.endswith(b"\n"):
                    raise ValueError("incomplete_journal")
                item = json.loads(line)
                state = item["state"]
                if state["revision"] != revision + 1:
                    raise ValueError("revision_mismatch")
                if hashlib.sha256(canonical(state)).hexdigest() != item["sha256"]:
                    raise ValueError("checksum_mismatch")
                revision = state["revision"]
        if state is None:
            raise ValueError("missing_state")
        return state
def diagnose(state, env):
    # No accepted executor exists in this release. No environment or JSON field
    # can turn this into an authorization. Replace only via an accepted release.
    daemon = state.get("daemon") or {}
    active = state.get("active") or {}
    keys = {"UGS_RUN_ID": "run_id", "UGS_STAGE_ID": "stage_id",
            "UGS_STAGE_NONCE": "stage_nonce"}
    matches = bool(active) and all(
        bool(env.get(k)) and env[k] == active.get(v) for k, v in keys.items())
    return {
        "verifier": VERSION,
        "allowed": False,
        "reason": "NO_ACCEPTED_GPU_EXECUTOR",
        "service_version": daemon.get("version"),
        "revision": state["revision"],
        "dispatch_enabled": state.get("policy", {}).get("dispatch_enabled") is True,
        "migration_cleared": state.get("policy", {}).get("migration_cleared") is True,
        "identity_fields_match": matches,
        "identity_fields_are_not_authorization": True,
        "next_action": "CPU preparation only; await an accepted executor and verifier release"
    }
def main():
    try:
        if os.uname().nodename.split(".")[0] != "gpu07":
            raise ValueError("wrong_node")
        result = diagnose(read_state(), os.environ)
    except Exception as exc:
        result = {"verifier": VERSION, "allowed": False,
                  "reason": "CHECK_FAILED", "error": type(exc).__name__}
    print(json.dumps(result, sort_keys=True))
    return 2
if __name__ == "__main__":
    sys.exit(main())
