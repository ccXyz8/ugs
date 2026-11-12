import contextlib
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
import uuid

import dispatch_cadence as d


class Clock:
    def __init__(self): self.now = 1000.
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.clock = Clock()
        self.profile = {'gpu_count': 2, 'gpu_uuids': ['GPU-5', 'GPU-7']}
        self.job = {'job_id': 'job-93', 'run_id': 'run93', 'project_id': 'ms',
                    'manifest_sha256': 'sha', 'stage_index': 0, 'status': 'QUEUED',
                    'queue_seq': 93, 'manifest': {'stages': [{'stage_id': 'eval', 'resource_profiles': [self.profile]}]}}
        self.state = {'jobs': [copy.deepcopy(self.job)], 'active_runs': {}, 'policy': {'scheduler_paused': False},
                      'daemon': {}, 'round': 5, 'served': [], 'ring': ['ms', 'other'], 'recovery': None}
        self.events = []
        self.audit = []
        self.order = []
        self.scans = []
        self.spawns = []
        self.replay_delay = 5
        self.source_delay = 7
        self.history_delay = .1
        self.commit_fail = False
        self.attempt = {}
        c = types.SimpleNamespace(ROOT=Path(self.temp.name), time=self.clock, uuid=uuid, BOOT='boot',
                                  PYTHON='python', SELF='entry', TERMINAL={'FAILED', 'CANCELLED', 'SUCCEEDED', 'WITHDRAWN'})
        self.c = c
        c.hash_scope=contextlib.nullcontext
        @contextlib.contextmanager
        def tx(event=None):
            self.clock.sleep(self.replay_delay)
            trial = copy.deepcopy(self.state)
            yield trial
            if event:
                if self.commit_fail and event == 'launch_intent': raise OSError('commit failed')
                self.state = trial
                self.events.append(event)
        c.Store = lambda: types.SimpleNamespace(tx=tx)
        c.get_job = lambda s, jid: next(j for j in s['jobs'] if j['job_id'] == jid)
        c.project_stage = lambda j: j['manifest']['stages'][j['stage_index']]
        c.launch_valid = lambda s, j, sha, g: (not s['policy']['scheduler_paused'] and j['manifest_sha256'] == sha
            and not s['active_runs'] and not set(g).intersection(s.get('reservations', [])))
        c.budgets_ready = lambda *a, **k: True
        def source(j):
            self.order.append('source'); self.clock.sleep(self.source_delay)
        c.source_preflight = source
        def observe(*args):
            self.order.append('scan'); self.clock.sleep(.9)
            record = {'monotonic': self.clock.monotonic(), 'valid': True}
            self.scans.append(record)
            return record
        c.observe = observe
        c.observed_records = lambda: self.scans
        def window(*args):
            self.clock.sleep(self.history_delay)
        c.require_idle_window = window
        c.mark_served = lambda s, p: s['served'].append(p)
        c.stamp = lambda: 'fixture-time'
        def spawn(*args, **kwargs):
            self.spawns.append(args)
            return types.SimpleNamespace(pid=1234)
        c.subprocess = types.SimpleNamespace(Popen=spawn)
        c.identity = lambda pid: {'pid': pid, 'start': '1', 'boot': 'boot'}
        c.canonical = lambda x: json.dumps(x, sort_keys=True).encode()
        c.digest = lambda x: hashlib.sha256(x).hexdigest()
        c.append_json = lambda p, row: self.audit.append(row)

    def call(self):
        return d.dispatch(self.c, self.job, copy.deepcopy(self.state), self.profile,
                          ['GPU-5', 'GPU-7'], {}, self.attempt)

    def failed(self):
        try: self.call()
        except Exception as exc:
            d.report_issue(self.c, self.job, self.attempt, exc)
            return exc
        self.fail('Expected launch rejection')

    def test_slow_replay_and_hashes_before_final_scan_allow_launch(self):
        active = self.call()
        self.assertEqual(self.order, ['source', 'scan', 'scan'])
        self.assertGreaterEqual(self.scans[1]['monotonic'] - self.scans[0]['monotonic'], 2)
        self.assertLessEqual(active['dispatch_timings']['final_snapshot_age'], 2)
        self.assertEqual(self.state['jobs'][0]['status'], 'STARTING')
        self.assertEqual(len(self.spawns), 1)
        for k in ('job_id', 'run_id', 'manifest_sha256', 'queue_seq', 'manifest'):
            self.assertEqual(self.state['jobs'][0][k], self.job[k])

    def test_final_snapshot_still_rejects_age_over_two(self):
        self.history_delay = 2.01
        exc = self.failed()
        self.assertEqual(exc.code, 'GPU_SNAPSHOT_STALE')
        self.assertFalse(self.spawns)
        self.assertEqual(self.state['jobs'][0]['status'], 'QUEUED')
        self.assertEqual(d.blocker_view(self.state['jobs'][0])['failure_category'], 'DISPATCH_BLOCKED')

    def test_identical_failure_deduplicates_full_state_and_audit(self):
        self.history_delay = 3
        self.failed()
        self.history_delay = 4  # variable durations must not defeat deduplication
        self.failed()
        self.assertEqual(self.events, ['dispatch_blocked'])
        self.assertEqual(len(self.audit), 1)

    def test_pause_race_rejected_before_scans(self):
        self.state['policy']['scheduler_paused'] = True
        self.assertEqual(self.failed().code, 'LAUNCH_PERMISSION_CHANGED')
        self.assertFalse(self.scans or self.spawns)

    def test_reserved_gpu_rejected(self):
        self.state['reservations'] = ['GPU-7']
        self.assertEqual(self.failed().code, 'LAUNCH_PERMISSION_CHANGED')
        self.assertFalse(self.spawns)

    def test_concurrent_allocation_rejected(self):
        self.state['active_runs']['other'] = {'gpus': ['GPU-5']}
        self.failed()
        self.assertEqual(list(self.state['active_runs']), ['other'])
        self.assertFalse(self.spawns)

    def test_manifest_update_race_does_not_attach_stale_error(self):
        self.state['jobs'][0]['manifest_sha256'] = 'new'
        self.failed()
        self.assertFalse(self.events)
        self.assertNotIn('dispatch_blocker', self.state['jobs'][0])

    def test_budget_change_prevents_launch(self):
        self.c.budgets_ready = lambda *a, **k: False
        self.assertEqual(self.failed().code, 'HOST_STORAGE_BUDGET')
        self.assertFalse(self.scans or self.spawns)

    def test_source_change_is_blocked_without_worker(self):
        def fail(j): raise ValueError('source changed fixture')
        self.c.source_preflight = fail
        self.failed()
        self.assertEqual(self.state['jobs'][0]['status'], 'BLOCKED')
        self.assertIn('source changed', self.state['jobs'][0]['error'])
        self.assertFalse(self.spawns)

    def test_second_scan_busy_rejects_and_rolls_back_intent(self):
        original = self.c.observe
        def scan(*a):
            if self.scans: raise ValueError('PROCESS_PRESENT')
            return original(*a)
        self.c.observe = scan
        self.failed()
        self.assertFalse(self.spawns or self.state['active_runs'])
        self.assertNotIn('launch_intent', self.events)

    def test_missing_common_window_rejects(self):
        def fail(*a): raise ValueError('No complete common idle window: GAP')
        self.c.require_idle_window = fail
        self.failed()
        self.assertFalse(self.spawns)

    def test_spawn_exception_is_recovery_blocked_not_retried(self):
        def fail(*a, **k): raise OSError('spawn uncertain')
        self.c.subprocess.Popen = fail
        self.failed()
        self.assertIsNotNone(self.state['recovery'])
        self.assertIn('job-93', self.state['active_runs'])

    def test_commit_failure_preserves_spawned_identity_and_blocks_recovery(self):
        self.commit_fail = True
        self.failed()
        self.assertEqual(len(self.spawns), 1)
        self.assertEqual(self.state['active_runs']['job-93']['guardian_spawn']['pid'], 1234)
        self.assertIsNotNone(self.state['recovery'])

    def test_success_clears_current_blocker_and_preserves_last_reason(self):
        self.history_delay = 3
        self.failed()
        self.history_delay = .1
        self.call()
        j = self.state['jobs'][0]
        self.assertNotIn('dispatch_blocker', j)
        self.assertIn('last_dispatch_blocker', j)
        self.assertEqual(d.blocker_view(j), {})

    def test_error_view_does_not_leak_previous_manifest_reason(self):
        self.history_delay = 3
        self.failed()
        j = self.state['jobs'][0]
        j['manifest_sha256'] = 'changed'
        self.assertEqual(d.blocker_view(j), {})

    def test_profile_change_rejected(self):
        self.state['jobs'][0]['manifest']['stages'][0]['resource_profiles'] = [{'gpu_count': 1}]
        self.assertEqual(self.failed().code, 'RESOURCE_PROFILE_CHANGED')
        self.assertFalse(self.spawns)


if __name__ == '__main__':
    unittest.main()
