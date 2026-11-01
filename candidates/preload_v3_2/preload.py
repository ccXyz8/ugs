"""Review candidate: acquire final state lock before the last GPU observation.
No production installation on import. The injected core is the frozen v3.1 core.
"""
import copy
import os
import time

REVISION = 'ugs-preload-v3.2-candidate1'


def verify(core, env=None, caller=None, clock=time):
    env = dict(os.environ) if env is None else env
    caller = os.getppid() if caller is None else caller
    timings = []
    fresh = []
    result = {}
    reason = None
    started = clock.monotonic()

    def timed(name, fn):
        before = clock.monotonic()
        try:
            return fn()
        finally:
            timings.append({'phase': name, 'seconds': clock.monotonic() - before})

    def window(state, gpus):
        records = timed('read_observation_history', core.observed_records)
        timed('verify_idle_window', lambda: core.require_idle_window(
            records, gpus, state['policy'], clock.monotonic(), state['daemon']))

    def check(state, original):
        active = core.active_for(state, env)
        if not active or active.get('generation') != original['generation']:
            raise ValueError('Generation changed or permit revoked')
        if active.get('preload_authorized_at'):
            raise ValueError('Preload already consumed')
        if active['gpus'] != original['gpus']:
            raise ValueError('GPU allocation changed')
        if any(g in state.get('gpu_reservations', {}) for g in active['gpus']):
            raise ValueError('GPU reserved')
        if any(set(active['gpus']).intersection(other.get('gpus', []))
               for jid, other in state['active_runs'].items() if jid != active['job_id']):
            raise ValueError('Overlapping managed allocation')
        view = copy.deepcopy(state)
        core.active_for(view, env)['grant_deadline'] = active['prepare_deadline']
        code, permit = timed('identity_release_permission', lambda: core.check_permission(
            view, env, caller, clock.monotonic()))
        if code:
            raise ValueError(permit.get('reason', 'Permission denied'))
        job = core.get_job(state, active['job_id'])
        timed('source_preflight', lambda: core.source_preflight(job))
        if not timed('host_storage_budget', lambda: core.budgets_ready(
                job, state, active['profile'], exclude=active['job_id'])):
            raise ValueError('Host/storage budget changed')
        return active, permit

    def age_check():
        if clock.monotonic() - fresh[-1]['monotonic'] > 2:
            raise ValueError('Final GPU recheck became stale')

    try:
        core.node_check()
        store = core.Store()
        before = clock.monotonic()
        with store.tx() as state:
            timings.append({'phase': 'initial_state_lock_and_replay', 'seconds': clock.monotonic()-before})
            core.require_v3(state)
            active = core.active_for(state, env)
            if not active or not active.get('authorized_at'):
                raise ValueError('Initial authorization required')
            original = copy.deepcopy(active)
            check(state, original)
            snapshot = copy.deepcopy(state)
        window(snapshot, original['gpus'])
        fresh.append(timed('first_gpu_scan', lambda: core.observe(snapshot['policy'], snapshot['daemon'], 'preload_candidate')))
        window(snapshot, original['gpus'])
        timed('scan_spacing', lambda: clock.sleep(2))
        # Unlike v3.1, lock contention and journal replay happen BEFORE scan 2.
        before = clock.monotonic()
        with store.tx('preload_authorized') as state:
            timings.append({'phase': 'final_state_lock_and_replay', 'seconds': clock.monotonic()-before})
            current, result = check(state, original)
            window(state, original['gpus'])
            fresh.append(timed('final_gpu_scan', lambda: core.observe(state['policy'], state['daemon'], 'preload_candidate')))
            window(state, original['gpus'])
            # Keep final identity/revocation/UUID/PIN/budget checks inside the lock.
            current, result = check(state, original)
            age_check()
            current['preload_checks'] = copy.deepcopy(fresh)
            current['preload_authorized_at'] = core.stamp()
            current['preload_authorized_monotonic'] = clock.monotonic()
            current['preload_candidate_revision'] = REVISION
            commit_started = clock.monotonic()
        timings.append({'phase': 'state_commit_and_unlock', 'seconds': clock.monotonic()-commit_started})
        # A slow journal commit must not turn an old snapshot into an allowed reply.
        age_check()
    except Exception as exc:
        reason = str(exc)

    audit = {'at': core.stamp(), 'event': 'preload_candidate_decision',
             'candidate_revision': REVISION, 'job_id': env.get('UGS_JOB_ID'),
             'generation': env.get('UGS_GENERATION'), 'allowed_before_audit': reason is None,
             'reason': reason, 'phases': timings,
             'last_observation_id': fresh[-1].get('observation_id') if fresh else None,
             'last_sample_age_seconds': clock.monotonic()-fresh[-1]['monotonic'] if fresh else None,
             'total_seconds_before_audit': clock.monotonic()-started}
    audit_start = clock.monotonic()
    try:
        core.append_json(core.ROOT/'service-events.jsonl', audit)
    except Exception as exc:
        reason = 'Decision audit write failed: ' + str(exc)
    audit_seconds = clock.monotonic()-audit_start
    if reason is None:
        try:
            age_check()
        except Exception as exc:
            reason = str(exc) + ' after decision audit'
    # Response timing is also available to the adapter even if audit fsync is slow.
    details = {'candidate_revision': REVISION, 'timings': timings,
               'audit_seconds': audit_seconds,
               'final_sample_age_seconds': clock.monotonic()-fresh[-1]['monotonic'] if fresh else None}
    if reason is not None:
        return 2, {'allowed': False, 'reason': 'PRELOAD_RECHECK_FAILED',
                   'message': reason, 'verifier': core.VERSION, **details}
    return 0, {**result, 'allowed': True, 'preload': True,
               'observed_at': fresh[-1]['at'], **details}
