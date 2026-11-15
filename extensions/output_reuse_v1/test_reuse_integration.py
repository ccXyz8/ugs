import copy,json,os,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import output_reuse
@unittest.skipUnless(Path('/proc/sys/kernel/random/boot_id').exists(),'gpu07/Linux acceptance')
class RealValidator(unittest.TestCase):
 def test_existing_receipt_allowed_without_weakening_source_validation(self):
  import ugs_reuse_entry as e
  c=e.core
  with tempfile.TemporaryDirectory() as td:
   project=Path(td).resolve();root=project/'evaluation/exp_1';root.mkdir(parents=True)
   source=root/'worker.py';source.write_text('print("fixture")\n');receipt=root/'receipt.json';receipt.write_text('{"old":true}')
   p={'project_id':'streasoner-more','project_root':str(project),'allowed_roots':[str(project)],'python':c.PYTHON}
   profile={'profile_id':'test','gpu_count':2,'gpu_model':'A100','precision':'bf16','execution':'ddp','world_size':2,'host_start_mib':1,'host_running_mib':1,'disk_min_gib':1,'acceptance_ref':'test'}
   stage={'stage_id':'val','kind':'validation','argv':[c.PYTHON,str(source)],'cwd':str(root),'output_dir':str(root),'evaluation_dir':str(root),'log_path':str(root/'log.txt'),'result_path':str(receipt),'source_sha256':{str(source):c.hash_file(source)},'resource_profiles':[profile],'adapter_accepted':True,'requires_previous_gate':False}
   m={'project_id':'streasoner-more','experiment_id':'exp_1','submission_version':'2','protocol':c.PROTOCOL,'protocol_sha256':c.hash_file(c.PROTOCOL_FILE),'stages':[stage],'artifact_policy':{'mode':'reuse','root':str(root)}}
   with patch.object(output_reuse,'PROJECT_ROOT',str(project)):
    c._job_validate(m,p)
    # Real core submit/admission/retry, isolated in-memory queue; same existing
    # result/log/output paths throughout. No GPU or production queue writes.
    report={'contract':'ugs-cpu-admission-v1','passed':True,'automatic_retry':False,'checks':[{'passed':source.is_file()}],'source_sha256':stage['source_sha256'],'resource_profiles':stage['resource_profiles']}
    evidence=root/'cpu.json';evidence.write_text(json.dumps(report))
    m.update(runtime_release=c.VERSION,admission={'contract':'ugs-stage-v2','automatic_retry':False,'stages':{'val':{'scope':'bounded_interface','cpu':{'path':str(evidence),'sha256':c.hash_file(evidence)},'interface_evidence':[]}}})
    state=c.initial();state['projects']={'streasoner-more':p};state['jobs']=[]
    first=c.submit(state,m);prior=c.get_job(state,first['job_id']);prior['status']='FAILED';before=copy.deepcopy(prior)
    next_manifest=copy.deepcopy(m);next_manifest['submission_version']='3'
    second=c.submit(state,next_manifest,first['job_id'])
    self.assertNotEqual(first['job_id'],second['job_id']);self.assertEqual(prior,before)
    self.assertEqual(c.get_job(state,second['job_id'])['manifest']['stages'][0]['result_path'],str(receipt))
    self.assertEqual(receipt.read_text(),'{"old":true}') # registration never clears it
    old=copy.deepcopy(m);old.pop('artifact_policy')
    with self.assertRaisesRegex(ValueError,'fresh result_path'):c._job_validate(old,p)
    stage['source_sha256'][str(source)]='0'*64
    with self.assertRaisesRegex(ValueError,'Source identity mismatch'):c._job_validate(m,p)
if __name__=='__main__':unittest.main()
