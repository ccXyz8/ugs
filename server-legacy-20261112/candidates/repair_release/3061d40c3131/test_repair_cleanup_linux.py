"""Real CPU process-tree cancellation + existing finalizer. GPU release is injected.
Never signals production identities or queries GPU hardware.
"""
import importlib.util,json,os,subprocess,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_repair_v1 as fixtures
import ugs_repair_v1 as repair

@unittest.skipUnless(sys.platform.startswith('linux'),'requires /proc identity checks')
class CleanupIntegration(unittest.TestCase):
    def test_cancel_real_descendants_wait_for_gpu_proof_then_replace(self):
        f=fixtures.Repair();f.setUp();c=f.c
        with tempfile.TemporaryDirectory(prefix='ugs-repair-cpu-') as raw:
            root=Path(raw);c.ROOT=root;store=c.Store(root);c.Store=lambda:store
            source=Path(__file__).with_name('ugs_control_v2.py')
            if not source.exists():source=Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source/ugs_control_v2.py')
            spec=importlib.util.spec_from_file_location('cpu_control',source);control=importlib.util.module_from_spec(spec)
            with patch.dict(sys.modules,{'ugs_exec_v3_1':c}):spec.loader.exec_module(control)
            c.stop_owned=control.stop_owned;c.finalize_available=control.finalize_available
            c.invalidate_observations=lambda *a:None
            release=[False];c.gpu_contexts_gone=lambda a:release[0]
            script="""import subprocess,sys,json,os
from pathlib import Path
r=Path(sys.argv[1]);workers=[subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)']) for _ in range(2)]
(r/'ready').write_text(json.dumps([p.pid for p in workers]))
exits=[p.wait() for p in workers]
(r/'proof').write_text(json.dumps({'generation':'fixture','stage_nonce':'nonce','all_children_reaped':True,'remaining':[],'leader_exit_code':exits[0]}))
"""
            guardian=subprocess.Popen([sys.executable,'-c',script,str(root)])
            try:
                deadline=time.monotonic()+5
                while not (root/'ready').exists() and time.monotonic()<deadline:time.sleep(.02)
                pids=json.loads((root/'ready').read_text());identities=[c.identity(pid) for pid in pids]
                with store.tx('fixture') as s:
                    s.clear();s.update(f.s)
                    s['daemon']={'identity':c.identity(os.getpid()),'repair_revision':'ugs-repair-v1'}
                    j=c.get_job(s,f.old);j['status']='RUNNING';j['first_started_at']='fixture'
                    s['active_runs'][f.old]={'guardian':c.identity(guardian.pid),'process':identities[0],
                         'generation':'fixture','stage_nonce':'nonce','guardian_result':str(root/'proof'),'gpus':[]}
                    repair.begin(c,s,f.old,f.m)
                repair.install(c)
                c.stop_owned(f.old)
                guardian.wait(timeout=10)
                self.assertTrue(all(not c.live(x) for x in identities))
                c.finalize_available()
                s=store.read();self.assertIn(f.old,s['active_runs']);self.assertEqual(len(s['jobs']),3)
                release[0]=True;c.finalize_available()
                s=store.read();self.assertNotIn(f.old,s['active_runs']);self.assertEqual(len(s['jobs']),4)
                self.assertEqual(s['replacements'][f.old]['status'],'COMPLETED')
                self.assertTrue(s['policy']['scheduler_paused'])
            finally:
                if guardian.poll() is None:
                    for record in c.process_tree(c.identity(guardian.pid)):c.exact_signal(record,9)
                    guardian.wait(timeout=10)

if __name__=='__main__':unittest.main()
