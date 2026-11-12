"""CPU-only permission, suffix immutability, and GPU selection regressions."""
import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import ugs_resource_entry as entry
import resource_override as r
c=entry.core

class Override(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        profile={'profile_id':'dual','gpu_count':2,'world_size':2,'gpu_model':'A100','precision':'bf16','execution':'distributed_inference','disk_min_gib':16,'host_start_mib':8192,'host_running_mib':8192,'acceptance_ref':'fixture'}
        stages=[{'stage_id':name,'kind':name,'argv':['python','old.py'],'source_sha256':{'old.py':'old'},'resource_profiles':[copy.deepcopy(profile)],'project_budget':{'windows':700},'requires_previous_gate':i>0,'cwd':'/fixture','output_dir':'/fixture/'+name,'evaluation_dir':'/fixture/eval','log_path':'/fixture/'+name+'/log','result_path':'/fixture/'+name+'/result'} for i,name in enumerate(['train','validation','test'])]
        self.old={'project_id':'sundial','experiment_id':'fixture','submission_version':'1','scientific_contract':{'seeds':[1,2],'windows':700},'evaluation_plan':{},'stages':stages,'admission':{'contract':'ugs-stage-v2','automatic_retry':False,'stages':{t['stage_id']:{'cpu':{'path':'fixture','sha256':'fixture'}} for t in stages}}}
        sha=c.digest(c.canonical(self.old))
        self.job={'job_id':'job-fixture','run_id':'run-fixture','project_id':'sundial','status':'WAITING_CONTINUATION','stage_index':2,'results':[{'stage_index':0},{'stage_index':1}],'first_started_at':'before','queue_seq':71,'idempotency_key':'old','manifest_sha256':sha,'manifest':copy.deepcopy(self.old),'manifest_history':[]}
        self.state={'jobs':[self.job],'active_runs':{},'projects':{'sundial':{'allowed_roots':[str(self.root)],'paused':False}},'policy':{'gpu_inventory':['GPU-a','GPU-5'],'max_experiment':1},'ring':['sundial'],'served':[]}
        self.new=copy.deepcopy(self.old);self.new['submission_version']='2'
        t=self.new['stages'][2];t['argv']=['python','single.py'];t['source_sha256']={'single.py':'new'}
        t['resource_profiles'][0].update(profile_id='single',gpu_count=1,world_size=1,execution='single',gpu_uuids=['GPU-5'])
        auth=self.root/'user.json';auth.write_text('{"authorized":true}')
        self.grant={'contract':r.REVISION,'job_id':self.job['job_id'],'run_id':self.job['run_id'],'project_id':'sundial','old_manifest_sha256':sha,'new_manifest_sha256':c.digest(c.canonical(self.new)),'from_stage_index':2,'stage_ids':['test'],'resource_profiles':{'test':copy.deepcopy(t['resource_profiles'])},'reason':'explicit single GPU test','user_instruction':'fixture user instruction','project_authorization':{'path':str(auth),'sha256':c.hash_file(auth)}}
        self.path=self.root/'grant.json';self.register()
        self.patchers=[patch.object(c,'job_validate',side_effect=lambda m,p,start:m),patch.object(c,'admission',return_value={'test':{}}),patch.object(c,'find_submission',return_value=None)]
        for p in self.patchers:p.start();self.addCleanup(p.stop)
    def register(self):
        self.path.write_text(json.dumps(self.grant));self.state['resource_change_grants']={str(self.path):{'path':str(self.path),'sha256':c.hash_file(self.path)}}
    def call(self):return r.apply(c,self.state,self.job['job_id'],self.new,self.grant['old_manifest_sha256'],str(self.path),'sundial')
    def test_granted_suffix_applies_preserving_identity_results_queue_controls(self):
        before=copy.deepcopy(self.state);self.call()
        for key in ('job_id','run_id','queue_seq','stage_index','results','first_started_at','status'):self.assertEqual(self.job[key],before['jobs'][0][key])
        for key in ('policy','active_runs','ring','served','projects'):self.assertEqual(self.state[key],before[key])
        self.assertEqual(self.job['manifest']['stages'][:2],self.old['stages'][:2]);self.assertEqual(len(self.job['resource_override_history']),1)
        self.assertEqual(self.job['manifest']['stages'][2]['resource_profiles'][0]['gpu_uuids'],['GPU-5'])
    def test_normal_update_still_rejects_resource_change(self):
        with self.assertRaisesRegex(ValueError,'Frozen resource semantics'):c.update_manifest(self.state,self.job,self.new,self.job['manifest_sha256'],2)
    def test_running_starting_and_terminal_rejected(self):
        for status in ('RUNNING','STARTING','STOPPING','SUCCEEDED','FAILED','CANCELLED'):
            with self.subTest(status=status):
                self.job['status']=status
                with self.assertRaises(ValueError):self.call()
    def test_stale_sha_and_job_run_scope_rejected(self):
        for key,value in [('old_manifest_sha256','wrong'),('run_id','wrong'),('job_id','other'),('project_id','other'),('from_stage_index',1),('new_manifest_sha256','wrong')]:
            with self.subTest(key=key):
                old=self.grant[key];self.grant[key]=value;self.register()
                with self.assertRaises(ValueError):self.call()
                self.grant[key]=old;self.register()
    def test_missing_or_tampered_authorization(self):
        self.state['resource_change_grants']={}
        with self.assertRaises(ValueError):self.call()
        self.register();self.path.write_text('{}')
        with self.assertRaises(ValueError):self.call()
    def test_completed_prefix_or_science_changes_rejected_even_if_grant_matches(self):
        for change in ('prefix','seed','windows','output','gate','cpu_prefix'):
            with self.subTest(change=change):
                proposed=copy.deepcopy(self.new)
                if change=='prefix':proposed['stages'][0]['argv']=['changed']
                elif change=='seed':proposed['scientific_contract']['seeds']=[999]
                elif change=='windows':proposed['stages'][2]['project_budget']['windows']=699
                elif change=='output':proposed['stages'][2]['output_dir']='/elsewhere'
                elif change=='gate':proposed['stages'][2]['requires_previous_gate']=False
                else:proposed['admission']['stages']['train']['cpu']['sha256']='changed'
                with self.assertRaises(ValueError):r.preserve_except_resources(c,self.old,proposed,2)
    def test_pending_revision_or_replace_rejected(self):
        self.job['pending_revision']={'fixture':True}
        with self.assertRaises(ValueError):self.call()
        del self.job['pending_revision'];self.state['replacements']={'x':{'target_job_id':self.job['job_id'],'status':'WAITING_CLEANUP'}}
        with self.assertRaises(ValueError):self.call()
    def test_validation_failure_restores_default_semantics_and_no_mutation(self):
        original=c.same_semantics;before=copy.deepcopy(self.state)
        with patch.object(c,'job_validate',side_effect=ValueError('CPU failed')):
            with self.assertRaisesRegex(ValueError,'CPU failed'):self.call()
        self.assertIs(c.same_semantics,original);self.assertEqual(self.state,before)
    def test_grant_cannot_be_replayed_after_apply(self):
        self.call()
        with self.assertRaises(ValueError):self.call()
    def test_unknown_or_invalid_gpu_target_rejected(self):
        for ids in ([],['GPU-5','GPU-5'],['5'],['GPU-unknown']):
            with self.subTest(ids=ids):
                self.new['stages'][2]['resource_profiles'][0]['gpu_uuids']=ids
                self.grant['new_manifest_sha256']=c.digest(c.canonical(self.new));self.grant['resource_profiles']={'test':copy.deepcopy(self.new['stages'][2]['resource_profiles'])};self.register()
                with self.assertRaises(ValueError):self.call()
    def test_selection_only_uses_requested_qualified_unreserved_gpu(self):
        self.job['manifest']=self.new
        class Idle:
            def candidates(self,now,window):return ['GPU-a','GPU-5']
        with patch.object(c,'accepted_stage',return_value=True),patch.object(c,'stage_idle_seconds',return_value=120),patch.object(c,'budgets_ready',return_value=True):
            self.assertEqual(c.select_profile(self.job,self.state,Idle())[1],['GPU-5'])
            self.state['gpu_reservations']={'GPU-5':{}}
            self.assertIsNone(c.select_profile(self.job,self.state,Idle()))
            self.state['gpu_reservations']={};self.state['active_runs']={'another':{'gpus':['GPU-5']}}
            self.assertIsNone(c.select_profile(self.job,self.state,Idle()))
    def test_target_unqualified_waits_even_if_another_card_ready(self):
        self.job['manifest']=self.new
        class Idle:
            def candidates(self,now,window):return ['GPU-a']
        with patch.object(c,'accepted_stage',return_value=True),patch.object(c,'stage_idle_seconds',return_value=120),patch.object(c,'budgets_ready',return_value=True):
            self.assertIsNone(c.select_profile(self.job,self.state,Idle()))
    def test_final_launch_recheck_rejects_wrong_gpu(self):
        self.job['manifest']=self.new
        with patch.object(c,'eligible',return_value=True),patch.object(c,'accepted_stage',return_value=True):
            self.assertTrue(c.launch_valid(self.state,self.job,self.job['manifest_sha256'],['GPU-5']))
            self.assertFalse(c.launch_valid(self.state,self.job,self.job['manifest_sha256'],['GPU-a']))

if __name__=='__main__':unittest.main()
