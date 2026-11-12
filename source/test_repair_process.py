"""CLI/daemon-hook integration using separate OS processes and a private journal.
Admission and GPU cleanup are fixtures, not GPU acceptance or live service tests.
"""
import contextlib,json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
import test_repair_v1 as fixtures
from test_repair_v1 import load_core
import ugs_repair_v1 as repair


def fixture_process(root,args):
    c=load_core();store=c.Store(root)
    c.Store=lambda:store;c.node_check=lambda:None
    c.job_validate=lambda *a:None;c.admission=lambda *a:{}
    original=c.scoped
    c.scoped=lambda s,j,p=None:original(s,j,p or c.get_job(s,j)['project_id'])
    c.live=lambda identity:bool(identity)
    # Record calls; don't signal any real process from this fixture.
    c.stop_owned=lambda jid:(root/'stop-request').write_text(jid)
    def finalize(idle=None):
        if not (root/'fixture-cleanup-proof').exists():return
        with store.tx('fixture_cleanup') as s:
            for jid,a in list(s['active_runs'].items()):
                if a.get('cancel_requested'):
                    s['active_runs'].pop(jid);c.get_job(s,jid)['status']='CANCELLED'
    c.finalize_available=finalize
    repair.install(c)
    if args==['tick']:c.finalize_available();return
    from unittest.mock import patch
    from types import SimpleNamespace
    with patch.object(repair.pwd,'getpwuid',return_value=SimpleNamespace(pw_name='kzhang19')):
        sys.argv=['ugs']+args;c.cli()


class ProcessIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);f=fixtures.Repair();f.setUp();self.f=f
        self.store=f.c.Store(self.root)
        f.s['daemon']={'identity':{'pid':123,'start':'fixture'},'repair_revision':'ugs-repair-v1'}
        with self.store.tx('fixture_setup') as s:s.clear();s.update(f.s)
        self.file=self.root/'replacement.json';self.file.write_text(json.dumps(f.m))
    def command(self,*args):
        return [sys.executable,'-B',__file__,'fixture',str(self.root),*args]
    def run_cli(self,*args,ok=True):
        p=subprocess.run(self.command(*args),capture_output=True,text=True,timeout=20)
        if ok:self.assertEqual(p.returncode,0,p.stderr)
        return p
    def test_disconnect_reload_cleanup_and_resume(self):
        with self.store.tx('running') as s:
            j=self.f.c.get_job(s,self.f.old);j['status']='RUNNING';j['first_started_at']='fixture'
            s['active_runs'][j['job_id']]={'generation':'fixture','gpus':['fixture-only']}
        p=self.run_cli('experiment','replace',self.f.old,'--file',str(self.file))
        self.assertEqual(json.loads(p.stdout)['status'],'WAITING_CLEANUP')
        self.assertTrue((self.root/'stop-request').exists())
        self.assertNotEqual(self.run_cli('scheduler','resume',ok=False).returncode,0)
        self.run_cli('tick');self.assertEqual(len(self.store.read()['jobs']),3)
        (self.root/'fixture-cleanup-proof').write_text('simulated guardian + GPU release')
        self.run_cli('tick')
        p=self.run_cli('experiment','replacement',self.f.old)
        self.assertEqual(json.loads(p.stdout)['status'],'COMPLETED')
        self.assertTrue(self.store.read()['policy']['scheduler_paused'])
        self.run_cli('scheduler','resume');self.assertFalse(self.store.read()['policy']['scheduler_paused'])
    def test_two_clients_same_replace_create_one_job(self):
        cmd=self.command('experiment','replace',self.f.old,'--file',str(self.file))
        clients=[subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        results=[]
        for p in clients:
            out,err=p.communicate(timeout=20);self.assertEqual(p.returncode,0,err);results.append(json.loads(out))
        self.assertEqual(results[0],results[1]);self.assertEqual(len(self.store.read()['jobs']),4)
    def test_two_queue_moves_one_stale_revision_rejected(self):
        revision=self.store.read()['revision']
        cmd=self.command('queue','move',self.f.later,'--before',self.f.old,'--reason','explicit test','--expected-revision',str(revision))
        clients=[subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for _ in range(2)]
        codes=[]
        for p in clients:p.communicate(timeout=20);codes.append(p.returncode)
        self.assertEqual(sum(code==0 for code in codes),1)
    def test_old_runtime_rejected_without_state_mutation(self):
        with self.store.tx('old_runtime') as s:s['daemon'].pop('repair_revision')
        before=self.store.journal.read_bytes()
        p=self.run_cli('experiment','replace',self.f.old,'--file',str(self.file),ok=False)
        self.assertNotEqual(p.returncode,0);self.assertEqual(self.store.journal.read_bytes(),before)
        self.assertFalse((self.root/'stop-request').exists())

if __name__=='__main__':
    if sys.argv[1:2]==['fixture']:
        try:fixture_process(Path(sys.argv[2]),sys.argv[3:])
        except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
    else:unittest.main()
