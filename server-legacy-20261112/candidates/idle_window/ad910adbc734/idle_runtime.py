"""Shared idle-window policy; old pinned stages keep their stricter contract."""
import json
from pathlib import Path

REVISION = 'ugs-idle-window-v1'
HERE = Path(__file__).resolve().parent


def parameters(policy):
    seconds = policy.get('idle_seconds', 300)
    if type(seconds) is not int or seconds not in (120, 300):
        raise ValueError('Unsupported idle window; expected 120 or 300 seconds')
    if policy.get('scan_seconds', 30) != 30 or policy.get('max_sample_gap_seconds', 35) != 35:
        raise ValueError('Scan cadence/gap must remain 30/35 seconds')
    return seconds, seconds // 30 + 1


def install(core):
    if getattr(core, '_idle_window_v1', False):
        return
    original_state = core.idle_state

    def idle_state(records, policy, now, daemon):
        seconds, count = parameters(policy)
        # Preserve the audited parser, process/health/baseline checks and all resets.
        details, latest = original_state(records, policy, now, daemon)
        for item in details.values():
            item['eligible'] = (item['since'] is not None and
                                item['idle_seconds'] >= seconds and item['count'] >= count)
            if item['since'] is not None:
                item['reason'] = 'QUALIFIED' if item['eligible'] else 'WARMING'
        return details, latest

    def sync(self, records, policy, daemon, now):
        self.window, self.minimum_samples = parameters(policy)
        self.details, self.last = idle_state(records, policy, now, daemon)
        self.since = {g:x['since'] for g,x in self.details.items() if x['since'] is not None}
        self.counts = {g:x['count'] for g,x in self.details.items()}

    def candidates(self, now, window=None):
        window = getattr(self, 'window', 300) if window is None else window
        if type(window) is not int or window not in (0, 120, 300):
            raise ValueError('Invalid candidate window')
        if self.last is None or now - self.last > 35 or now < self.last:
            return []
        count = window // 30 + 1 if window else 0
        return sorted((g for g,start in self.since.items()
                       if now-start >= window and (not window or self.counts.get(g,0) >= count)),
                      key=lambda g:(self.since[g],g))

    def stage_seconds(job, policy):
        seconds, _ = parameters(policy)
        if seconds == 300:
            return seconds
        stage = core.project_stage(job)
        pin = job['manifest'].get('admission', {}).get('stages', {}).get(stage['stage_id'], {}).get('cpu', {})
        # Never select a 120s GPU for a frozen verifier which still requires 300s.
        path = str(HERE / 'idle_runtime.py')
        try:
            if core.hash_file(pin['path']) != pin['sha256']:
                return 300
            report = json.loads(Path(pin['path']).read_text())
            if report.get('source_sha256', {}).get(path) != core.hash_file(path):
                return 300
            verifier = report.get('preflight', {}).get('verifier', {})
            if verifier.get('path') != str(HERE / 'verify_launch_idle.py'):
                return 300
            if verifier.get('sha256') != core.hash_file(verifier['path']):
                return 300
        except (OSError, KeyError, ValueError, TypeError):
            return 300
        return seconds

    def select_profile(job, state, idle, age=True):
        if job.get('paused') or state['projects'].get(job['project_id'], {}).get('paused') or not core.accepted_stage(job):
            return None
        used = {g for jid,a in state['active_runs'].items() if jid != job['job_id'] for g in a.get('gpus', [])}
        window = stage_seconds(job, state['policy']) if age else 0
        pool = [g for g in idle.candidates(core.time.monotonic(), window)
                if g not in used and g not in state.get('gpu_reservations', {})]
        for profile in core.project_stage(job)['resource_profiles']:
            if len(pool) >= profile['gpu_count'] and core.budgets_ready(job,state,profile,exclude=job['job_id']):
                return profile, pool[:profile['gpu_count']]
        return None

    core.idle_state = idle_state
    core.Idle.sync = sync
    core.Idle.candidates = candidates
    core.select_profile = select_profile
    core.stage_idle_seconds = stage_seconds
    core._idle_window_v1 = True
