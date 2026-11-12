"""Synthetic CPU histories; never starts CUDA or changes production state."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source')
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
import ugs_exec_v3_1 as c
import memory_runtime
import idle_runtime as w
memory_runtime.install();w.install(c)

class Window(unittest.TestCase):
    def setUp(self):
        self.ids=['fixture-'+str(i) for i in range(8)]
        b={'memory_mib':4,'memory_ceiling_mib':7,'driver_version':'fixture','memory_reserved_mib':620,'memory_total_mib':40960}
        self.policy={'idle_seconds':120,'scan_seconds':30,'max_sample_gap_seconds':35,'baseline':{g:dict(b) for g in self.ids},'gpu_inventory':self.ids}
        self.daemon={'identity':{'pid':123,'start':'fixture'},'monotonic':0}
        self.valid=patch.object(c,'baseline_valid',lambda b:True);self.valid.start();self.addCleanup(self.valid.stop)
    def rows(self,times=(30,60,90,120,150),memory=7):
        cards=[{'uuid':g,'name':'A100','memory_mib':memory,'memory_reserved_mib':620,'memory_total_mib':40960,'driver_version':'fixture','health_ok':True,'ecc_uncorrectable':0,'processes':[],'util':0,'memory_util':0} for g in self.ids]
        return [{'boot':c.BOOT,'daemon_pid':123,'daemon_start':'fixture','version':c.VERSION,'monotonic':t,'started_monotonic':t-.1,'valid':True,'policy_sha256':c.observation_policy(self.policy),'cards':copy.deepcopy(cards)} for t in times]
    def details(self,rows,now=150):return c.idle_state(rows,self.policy,now,self.daemon)[0]
    def test_exact_120_with_five_samples(self):
        self.assertTrue(all(v['eligible'] for v in self.details(self.rows()).values()))
        c.require_idle_window(self.rows(),self.ids[:2],self.policy,150,self.daemon)
    def test_119_seconds_rejected_even_with_five_samples(self):
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows((30,60,90,120,149)),149).values()))
    def test_four_samples_cannot_qualify_by_waiting(self):
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows((30,60,90,120)),150).values()))
    def test_gap_over35_resets(self):
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows((30,60,96,126,156)),156).values()))
    def test_stale_and_future_fail(self):
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows(),186).values()))
        with self.assertRaises(ValueError):self.details(self.rows(),149)
    def test_selected_group_rejects_one_busy_card(self):
        rows=self.rows();rows[-2]['cards'][0]['processes']=[{'pid':321,'type':'C'}]
        self.assertTrue(self.details(rows)[self.ids[1]]['eligible'])
        with self.assertRaises(ValueError):c.require_idle_window(rows,self.ids[:2],self.policy,150,self.daemon)
    def test_health_unknown_ecc_util_and_memory_still_reject(self):
        for key,value in [('health_ok',False),('processes',None),('ecc_uncorrectable',1),('util',1),('memory_util',1),('memory_mib',8),('memory_mib',3)]:
            with self.subTest(key=key,value=value):
                rows=self.rows();rows[-1]['cards'][0][key]=value
                self.assertFalse(self.details(rows)[self.ids[0]]['eligible'])
    def test_observation_failure_and_invalidation_reset(self):
        rows=self.rows();rows[-2]['valid']=False
        self.assertFalse(any(v['eligible'] for v in self.details(rows).values()))
        rows=self.rows();rows[-2]={'boot':c.BOOT,'daemon_pid':123,'daemon_start':'fixture','version':c.VERSION,'monotonic':120,'invalidated_gpus':self.ids[:1]}
        self.assertFalse(self.details(rows)[self.ids[0]]['eligible'])
    def test_legacy300_and_allocation_candidates(self):
        idle=c.Idle();idle.sync(self.rows(),self.policy,self.daemon,150)
        self.assertEqual(len(idle.candidates(150)),8)
        self.assertEqual(idle.candidates(150,300),[])
        self.assertEqual(len(idle.candidates(150,0)),8)
        self.assertEqual(idle.candidates(186),[])
        self.policy['idle_seconds']=300
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows()).values()))
        self.assertTrue(all(v['eligible'] for v in self.details(self.rows(range(30,331,30)),330).values()))
    def test_memory_fallback_invalidates_old_history(self):
        rows=self.rows(memory=4)
        memory_runtime.latch_fallback(self.policy,[{'uuid':self.ids[0],'memory_mib':8}],{'at':'fixture'})
        self.assertFalse(any(v['eligible'] for v in self.details(rows).values()))
        self.assertTrue(all(v['eligible'] for v in self.details(self.rows(memory=4)).values()))
        self.assertFalse(any(v['eligible'] for v in self.details(self.rows(memory=7)).values()))
    def test_policy_only_allows_reviewed_windows(self):
        for value in (0,1,119,121,'120',True,None):
            with self.subTest(value=value),self.assertRaises(ValueError):w.parameters({'idle_seconds':value})
        self.assertEqual(w.parameters({}),(300,11))
    def test_stage_contract_detection_and_tamper(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'cpu.json'
            report={'source_sha256':{str(w.HERE/'idle_runtime.py'):c.hash_file(str(w.HERE/'idle_runtime.py'))},'preflight':{'verifier':{'path':str(w.HERE/'verify_launch_idle.py'),'sha256':c.hash_file(str(w.HERE/'verify_launch_idle.py'))}}}
            p.write_text(json.dumps(report))
            job={'stage_index':0,'manifest':{'stages':[{'stage_id':'test'}],'admission':{'stages':{'test':{'cpu':{'path':str(p),'sha256':c.hash_file(str(p))}}}}}}
            self.assertEqual(c.stage_idle_seconds(job,self.policy),120)
            report['preflight']['verifier']['path']='old-verifier';p.write_text(json.dumps(report));job['manifest']['admission']['stages']['test']['cpu']['sha256']=c.hash_file(str(p))
            self.assertEqual(c.stage_idle_seconds(job,self.policy),300)
            p.write_text('{}')
            self.assertEqual(c.stage_idle_seconds(job,self.policy),300)
    def test_select_profile_respects_legacy_and_reservations(self):
        idle=c.Idle();idle.sync(self.rows(),self.policy,self.daemon,150)
        job={'job_id':'fixture','project_id':'p','stage_index':0,'manifest':{'stages':[{'stage_id':'test','resource_profiles':[{'gpu_count':2}]}]}}
        s={'policy':self.policy,'projects':{'p':{}},'active_runs':{},'gpu_reservations':{}}
        with patch.object(c,'accepted_stage',return_value=True),patch.object(c,'budgets_ready',return_value=True),patch.object(c.time,'monotonic',return_value=150):
            self.assertIsNone(c.select_profile(job,s,idle))
            # age=False is potential capacity only; not launch authorization.
            self.assertEqual(len(c.select_profile(job,s,idle,False)[1]),2)
            s['gpu_reservations']={g:{} for g in self.ids}
            self.assertIsNone(c.select_profile(job,s,idle,False))

if __name__=='__main__':unittest.main()
