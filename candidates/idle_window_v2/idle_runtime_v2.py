"""Current-state admission; legacy time windows only for staged migration."""
import json
import math
from pathlib import Path

REVISION = 'ugs-idle-window-v2'
HERE = Path(__file__).resolve().parent
OLD = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/idle_window/ad910adbc734')


def parameters(policy):
    values = (policy.get('idle_seconds', 300), policy.get('scan_seconds', 30),
              policy.get('max_sample_gap_seconds', 35))
    if any(type(x) is not int for x in values) or values not in ((0, 30, 35), (60, 15, 20), (120, 30, 35), (300, 30, 35)):
        raise ValueError('Unsupported idle/scan/gap policy; accepted: 0/30/35, 60/15/20, 120/30/35, 300/30/35')
    seconds, scan, gap = values
    return seconds, scan, gap, seconds // scan + 1


def contract(core, job):
    stage = core.project_stage(job)
    pin = job['manifest'].get('admission', {}).get('stages', {}).get(stage['stage_id'], {}).get('cpu', {})
    try:
        if not core.pin_ok(pin): return 'unknown'
        report = json.loads(Path(pin['path']).read_text())
        verifier = report.get('preflight', {}).get('verifier', {})
        sources = report.get('source_sha256', {})
        for folder, helper, verifier_name, kind in (
                (HERE, 'idle_runtime_v2.py', 'verify_launch_idle_v2.py', 'v2'),
                (OLD, 'idle_runtime.py', 'verify_launch_idle.py', 'v1')):
            h, v = str(folder / helper), str(folder / verifier_name)
            if (verifier.get('path') == v and verifier.get('sha256') == core.hash_file(v)
                    and sources.get(v) == core.hash_file(v) and sources.get(h) == core.hash_file(h)):
                return kind
    except (OSError, KeyError, ValueError, TypeError):
        return 'unknown'
    return 'legacy300'


def stage_policy(core, job, policy):
    seconds, scan, gap, count = parameters(policy)
    kind = contract(core, job)
    compatible = seconds not in (0, 60) or kind == 'v2'
    required = seconds if kind == 'v2' else 120 if kind == 'v1' else 300
    if seconds == 300: required = 300
    return {'contract': kind, 'mode': 'current_state' if seconds == 0 else 'continuous_window', 'idle_seconds': required, 'sampling_seconds': scan,
            'max_sample_gap_seconds': gap, 'minimum_samples': required // scan + 1,
            'compatible': compatible, 'reason': None if compatible else 'VERIFIER_MIGRATION_REQUIRED'}


def idle_state(core, records, policy, now, daemon):
    # Same audited schema, identity, health, baseline and invalidation checks as
    # frozen v3.1; only the window/count and maximum sampling gap are parameterized.
    seconds, scan, gap, minimum = parameters(policy)
    inventory = policy.get('gpu_inventory') or []
    result = {g: {'since': None, 'count': 0, 'reason': 'NO_CURRENT_OBSERVATIONS'} for g in inventory}
    valid_baselines = {g: core.baseline_valid(policy.get('baseline', {}).get(g)) for g in inventory}
    previous = latest_sample = None
    signature = core.observation_policy(policy)
    def reset(reason, gpus=None):
        for g in inventory if gpus is None else gpus:
            if g in result: result[g] = {'since': None, 'count': 0, 'reason': reason}
    for row in records:
        if row.get('boot') != core.BOOT or row.get('daemon_pid') != daemon['identity']['pid']: continue
        if row.get('daemon_start') is None: raise ValueError('Observation missing coordinator start identity')
        if row.get('daemon_start') != daemon['identity']['start']: continue
        if row.get('version') != core.VERSION: raise ValueError('Unknown observation schema for current coordinator')
        end = row.get('monotonic')
        if type(end) not in (int, float) or not math.isfinite(end) or end < daemon['monotonic']:
            raise ValueError('Unknown/invalid observation timestamp')
        if end > now or (previous is not None and end <= previous):
            raise ValueError('Observation timestamps are not strictly ordered/current')
        previous = end
        if 'invalidated_gpus' in row:
            reset('INVALIDATED', row['invalidated_gpus']); continue
        begin = row.get('started_monotonic')
        if (row.get('valid') is not True or type(begin) not in (int, float) or not math.isfinite(begin)
                or begin > end or end - begin > 5 or row.get('policy_sha256') != signature):
            reset('OBSERVATION_UNKNOWN_OR_FAILED'); latest_sample = None; continue
        cards = row.get('cards') or []
        byuuid = {c.get('uuid'): c for c in cards}
        if len(cards) != 8 or len(byuuid) != 8 or set(byuuid) != set(inventory):
            reset('INVENTORY_UNKNOWN'); latest_sample = None; continue
        if latest_sample is not None and end - latest_sample > gap: reset('OBSERVATION_GAP')
        latest_sample = end
        for g in inventory:
            reason = core.card_reason(byuuid[g], policy.get('baseline', {}).get(g), valid_baselines[g])
            if reason: reset(reason, [g])
            else:
                item = result[g]
                if seconds == 0:
                    item['since'], item['count'] = end, 0
                if item['since'] is None: item['since'], item['count'] = end, 0
                item['count'] += 1; item['reason'] = 'WARMING'
    if latest_sample is None or now - latest_sample > gap: reset('OBSERVATION_STALE')
    for item in result.values():
        item['idle_seconds'] = max(0, now - item['since']) if item['since'] is not None else 0
        item['eligible'] = item['since'] is not None and item['idle_seconds'] >= seconds and item['count'] >= minimum
        if item['eligible']: item['reason'] = 'QUALIFIED'
    return result, latest_sample


def install(core):
    if getattr(core, '_idle_window_v2', False): return
    old_select, old_launch = core.select_profile, core.launch_valid
    core.idle_state = lambda records, policy, now, daemon: idle_state(core, records, policy, now, daemon)
    def sync(self, records, policy, daemon, now):
        self.window, self.scan, self.gap, self.minimum_samples = parameters(policy)
        self.details, self.last = core.idle_state(records, policy, now, daemon)
        self.since = {g: x['since'] for g, x in self.details.items() if x['since'] is not None}
        self.counts = {g: x['count'] for g, x in self.details.items()}
    def candidates(self, now, window=None):
        window = self.window if window is None else window
        if type(window) is not int or window not in (0, 60, 120, 300): raise ValueError('Invalid candidate window')
        if self.last is None or now < self.last or now - self.last > self.gap: return []
        count = window // self.scan + 1 if window else 0
        return sorted((g for g, start in self.since.items() if now - start >= window
                       and (not window or self.counts.get(g, 0) >= count)), key=lambda g: (self.since[g], g))
    def select(job, state, idle, age=True):
        if not stage_policy(core, job, state['policy'])['compatible']: return None
        return old_select(job, state, idle, age)
    def launch(state, job, sha, gpus):
        return stage_policy(core, job, state['policy'])['compatible'] and old_launch(state, job, sha, gpus)
    core.Idle.sync, core.Idle.candidates = sync, candidates
    core.stage_idle_seconds = lambda job, policy: stage_policy(core, job, policy)['idle_seconds']
    core.stage_idle_policy = lambda job, policy: stage_policy(core, job, policy)
    core.idle_parameters = parameters
    core.select_profile, core.launch_valid = select, launch
    core._idle_window_v2 = True
