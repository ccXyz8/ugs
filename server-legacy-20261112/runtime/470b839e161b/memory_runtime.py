"""Shared memory policy for scheduler, preload verifier and cleanup."""
import copy,json
from pathlib import Path
import ugs_exec_v3_1 as core
def memory_matches(card, baseline):
    if not isinstance(card, dict) or not isinstance(baseline, dict):
        return False
    used = card.get('memory_mib')
    floor = baseline.get('memory_mib')
    ceiling = baseline.get('memory_ceiling_mib', floor)
    if any(type(x) is not int for x in (used, floor, ceiling)):
        return False
    if baseline.get('memory_allowance_disabled') is True:
        return type(floor) is int and floor == 4 and type(used) is int and used == 4
    if 'memory_ceiling_mib' in baseline:
        # The user's exception concerns the reviewed 4 MiB baseline only.
        if floor != 4 or not 4 <= ceiling <= 7:
            return False
    return floor <= used <= ceiling


def fallback_candidates(policy, cards, allocated):
    """Called only after guardian/process-tree exit verification by controller."""
    result = []
    baselines = policy.get('baseline', {})
    for card in cards:
        baseline = baselines.get(card.get('uuid'), {})
        used = card.get('memory_mib')
        if (card.get('uuid') in allocated and card.get('processes') == []
                and baseline.get('memory_mib') == 4
                and baseline.get('memory_ceiling_mib') == 7
                and baseline.get('memory_allowance_disabled') is not True
                and type(used) is int and used > 7):
            result.append({'uuid':card['uuid'], 'memory_mib':used})
    return result


def latch_fallback(policy, triggers, context):
    """Mutate within the existing state transaction; never relearn a baseline."""
    if not triggers:
        return False
    changed = []
    for uuid, baseline in policy.get('baseline', {}).items():
        if baseline.get('memory_mib') == 4:
            baseline['memory_ceiling_mib'] = 4
            baseline['memory_allowance_disabled'] = True
            changed.append(uuid)
    policy['memory_allowance_fallback'] = {
        'latched': True, 'reason':'cleanup_memory_above_7_mib',
        'triggers':triggers, 'affected_uuids':changed, **context,
    }
    return True


def card_reason(c,b,baseline_ok):
    if not c or c.get("health_ok") is not True:return "HEALTH_UNKNOWN_OR_BAD"
    if type(c.get("ecc_uncorrectable")) is not int or c["ecc_uncorrectable"]!=0:return "ECC_UNKNOWN_OR_BAD"
    if not isinstance(c.get("processes"),list):return "PROCESS_VISIBILITY_UNKNOWN"
    if c["processes"]:return "PROCESS_PRESENT"
    for key in ("util","memory_util"):
        if type(c.get(key)) is not int or c[key]!=0:return "UTILIZATION_UNKNOWN_OR_BUSY"
    if not baseline_ok:return "BASELINE_UNREVIEWED"
    if c.get("driver_version")!=b["driver_version"]:return "DRIVER_CHANGED"
    if not memory_matches(c,b):return "MEMORY_NOT_BASELINE"
    if c.get("memory_reserved_mib")!=b["memory_reserved_mib"] or c.get("memory_total_mib")!=b["memory_total_mib"]:
        return "MEMORY_LAYOUT_CHANGED"
    if not isinstance(c.get("name"),str) or "A100" not in c["name"]:return "GPU_TYPE_UNKNOWN"
    return None

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
                and core.baseline_valid(policy.get("baseline", {}).get(card["uuid"]))
                and card.get("driver_version") == policy["baseline"][card["uuid"]].get("driver_version")]
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

def install():
    core.memory_matches=memory_matches
    core.card_reason=card_reason
    core.gpu_contexts_gone=gpu_contexts_gone
