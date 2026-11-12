"""Fresh launch snapshots after slow validation; immutable v3.1 stays pinned."""
import copy
import fcntl
import os
import subprocess

REVISION = 'ugs-dispatch-v1'


class DispatchError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Unchanged(Exception):
    """Abort an otherwise no-op state transaction without journal growth."""


def blocker_view(job):
    b = job.get('dispatch_blocker')
    if (not b or job.get('status') not in ('QUEUED', 'WAITING_RESOURCES', 'WAITING_CONTINUATION', 'BLOCKED')
            or b['manifest_sha256'] != job['manifest_sha256'] or b['run_id'] != job['run_id']
            or b['stage_index'] != job['stage_index']):
        return {}
    return {'failure_category': 'DISPATCH_BLOCKED', 'failure_phase': b['phase'],
            'failure_reason': b['code'] + ': ' + b['message'],
            'failure_message': b['message'], 'dispatch_blocker': b}


def report_issue(c, selected, attempt, exc):
    """Keep one durable current reason; identical failed admissions add no state."""
    b = {k: selected[k] for k in ('job_id', 'run_id', 'manifest_sha256', 'stage_index')}
    b.update(at=c.stamp(), code=getattr(exc, 'code', 'DISPATCH_CHECK_ERROR'),
             phase=attempt.get('phase', 'unknown'), message=str(exc)[:2048],
             gpus=list(attempt.get('gpus', [])), timings=attempt.get('timings', {}))
    fingerprint = c.digest(c.canonical({k: b[k] for k in
        ('job_id', 'run_id', 'manifest_sha256', 'stage_index', 'code', 'phase', 'message', 'gpus')}))
    b['fingerprint'] = fingerprint
    try:
        with c.Store().tx('dispatch_blocked') as s:
            j = c.get_job(s, selected['job_id'])
            if attempt.get('spawn_attempted'):
                # Popen or the intent commit may fail after a process was created.
                # Never admit a second attempt under an ambiguous launch.
                if attempt.get('active'):
                    s['active_runs'].setdefault(j['job_id'], copy.deepcopy(attempt['active']))
                    j['status'] = 'STARTING'
                s['recovery'] = {'job_id': j['job_id'], 'reason': 'ambiguous launch: ' + b['message'],
                                 'at': b['at'], 'dispatch_revision': REVISION}
            else:
                if (j['run_id'] != b['run_id'] or j['manifest_sha256'] != b['manifest_sha256']
                        or j['stage_index'] != b['stage_index'] or j['status'] in c.TERMINAL
                        or (j['job_id'] in s['active_runs'] and not s['active_runs'][j['job_id']].get('continuation'))):
                    raise Unchanged()
                if j.get('dispatch_blocker', {}).get('fingerprint') == fingerprint:
                    raise Unchanged()
                if b['code'] == 'SOURCE_PREFLIGHT_FAILED':
                    j['status'] = 'BLOCKED'
                    j['error'] = b['message']
                    s['active_runs'].pop(j['job_id'], None)
            j['dispatch_blocker'] = b
        c.append_json(c.ROOT / 'service-events.jsonl', {'event': 'dispatch_blocked', **b})
        return True
    except Unchanged:
        return False


def dispatch(c, selected, chosen_state, profile, gpus, children, attempt):
    """No replay or file hashing after the final GPU snapshot."""
    attempt.update(phase='state_lock_and_replay', gpus=list(gpus), timings={})
    started = c.time.monotonic()
    with c.Store().tx('launch_intent') as s:
        attempt['timings']['state_lock_and_replay'] = c.time.monotonic() - started
        j = c.get_job(s, selected['job_id'])
        attempt['phase'] = 'identity_controls_and_budget'
        if not c.launch_valid(s, j, selected['manifest_sha256'], gpus):
            raise DispatchError('LAUNCH_PERMISSION_CHANGED', 'Job identity, controls or resource ownership changed')
        # Also bind the exact selected profile, including UUID restrictions.
        if profile not in c.project_stage(j)['resource_profiles']:
            raise DispatchError('RESOURCE_PROFILE_CHANGED', 'Selected resource profile changed')
        if not c.budgets_ready(j, s, profile, exclude=j['job_id']):
            raise DispatchError('HOST_STORAGE_BUDGET', 'Host memory or project storage budget unavailable')
        attempt['phase'] = 'source_preflight'
        started = c.time.monotonic()
        try:
            c.source_preflight(j)
        except Exception as exc:
            raise DispatchError('SOURCE_PREFLIGHT_FAILED', str(exc)) from exc
        attempt['timings']['source_preflight'] = c.time.monotonic() - started
        # The state lock spans both observations, preventing pause/update/reserve
        # from racing the decision. Existing worker/guardian processes continue.
        before = []
        for n in range(2):
            if n:
                c.time.sleep(2)
            attempt['phase'] = 'first_gpu_recheck' if not n else 'final_gpu_recheck'
            before.append(c.observe(s['policy'], s['daemon'], 'launch_recheck'))
            attempt['phase'] = 'first_idle_window' if not n else 'final_idle_window'
            c.require_idle_window(c.observed_records(), gpus, s['policy'], c.time.monotonic(), s['daemon'])
        age = c.time.monotonic() - before[-1]['monotonic']
        attempt['timings']['final_snapshot_age'] = age
        attempt['phase'] = 'final_snapshot_freshness'
        if age < 0 or age > 2:
            raise DispatchError('GPU_SNAPSHOT_STALE', 'Launch GPU recheck became stale; limit 2s')
        if j['job_id'] not in s['active_runs']:
            s['round'] = chosen_state['round']
            s['served'] = copy.deepcopy(chosen_state['served'])
            c.mark_served(s, j['project_id'])
        t = c.project_stage(j)
        a = {'job_id': j['job_id'], 'run_id': j['run_id'], 'stage_id': t['stage_id'],
             'manifest_sha256': j['manifest_sha256'], 'stage_nonce': c.uuid.uuid4().hex,
             'generation': c.uuid.uuid4().hex, 'boot': c.BOOT, 'gpus': list(gpus),
             'profile': copy.deepcopy(profile), 'process': None, 'guardian': None,
             'grant_deadline': c.time.monotonic() + 15, 'prepare_deadline': c.time.monotonic() + 900,
             'checks': before, 'intent_at': c.stamp(), 'dispatch_revision': REVISION,
             'dispatch_timings': copy.deepcopy(attempt['timings'])}
        if j.get('dispatch_blocker'):
            j['last_dispatch_blocker'] = j.pop('dispatch_blocker')
        s['active_runs'][j['job_id']] = a
        j['status'] = 'STARTING'
        j.setdefault('first_started_at', c.stamp())
        attempt['phase'] = 'guardian_spawn_and_commit'
        attempt['active'] = a
        with (c.ROOT / 'service.log').open('ab', buffering=0) as log:
            attempt['spawn_attempted'] = True
            child = c.subprocess.Popen([c.PYTHON, '-B', str(c.SELF), 'internal', 'guardian', '--nonce', a['stage_nonce']],
                                      stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        children[child.pid] = child
        a['guardian_spawn'] = c.identity(child.pid)
        if not a['guardian_spawn']:
            raise ValueError('guardian identity missing')
    return a


def serve(c):
    c.node_check()
    store = c.Store()
    with (c.ROOT / 'daemon.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with store.tx('executor_start') as s:
            c.require_v3(s)
            if not c.release_ok(s):
                raise ValueError('release not accepted')
            if s['active_runs'] and not s.pop('handoff_ready', False):
                s['recovery'] = {'reason': 'Unplanned coordinator restart: verify existing identities; no relaunch'}
            s['shutdown_requested'] = False
            s['daemon'] = {'identity': c.identity(os.getpid()), 'version': c.VERSION, 'started': c.stamp(),
                           'monotonic': c.time.monotonic(), 'dispatch_revision': REVISION}
        idle = c.Idle()
        next_scan = 0
        children = {}
        while True:
            with store.tx() as current:
                shutdown = current.get('shutdown_requested') and not current['active_runs']
            if shutdown:
                with store.tx('coordinator_stopped') as current:
                    current['daemon']['stopped_at'] = c.stamp()
                return
            now = c.time.monotonic()
            if now >= next_scan:
                with store.tx() as s:
                    policy, daemon = copy.deepcopy(s['policy']), copy.deepcopy(s['daemon'])
                try:
                    c.observe(policy, daemon, 'periodic')
                except Exception as exc:
                    idle.reset()
                    c.append_json(c.ROOT / 'service-events.jsonl', {'at': c.stamp(), 'event': 'scan_failed', 'error': str(exc)[:2048]})
                next_scan = now + 30
            for pid, child in list(children.items()):
                if child.poll() is not None:
                    del children[pid]
            c.detect_lost_leaders()
            c.finalize_available(idle)
            with store.tx() as s:
                s = copy.deepcopy(s)
            try:
                idle.sync(c.observed_records(), s['policy'], s['daemon'], c.time.monotonic())
            except Exception as exc:
                idle.reset()
                c.append_json(c.ROOT / 'service-events.jsonl', {'at': c.stamp(), 'event': 'observation_history_rejected', 'error': str(exc)[:2048]})
            if not c.eligible(s):
                c.time.sleep(.5)
                continue
            selected = None
            chosen = copy.deepcopy(s)
            for jid, active in s['active_runs'].items():
                if not active.get('continuation'):
                    continue
                j = c.get_job(chosen, jid)
                if j.get('paused') or not c.select_profile(j, chosen, idle, False):
                    with store.tx('continuation_yield') as z:
                        if z['active_runs'].get(jid, {}).get('continuation'):
                            z['active_runs'].pop(jid)
                            c.get_job(z, jid)['status'] = 'WAITING_CONTINUATION'
                    continue
                if c.select_profile(j, chosen, idle):
                    selected = j
                    break
            if selected is None and c.slot_available(chosen):
                selected = c.choose(chosen, lambda j: j['status'] in ('QUEUED', 'WAITING_RESOURCES', 'WAITING_CONTINUATION')
                                    and bool(c.select_profile(j, chosen, idle)))
            found = c.select_profile(selected, chosen, idle) if selected else None
            if not found:
                c.time.sleep(.5)
                continue
            attempt = {}
            try:
                dispatch(c, selected, chosen, found[0], found[1], children, attempt)
            except Exception as exc:
                report_issue(c, selected, attempt, exc)
                idle.reset()
            c.time.sleep(.5)


def install(c, cli):
    if getattr(c, '_dispatch_v1', False):
        return
    c.serve = lambda: serve(c)
    original = cli.preflight.failure_view
    cli.preflight.failure_view = lambda job: {**original(job), **blocker_view(job)}
    c._dispatch_v1 = True
