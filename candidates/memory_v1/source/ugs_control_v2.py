"""Runtime control extension; keeps the frozen v3.1 stage-verifier contract."""
import copy
import json
import signal
import sys
import time
from pathlib import Path
import ugs_exec_v3_1 as core
from ugs_memory_policy_v1 import fallback_candidates, latch_fallback

REVISION = "ugs-control-v2"
_original_scoped = core.scoped
_original_cli = core.cli
_original_summary = core.summary


def scoped(state, job_id, project=None):
    job = core.get_job(state, job_id)
    return _original_scoped(state, job_id, project if project is not None else job["project_id"])


def gpu_contexts_gone(active):
    proof = json.loads(Path(active["guardian_result"]).read_text())
    if (proof.get("generation") != active["generation"] or
            proof.get("stage_nonce") != active["stage_nonce"] or
            proof.get("all_children_reaped") is not True or proof.get("remaining")):
        raise ValueError("Invalid descendant cleanup proof")
    identities = list(active.get("stop_identities", [])) + list(proof.get("exits", []))
    if active.get("process"):
        identities.append(active["process"])
    pids = set()
    for record in identities:
        pid = record.get("pid")
        if type(pid) is not int or pid <= 0:
            raise ValueError("Unknown descendant PID in cleanup proof")
        pids.add(str(pid))
    with core.Store().tx() as state:
        policy = copy.deepcopy(state["policy"])
        daemon = copy.deepcopy(state["daemon"])
    cards = core.observe(policy, daemon, "cleanup_recheck")["cards"]
    # Cleanup queries share the same persisted history as all other observers.
    if not set(active.get("gpus", [])).issubset({c["uuid"] for c in cards}):
        raise ValueError("Allocated GPU missing from cleanup observation")
    # Check all visible devices, including a worker that used an unexpected GPU.
    if any(str(p["pid"]) in pids for c in cards for p in c["processes"]):
        return False
    # Only verified process-free allocated cards may trigger the global latch.
    eligible = [card for card in cards if card["uuid"] in active.get("gpus", [])
                and card.get("processes") == []
                and core.baseline_valid(policy.get("baseline", {}).get(card["uuid"]))]
    if fallback_candidates(policy, eligible, active.get("gpus", [])):
        with core.Store().tx("idle_memory_fallback") as current:
            triggers = fallback_candidates(current["policy"], eligible, active.get("gpus", []))
            latch_fallback(current["policy"], triggers, {
                "at": core.stamp(), "job_id": active.get("job_id"),
                "run_id": active.get("run_id"), "stage_index": active.get("stage_index"),
                "generation": active.get("generation"),
            })
        # The baseline policy signature changes, so old idle samples are invalid.
        # The current chain remains cleanup-pending; never finalize on this pass.
        return False
    baselines = policy.get("baseline", {})
    for card in cards:
        if card["uuid"] not in active.get("gpus", []) or card["processes"]:
            continue  # Other users' contexts are not ours to release or stop.
        baseline = baselines.get(card["uuid"])
        if not core.baseline_valid(baseline) or card.get("driver_version") != baseline["driver_version"]:
            raise ValueError("Cannot verify memory release against a current reviewed baseline")
        if not core.memory_matches(card, baseline):
            return False
        if any(card.get(key) != baseline[key] for key in
               ("memory_reserved_mib", "memory_total_mib")):
            return False
    return True


def stop_owned(job_id):
    store = core.Store()
    with store.tx() as state:
        active = copy.deepcopy(state["active_runs"].get(job_id))
    if not active or active.get("continuation"):
        return
    guardian = active.get("guardian") or active.get("guardian_spawn")
    if not guardian or not core.live(guardian):
        return
    owned = {(p["pid"], p["start"]): p for p in active.get("stop_identities", [])}
    try:
        for _ in range(3):
            for record in core.process_tree(guardian):
                owned[(record["pid"], record["start"])] = record
                core.exact_signal(record, signal.SIGSTOP)
            time.sleep(.05)
        with store.tx("stop_identity_snapshot") as state:
            current = state["active_runs"].get(job_id)
            if not current or current.get("generation") != active["generation"]:
                return
            current["stop_identities"] = list(owned.values())
            current["stop_signalled_at"] = core.stamp()
    finally:
        # SIGSTOP is only a short anti-fork step, never a retained pause mode.
        # Even a journal-write error must not leave captured workers frozen.
        for record in owned.values():
            core.exact_signal(record, signal.SIGKILL)


def detect_lost_leaders():
    with core.Store().tx() as state:
        records = copy.deepcopy(state["active_runs"])
    for job_id, active in records.items():
        if active.get("continuation") or active.get("cancel_requested"):
            continue
        if not active.get("guardian") and time.monotonic() > active.get("grant_deadline", 0):
            with core.Store().tx("launch_registration_missing") as state:
                if job_id in state["active_runs"]:
                    state["recovery"] = {"job_id": job_id, "reason": "Guardian registration missing; never relaunch"}
        elif core.live(active.get("guardian")) and active.get("process") and not core.live(active["process"]):
            # A receipt is not proof that lingering descendants have exited.
            missing = active.get("leader_missing_since")
            if missing is not None and time.monotonic() - missing < 5:
                continue
            with core.Store().tx("leader_exit_cleanup_observed") as state:
                current = state["active_runs"].get(job_id)
                if not current or current.get("generation") != active["generation"]:
                    continue
                if missing is None:
                    current["leader_missing_since"] = time.monotonic()
                elif core.live(current.get("guardian")) and not core.live(current.get("process")):
                    core.request_stop(state, core.get_job(state, job_id),
                                      "Adapter exited but descendants/guardian did not finish within 5s", "FAILED")


def finalize_available(idle=None):
    store = core.Store()
    with store.tx() as state:
        records = copy.deepcopy(state["active_runs"])
    for job_id, active in records.items():
        if active.get("continuation"):
            continue
        try:
            if active.get("cancel_requested"):
                core.stop_owned(job_id)
                with store.tx() as state:
                    active = copy.deepcopy(state["active_runs"].get(job_id) or active)
            done = core.finalizable(active)
            if not done:
                continue
            if not core.gpu_contexts_gone(active):
                with store.tx() as state:
                    already_pending = bool((state["active_runs"].get(job_id) or {}).get("cleanup_pending"))
                if not already_pending:
                    with store.tx("gpu_cleanup_pending") as state:
                        current = state["active_runs"].get(job_id)
                        if current and current.get("generation") == active["generation"]:
                            current["cleanup_pending"] = "GPU context or unattributed memory remains after process exit"
                            core.get_job(state, job_id)["status"] = "STOPPING"
                continue
            with store.tx("stage_finalized") as state:
                current = state["active_runs"].get(job_id)
                if not current or current.get("generation") != active["generation"]:
                    continue
                job = core.get_job(state, job_id)
                core.invalidate_observations(active["gpus"], state["policy"], state["daemon"], "managed stage finalized")
                job["gpu_release_verified_at"] = core.stamp()
                if current.get("cancel_requested"):
                    request = current["cancel_requested"]
                    job["status"] = request["terminal"]
                    job["terminal_reason"] = request["reason"]
                    job["guardian_result"] = active["guardian_result"]
                    job["automatic_retry"] = False
                    state["active_runs"].pop(job_id)
                elif done["leader_exit_code"] != 0:
                    job["status"] = "FAILED"
                    job["error"] = "adapter exit " + str(done["leader_exit_code"])
                    job["automatic_retry"] = False
                    state["active_runs"].pop(job_id)
                else:
                    try:
                        core.complete_stage(state, active, core.receipt_verified(job, active))
                    except Exception as error:
                        job["status"] = "FAILED"
                        job["error"] = str(error)
                        job["automatic_retry"] = False
                        state["active_runs"].pop(job_id, None)
            if idle is not None:
                core.release_used_devices(idle, active["gpus"])
        except Exception as error:
            with store.tx("recovery_blocked") as state:
                if job_id in state["active_runs"]:
                    core.get_job(state, job_id)["status"] = "STOPPING"
                    state["recovery"] = {"job_id": job_id, "reason": str(error), "at": core.stamp()}


def summary(state):
    result = _original_summary(state)
    result["controller_revision"] = REVISION
    for active in result["active"]:
        active["cleanup_pending"] = state["active_runs"][active["job_id"]].get("cleanup_pending")
    return result


def cli():
    if len(sys.argv) > 2 and sys.argv[1] == "experiment" and sys.argv[2] in ("pause", "resume"):
        raise ValueError("Experiment pause/resume removed: use stop JOB_ID for started work, remove JOB_ID for never-started work")
    return _original_cli()


def install():
    from ugs_manifest_validation_v1 import install as install_manifest_validation
    install_manifest_validation()
    if getattr(core, "_ugs_control_installed", False):
        return
    core.scoped = scoped
    core.stop_owned = stop_owned
    core.gpu_contexts_gone = gpu_contexts_gone
    core.detect_lost_leaders = detect_lost_leaders
    core.finalize_available = finalize_available
    core.summary = summary
    core.cli = cli
    core.SELF = Path(__file__).resolve()
    core._ugs_control_installed = True


if __name__ == "__main__":
    install()
    try:
        cli()
    except Exception as error:
        print(json.dumps({"error": type(error).__name__, "message": str(error)}), file=sys.stderr)
        sys.exit(2)
