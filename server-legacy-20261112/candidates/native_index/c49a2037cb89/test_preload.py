"""Deterministic CPU fault injection; no SSH, GPU, production state or project files."""
import contextlib
import copy
import json
from pathlib import Path
import unittest
import sys
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
from preload import verify

class Clock:
    def __init__(self): self.now = 1000.0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds

class Core:
    ROOT = Path('/isolated-fixture')
    VERSION = 'ugs-exec-v3.1'
    def __init__(self, clock):
        self.clock=clock; self.locked=False; self.audit=[]; self.scan_times=[]
        self.lock_delay=0; self.commit_delay=0; self.audit_delay=0
        self.post_scan_delay=0; self.cancel_before_lock=False; self.reserve_before_lock=False
        self.overlap=False; self.scan_failure=False; self.audit_failure=False
        self.budget_failure=False; self.pin_failure=False; self.bad_window=False
        self.active={'job_id':'job-test','generation':'generation-test','gpus':['gpu-a','gpu-b'],
                     'authorized_at':'initial','prepare_deadline':2000,'profile':{}}
        self.state={'active_runs':{'job-test':self.active},'policy':{},'daemon':{},'gpu_reservations':{}}
    def Store(self): return self
    @contextlib.contextmanager
    def tx(self,event=None):
        if event:
            self.clock.sleep(self.lock_delay)
            if self.cancel_before_lock:self.active['cancel_requested']=True
            if self.reserve_before_lock:self.state['gpu_reservations']['gpu-a']={}
            if self.overlap:self.state['active_runs']['other']={'gpus':['gpu-a']}
        self.locked=True
        snapshot=copy.deepcopy(self.state)
        try:
            yield self.state
        except Exception:
            self.state=snapshot;self.active=self.state['active_runs']['job-test'];raise
        else:
            if event:self.clock.sleep(self.commit_delay)
        finally:self.locked=False
    def node_check(self): pass
    def require_v3(self,s): pass
    def active_for(self,s,env):
        a=s['active_runs'].get(env['UGS_JOB_ID'])
        return None if a and a.get('cancel_requested') else a
    def get_job(self,s,jid): return {'job_id':jid}
    def check_permission(self,s,env,caller,now):
        if self.pin_failure and len(self.scan_times)==2:return 2,{'reason':'RELEASE_OR_MIGRATION_NOT_ACCEPTED'}
        if now>self.active['prepare_deadline']:return 2,{'reason':'EXPIRED_OR_OLD_BOOT'}
        return 0,{'allowed':True,'verifier':self.VERSION}
    def source_preflight(self,j):
        if len(self.scan_times)==2:self.clock.sleep(self.post_scan_delay)
    def budgets_ready(self,*args,**kwargs):return not (self.budget_failure and len(self.scan_times)==2)
    def observed_records(self):return list(self.scan_times)
    def require_idle_window(self,records,gpus,policy,now,daemon):
        if self.bad_window and len(self.scan_times)==2:raise ValueError('No complete common idle window')
    def observe(self,policy,daemon,source):
        if len(self.scan_times)==1:
            assert self.locked, 'Final GPU scan must happen under state lock'
            if self.scan_failure:raise ValueError('GPU telemetry unavailable')
        self.clock.sleep(.9)
        record={'monotonic':self.clock.monotonic(),'at':'fixture','observation_id':str(len(self.scan_times))}
        self.scan_times.append(record)
        return record
    def stamp(self):return 'fixture-time'
    def append_json(self,path,record):
        if self.audit_failure:raise OSError('fixture disk error')
        self.clock.sleep(self.audit_delay);self.audit.append(copy.deepcopy(record))

class PreloadTests(unittest.TestCase):
    def setUp(self):self.clock=Clock();self.core=Core(self.clock)
    def run_case(self):return verify(self.core,{'UGS_JOB_ID':'job-test','UGS_GENERATION':'generation-test'},123,self.clock)
    def test_success_final_scan_under_lock_and_spacing(self):
        code,out=self.run_case();self.assertEqual(code,0);self.assertTrue(out['allowed'])
        self.assertGreaterEqual(self.core.scan_times[1]['monotonic']-self.core.scan_times[0]['monotonic'],2)
        self.assertLessEqual(out['final_sample_age_seconds'],2)
        self.assertTrue(self.core.active['preload_authorized_at'])
    def test_slow_state_lock_replay_before_final_scan_does_not_age_it(self):
        self.core.lock_delay=6
        code,out=self.run_case();self.assertEqual(code,0)
        phase=next(p for p in out['timings'] if p['phase']=='final_state_lock_and_replay')
        self.assertEqual(phase['seconds'],6)
        self.assertLessEqual(out['final_sample_age_seconds'],2)
    def test_slow_post_scan_source_check_still_denied(self):
        self.core.post_scan_delay=2.01
        code,out=self.run_case();self.assertEqual(code,2);self.assertIn('stale',out['message'])
        self.assertNotIn('preload_authorized_at',self.core.active)
    def test_slow_commit_denied_despite_consumed_one_shot(self):
        self.core.commit_delay=2.01
        code,out=self.run_case();self.assertEqual(code,2);self.assertFalse(out['allowed'])
        self.assertIn('preload_authorized_at',self.core.active)
        self.assertEqual(self.run_case()[0],2)  # no permit reuse after denial
    def test_slow_audit_denied(self):
        self.core.audit_delay=2.01
        code,out=self.run_case();self.assertEqual(code,2);self.assertIn('after decision audit',out['message'])
    def test_audit_failure_denied(self):
        self.core.audit_failure=True
        code,out=self.run_case();self.assertEqual(code,2);self.assertIn('audit write failed',out['message'])
    def test_cancel_while_waiting_lock_denied_before_scan(self):
        self.core.cancel_before_lock=True
        self.assertEqual(self.run_case()[0],2);self.assertEqual(len(self.core.scan_times),1)
    def test_reservation_while_waiting_lock_denied(self):
        self.core.reserve_before_lock=True
        self.assertEqual(self.run_case()[0],2);self.assertEqual(len(self.core.scan_times),1)
    def test_overlapping_uuid_allocation_denied(self):
        self.core.overlap=True
        self.assertEqual(self.run_case()[0],2);self.assertEqual(len(self.core.scan_times),1)
    def test_final_scan_failure_denied_and_audited(self):
        self.core.scan_failure=True
        code,out=self.run_case();self.assertEqual(code,2)
        self.assertEqual(self.core.audit[-1]['reason'],'GPU telemetry unavailable')
    def test_final_history_change_denied(self):
        self.core.bad_window=True
        self.assertEqual(self.run_case()[0],2);self.assertNotIn('preload_authorized_at',self.core.active)
    def test_final_budget_change_denied(self):
        self.core.budget_failure=True
        self.assertEqual(self.run_case()[0],2);self.assertNotIn('preload_authorized_at',self.core.active)
    def test_final_pin_change_denied(self):
        self.core.pin_failure=True
        self.assertEqual(self.run_case()[0],2);self.assertNotIn('preload_authorized_at',self.core.active)
    def test_expired_prepare_deadline_denied(self):
        self.core.lock_delay=1100
        self.assertEqual(self.run_case()[0],2);self.assertEqual(len(self.core.scan_times),1)

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PreloadTests))
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
            'test_kind':'deterministic CPU fault injection with simulated core/clock',
            'production_state_changed':False,'cuda_used':False,'server_integration_tested':False}
    Path(__file__).with_name('cpu-test-result.json').write_text(json.dumps(report,indent=2)+'\n')
    raise SystemExit(0 if result.wasSuccessful() else 1)
