"""CPU state-machine tests against actual core, with GPU/admission I/O replaced."""
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ugs_repair_v1 as r


def load_core():
    path = Path(__file__).with_name('ugs_exec_v3_1.py')
    if not path.exists(): path = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source/ugs_exec_v3_1.py')
    spec = importlib.util.spec_from_file_location('repair_test_core', path)
    c = importlib.util.module_from_spec(spec)
    read = Path.read_text
    def fixture_read(p, *a, **kw):
        if str(p) == '/proc/sys/kernel/random/boot_id': return 'cpu-fixture-boot'
        return read(p, *a, **kw)
    with patch.object(Path, 'read_text', fixture_read): spec.loader.exec_module(c)
    return c


class Repair(unittest.TestCase):
    def setUp(self):
        self.c = load_core(); c = self.c
        c.job_validate = lambda m,p,*a: None
        c.admission = lambda m,p,*a: {}
        c.scoped = lambda s,j,p=None: self.scope(s,j,p)
        self.s = c.initial(); self.s['projects']={'A':{'allowed_roots':['/cpu-fixture']}, 'B':{'allowed_roots':['/cpu-fixture']}}
        self.s['ring']=['A','B']; self.s['served']=[]
        self.old = c.submit(self.s,self.manifest('old'))['job_id']
        self.later = c.submit(self.s,self.manifest('later'))['job_id']
        self.other = c.submit(self.s,self.manifest('other','B'))['job_id']
        self.m = self.manifest('fixed')
    def scope(self,s,j,p):
        job=self.c.get_job(s,j)
        if p is not None and p!=job['project_id']:raise ValueError('wrong project')
        return job
    def manifest(self,name,project='A'):
        root='/cpu-fixture/'+name
        return {'project_id':project,'experiment_id':name,'submission_version':'1',
                'stages':[{'stage_id':'one','output_dir':root+'/out','evaluation_dir':root+'/eval',
                           'log_path':root+'/out/log','result_path':root+'/out/result', 'resource_profiles':[]} ]}
    def running(self,jid):
        j=self.c.get_job(self.s,jid);j['status']='RUNNING';j['first_started_at']='fixture'
        self.s['active_runs'][jid]={'generation':'frozen','gpus':['fixture-gpu']}
    def test_running_atomic_pause_stop_and_cleanup_barrier(self):
        self.running(self.old);self.running(self.other)
        other=copy.deepcopy(self.s['active_runs'][self.other])
        x=r.begin(self.c,self.s,self.old,self.m)
        self.assertEqual(x['status'],'WAITING_CLEANUP');self.assertTrue(self.s['policy']['scheduler_paused'])
        self.assertEqual(self.s['active_runs'][self.other],other)
        self.assertEqual(len(self.s['jobs']),3)
        r.advance(self.c,self.s,self.old);self.assertEqual(len(self.s['jobs']),3)
        self.s['active_runs'].pop(self.old);self.c.get_job(self.s,self.old)['status']='CANCELLED'
        r.advance(self.c,self.s,self.old)
        self.assertEqual(len(self.s['jobs']),4);self.assertTrue(self.s['policy']['scheduler_paused'])
        self.assertEqual(self.s['jobs'][-1]['queue_position'],1)
    def test_precheck_failure_does_not_pause_or_stop(self):
        self.running(self.old);before=copy.deepcopy(self.s)
        self.c.job_validate=lambda *a: (_ for _ in ()).throw(ValueError('bad CPU proof'))
        with self.assertRaises(ValueError):r.begin(self.c,self.s,self.old,self.m)
        self.assertEqual(self.s,before)
    def test_completed_idempotency_and_queue_order(self):
        a=r.begin(self.c,self.s,self.old,self.m)
        self.assertEqual(r.begin(self.c,self.s,self.old,self.m),a)
        r.install(self.c)
        self.assertEqual(self.c.pending_heads(self.s)['A']['job_id'],a['replacement_job_id'])
        self.assertEqual([j['queue_seq'] for j in self.s['jobs']],[1,2,3,4])
    def test_abort_never_revives_or_resumes(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        r.abort(self.c,self.s,self.old)
        self.assertFalse(r.pending(self.s));self.assertEqual(self.s['jobs'][0]['status'],'STOPPING')
        self.assertIn('cancel_requested',self.s['active_runs'][self.old])
        self.assertTrue(self.s['policy']['scheduler_paused'])
    def test_changed_pins_block_after_cleanup(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        self.s['active_runs'].pop(self.old);self.s['jobs'][0]['status']='CANCELLED'
        self.c.job_validate=lambda *a: (_ for _ in ()).throw(ValueError('PIN changed'))
        r.advance(self.c,self.s,self.old)
        self.assertEqual(self.s['replacements'][self.old]['status'],'BLOCKED')
        self.assertEqual(len(self.s['jobs']),3)
    def test_recovery_blocks_completion(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        self.s['active_runs'].pop(self.old);self.s['jobs'][0]['status']='CANCELLED';self.s['recovery']={'reason':'unknown'}
        r.advance(self.c,self.s,self.old);self.assertEqual(len(self.s['jobs']),3)
    def test_project_sha_and_nested_output_protection(self):
        for mode in ['project','sha','path']:
            m=copy.deepcopy(self.m);kwargs={}
            if mode=='project':kwargs['project']='B'
            if mode=='sha':kwargs['expected']='stale'
            if mode=='path':m['stages'][0]['output_dir']='/cpu-fixture/old/out/nested'
            with self.assertRaises(ValueError):r.begin(self.c,self.s,self.old,m,**kwargs)
        self.assertFalse(self.s['policy']['scheduler_paused'])
    def test_update_selects_future_stage(self):
        calls=[];self.c.update_manifest=lambda *a:calls.append(a[-1]) or {}
        r.update(self.c,self.s,self.old,self.m);self.assertIsNone(calls[-1])
        self.running(self.old);r.update(self.c,self.s,self.old,self.m);self.assertEqual(calls[-1],1)
        self.s['active_runs'][self.old]['continuation']=True
        r.update(self.c,self.s,self.old,self.m);self.assertEqual(calls[-1],0)
    def test_durable_request_after_client_disconnect(self):
        with tempfile.TemporaryDirectory() as d:
            store=self.c.Store(Path(d));self.running(self.old)
            with store.tx('fixture') as s:s.clear();s.update(copy.deepcopy(self.s));r.begin(self.c,s,self.old,self.m)
            restored=store.read()
            self.assertEqual(restored['replacements'][self.old]['status'],'WAITING_CLEANUP')
            self.assertIn('cancel_requested',restored['active_runs'][self.old])
    def test_pending_prevents_eligibility(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        self.c.eligible=lambda s:True;r.install(self.c)
        self.assertFalse(self.c.eligible(self.s))
    def test_resume_refuses_unfinished_request_transactionally(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        with tempfile.TemporaryDirectory() as d:
            store=self.c.Store(Path(d))
            with store.tx('fixture') as state:state.clear();state.update(copy.deepcopy(self.s))
            self.c.Store=lambda:store;self.c.node_check=lambda:None;r.install(self.c)
            from types import SimpleNamespace
            with patch.object(sys,'argv',['ugs','scheduler','resume']), patch.object(r.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='kzhang19')):
                with self.assertRaises(ValueError):self.c.cli()
            self.assertTrue(store.read()['policy']['scheduler_paused'])
    def test_new_identity_and_other_project_round_unchanged(self):
        ring=copy.deepcopy(self.s['ring']);served=copy.deepcopy(self.s['served'])
        old_run=self.s['jobs'][0]['run_id']
        result=r.begin(self.c,self.s,self.old,self.m)
        new=self.c.get_job(self.s,result['replacement_job_id'])
        self.assertNotEqual(new['run_id'],old_run)
        self.assertEqual(self.s['ring'],ring);self.assertEqual(self.s['served'],served)
        self.assertEqual(new['explicit_retry_of'],self.old)
    def test_move_same_project_without_mutating_registration_or_ring(self):
        before=copy.deepcopy(self.s['ring'])
        r.move(self.c,self.s,self.later,self.old,True,'user requested')
        r.install(self.c)
        self.assertEqual(self.c.pending_heads(self.s)['A']['job_id'],self.later)
        self.assertEqual(self.s['ring'],before)
        self.assertEqual([j['queue_seq'] for j in self.s['jobs']],[1,2,3])
    def test_move_rejects_wrong_project_active_stale_and_no_reason(self):
        for anchor,reason,revision in [(self.other,'why',None),(self.old,'',None),(self.old,'why',-1)]:
            with self.assertRaises(ValueError):r.move(self.c,self.s,self.later,anchor,True,reason,expected_revision=revision)
        self.running(self.old)
        with self.assertRaises(ValueError):r.move(self.c,self.s,self.later,self.old,True,'why')
    def test_queue_lists_controls_without_claiming_gpu_readiness(self):
        self.s['policy']['scheduler_paused']=True
        rows=r.queue_rows(self.c,self.s,'A')['queue']
        self.assertIn('SCHEDULER_PAUSED',rows[0]['blocking_controls'])
        self.assertIn('PROJECT_FIFO',rows[1]['blocking_controls'])
        self.assertEqual(rows[0]['resource_check'],'NOT_EVALUATED')
    def test_dry_run_replace_leaves_journal_and_processes_unchanged(self):
        self.running(self.old)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=self.c.Store(root)
            with store.tx('fixture') as state:state.clear();state.update(copy.deepcopy(self.s))
            before=store.journal.read_bytes();f=root/'new.json';f.write_text(__import__('json').dumps(self.m))
            self.c.Store=lambda:store;self.c.node_check=lambda:None;r.install(self.c)
            from types import SimpleNamespace
            with patch.object(sys,'argv',['ugs','experiment','replace',self.old,'--file',str(f),'--dry-run']), patch.object(r.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='kzhang19')), patch.object(self.c,'stop_owned') as stop:
                import contextlib,io
                with contextlib.redirect_stdout(io.StringIO()):self.c.cli()
                stop.assert_not_called()
            self.assertEqual(store.journal.read_bytes(),before)
    def test_conflicting_replacement_rejected(self):
        self.running(self.old);r.begin(self.c,self.s,self.old,self.m)
        with self.assertRaises(ValueError):r.begin(self.c,self.s,self.old,self.manifest('different'))
        with self.assertRaises(ValueError):r.update(self.c,self.s,self.old,self.m)

if __name__=='__main__': unittest.main()
