import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import output_reuse as r
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.project=Path(self.tmp.name).resolve()/'project';self.root=self.project/'evaluation/exp_1';self.root.mkdir(parents=True)
  self.p={'project_root':str(self.project)}
  self.patch=patch.object(r,'PROJECT_ROOT',str(self.project));self.patch.start();self.addCleanup(self.patch.stop)
  self.m={'project_id':'streasoner-more','submission_version':'attempt2','artifact_policy':{'mode':'reuse','root':str(self.root)},'stages':[]}
  for sid in ('train','val','test'):
   audit=self.root/'.ugs/attempt2'/sid;audit.mkdir(parents=True)
   self.m['stages'].append({'stage_id':sid,'output_dir':str(self.root),'evaluation_dir':str(self.root),'log_path':str(self.root/(sid+'.log')),'result_path':str(audit/'receipt.json'),'failure_record':{'path':str(audit/'failure.json')}})
 def job(self,i,m=None,status='QUEUED'):
  return {'job_id':str(i),'queue_seq':i,'manifest':copy.deepcopy(m or self.m),'project_id':'streasoner-more','status':status}
 def state(self,*jobs):return {'jobs':list(jobs),'active_runs':{},'projects':{'streasoner-more':self.p}}
 def core(self):
  c=SimpleNamespace(TERMINAL={'FAILED','SUCCEEDED','CANCELLED','WITHDRAWN'})
  c.get_job=lambda s,j:next(x for x in s['jobs'] if x['job_id']==j)
  c._job_validate=lambda *a:None
  c.job_validate=lambda *a:None
  def submit(s,m,retry=None):
   if retry:raise ValueError('old independent directory rule')
   j=self.job(len(s['jobs'])+1,m);s['jobs'].append(j);return {'job_id':j['job_id']}
  c.submit=submit;c.select_profile=lambda *a:('profile',['1','2']);c.launch_valid=lambda *a:True
  cli=SimpleNamespace(preflight=SimpleNamespace(failure_view=lambda j:{'reason':'old'},enrich_output=lambda d,a:d),help_zh=SimpleNamespace(NOTES=[]));r.install(c,cli);return c
 def test_three_stages_share_root_and_overwrite_existing_products(self):
  self.assertEqual(r.policy(self.m,self.p),self.root)
  # Real existing project paths, not replacement output directories.
  for name in ('train.log','val.log','test.log','predictions.json','metrics.json'):
   f=self.root/name;f.write_text('old');inode=f.stat().st_ino
   f.write_text('new');self.assertEqual(f.read_text(),'new');self.assertEqual(f.stat().st_ino,inode)
 def test_default_unchanged(self):
  m=copy.deepcopy(self.m);m.pop('artifact_policy');self.assertIsNone(r.policy(m,self.p))
  c=self.core();s=self.state(self.job(1,status='FAILED'))
  with self.assertRaises(ValueError):c.submit(s,m,'1')
 def test_other_project_rejected(self):
  with self.assertRaises(ValueError):r.policy(self.m,{'project_root':'/another'})
 def test_root_escape_and_symlink_rejected(self):
  for root in (self.project,self.root.parent/'wrong'):
   m=copy.deepcopy(self.m);m['artifact_policy']['root']=str(root)
   with self.assertRaises(ValueError):r.policy(m,self.p)
  alias=self.root.parent/'exp_2';alias.symlink_to(self.root,target_is_directory=True)
  m=copy.deepcopy(self.m);m['artifact_policy']['root']=str(alias)
  with self.assertRaises(ValueError):r.policy(m,self.p)
 def test_same_receipt_path_across_stages_allowed(self):
  m=copy.deepcopy(self.m);m['stages'][1]['result_path']=m['stages'][0]['result_path']
  self.assertEqual(r.policy(m,self.p),self.root)
 def test_same_root_claim_survives_stage_gap(self):
  a,b=self.job(1,status='WAITING_CONTINUATION'),self.job(2);s=self.state(a,b);c=self.core()
  self.assertEqual(r.blocker(c,s,b),'1');self.assertIsNone(c.select_profile(b,s,None));self.assertFalse(c.launch_valid(s,b,'x',['1','2']))
  a['status']='SUCCEEDED';self.assertIsNone(r.blocker(c,s,b));self.assertTrue(c.launch_valid(s,b,'x',['1','2']))
 def test_active_later_job_blocks_earlier_job_and_terminal_cleanup(self):
  a,b=self.job(1),self.job(2,status='FAILED');s=self.state(a,b);s['active_runs']['2']={};self.assertEqual(r.blocker(self.core(),s,a),'2')
 def test_parent_child_and_alias_conflicts(self):
  a,b=self.job(1),self.job(2);a['manifest'].pop('artifact_policy');a['manifest']['stages'][0]['output_dir']=str(self.root/'child');s=self.state(a,b)
  self.assertEqual(r.blocker(self.core(),s,b),'1')
 def test_distinct_experiments_can_run(self):
  a,b=self.job(1),self.job(2);a['manifest']['artifact_policy']['root']=str(self.root.parent/'exp_2');s=self.state(a,b);s['active_runs']['1']={};self.assertIsNone(r.blocker(self.core(),s,b))
 def test_retry_preserves_prior_and_creates_link(self):
  c=self.core();old=self.job(1,status='FAILED');old.update(first_started_at='now',gpu_release_verified_at='done');s=self.state(old);before=copy.deepcopy(old)
  out=c.submit(s,self.m,'1');self.assertEqual(old,before);self.assertEqual(s['jobs'][1]['explicit_retry_of'],'1');self.assertEqual(out['job_id'],'2')
 def test_retry_rejects_unsettled(self):
  c=self.core();old=self.job(1,status='FAILED');old['first_started_at']='now';s=self.state(old)
  with self.assertRaises(ValueError):c.submit(s,self.m,'1')
  old['gpu_release_verified_at']='done';s['active_runs']['1']={}
  with self.assertRaises(ValueError):c.submit(s,self.m,'1')
 def test_original_validation_and_final_gate_still_run(self):
  c=self.core()
  # installation preserves previous validator: reinstall around a failing guard.
  c.job_validate=lambda *a:(_ for _ in ()).throw(ValueError('original PIN failure'))
  c.launch_valid=lambda *a:False
  cli=SimpleNamespace(preflight=SimpleNamespace(failure_view=lambda j:{'reason':'old'},enrich_output=lambda d,a:d),help_zh=SimpleNamespace(NOTES=[]));r.install(c,cli)
  with self.assertRaisesRegex(ValueError,'original PIN'):c.job_validate(self.m,self.p)
  j=self.job(1);self.assertFalse(c.launch_valid(self.state(j),j,'x',['1','2']))
 def test_same_paths_reused_and_old_receipt_archived(self):
  import hashlib
  c=self.core();c.ROOT=Path(self.tmp.name)/'ugs';c.ROOT.mkdir()
  c.digest=lambda raw:hashlib.sha256(raw).hexdigest();c.stamp=lambda:'now'
  c.project_stage=lambda j:j['manifest']['stages'][0]
  c.append_json=lambda p,x:p.write_text(json.dumps(x)+'\n')
  old=self.job(1,status='SUCCEEDED');old.update(run_id='oldrun',first_started_at='old',gpu_release_verified_at='released')
  new=self.job(2);new.update(run_id='newrun',stage_index=0)
  s=self.state(old,new);path=Path(new['manifest']['stages'][0]['result_path'])
  record={'job_id':'1','run_id':'oldrun','stage_id':'train','generation':'old'}
  path.write_text(json.dumps(record));oldbytes=path.read_bytes()
  cli=SimpleNamespace(preflight=SimpleNamespace(failure_view=lambda j:{}))
  r.prepare(c,cli,s,new)
  self.assertFalse(path.exists());self.assertEqual(Path(old['artifact_receipt_history'][0]['archive_path']).read_bytes(),oldbytes)
  path.write_text(json.dumps(dict(record,job_id='2',run_id='newrun',generation='new')))
  self.assertEqual(json.loads(path.read_text())['job_id'],'2')
 def test_receipt_reset_refuses_unowned_file(self):
  c=self.core();c.project_stage=lambda j:j['manifest']['stages'][0]
  j=self.job(1);j['stage_index']=0;s=self.state(j);p=Path(j['manifest']['stages'][0]['result_path']);p.write_text('{"job_id":"foreign"}')
  with self.assertRaises((StopIteration,ValueError)):r.prepare(c,None,s,j)
  self.assertTrue(p.exists())
if __name__=='__main__':unittest.main()
