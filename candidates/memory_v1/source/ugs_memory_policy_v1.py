"""Explicit idle-FB allowance; never substitutes for process/health checks."""

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
