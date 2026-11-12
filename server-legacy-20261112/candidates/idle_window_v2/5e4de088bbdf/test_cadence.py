import contextlib,copy,json,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import dispatch_cadence as d
import idle_preflight_v2 as p

class End(Exception):pass
class Clock:
 def __init__(self):self.now=0.
 def monotonic(self):return self.now
 def sleep(self,n):
  self.now+=n
  if self.now>=61:raise End()

class Cadence(unittest.TestCase):
 def run_loop(self,active=False,repair=False,fail=False):
  with tempfile.TemporaryDirectory() as folder:
   clock=Clock();self.samples=[];self.reads=0;self.cleanups=0;self.errors=[]
   state={'active_runs':{'j':{}} if active else {},'handoff_ready':active,'policy':{'scan_seconds':15},'executor_release':{'dispatch_revision':d.REVISION,'files':{'entry':'sha',d.__file__:'sha'}},'replacements':{'r':{'status':'WAITING_CLEANUP'}} if repair else {}}
   @contextlib.contextmanager
   def tx(event=None):
    self.reads+=1
    yield state
   def observe(*args):
    self.samples.append(clock.now)
    if fail:raise ValueError('fixture failed scan')
   def finalize(*args):self.cleanups+=1
   c=types.SimpleNamespace(node_check=lambda:None,ROOT=Path(folder),SELF='entry',Store=lambda:types.SimpleNamespace(tx=tx),require_v3=lambda s:None,release_ok=lambda s:True,hash_file=lambda p:'sha',identity=lambda p:{'pid':p},VERSION='fixture',stamp=lambda:'fixture',time=clock,Idle=lambda:types.SimpleNamespace(reset=lambda:None,sync=lambda *a:None),observe=observe,idle_parameters=lambda p:(60,15,20,5),detect_lost_leaders=lambda:None,finalize_available=finalize,observed_records=lambda:[],eligible=lambda s:False,append_json=lambda p,r:self.errors.append(r))
   with self.assertRaises(End):d.serve(c)
 def test_idle_cadence_15_and_single_read_per_loop(self):
  self.run_loop();self.assertEqual(self.samples,[0,15,30,45,60]);self.assertEqual(self.cleanups,0);self.assertEqual(self.reads,123)
 def test_active_cleanup_preserved(self):
  self.run_loop(active=True);self.assertGreater(self.cleanups,0);self.assertEqual(self.samples,[0,15,30,45,60])
 def test_repair_cleanup_preserved(self):
  self.run_loop(repair=True);self.assertGreater(self.cleanups,0)
 def test_failed_scan_does_not_stop_sampling(self):
  self.run_loop(fail=True);self.assertEqual(len(self.errors),5);self.assertEqual(self.samples,[0,15,30,45,60])

class Preflight(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=Path(self.tmp.name)
  self.calls=[];old={'path':'old','sha256':'oldsha','timeout':20}
  self.pre=types.SimpleNamespace(PROJECTS={'p'},VERIFIER='old',VERIFIER_SHA='oldsha',VERIFIER_SPEC=old,core=types.SimpleNamespace(hash_file=lambda p:'sha'))
  def validate(m,project,start):self.calls.append((copy.deepcopy(m),copy.deepcopy(self.pre.VERIFIER_SPEC)))
  self.pre.validate_preflight=validate
  p.install(self.pre)
 def manifest(self,specs):
  stages=[];pins={}
  for i,spec in enumerate(specs):
   key=str(i);path=self.folder/(key+'.json');path.write_text(json.dumps({'preflight':{'verifier':spec},'source_sha256':{str(p.HERE/'idle_runtime_v2.py'):'sha'}}));stages.append({'stage_id':key});pins[key]={'cpu':{'path':str(path)}}
  return {'project_id':'p','stages':stages,'admission':{'stages':pins}}
 def new(self):return {'path':str(p.HERE/'verify_launch_idle_v2.py'),'sha256':'sha','timeout':20}
 def test_mixed_stages_old_new_and_restoration(self):
  old=self.pre.VERIFIER_SPEC.copy();m=self.manifest([old,self.new()]);self.pre.validate_preflight(m,{},0)
  self.assertEqual([s for _,s in self.calls],[old,self.new()]);self.assertEqual(self.pre.VERIFIER_SPEC,old);self.assertEqual(len(m['stages']),2)
 def test_unknown_and_missing_helper_rejected(self):
  with self.assertRaises(ValueError):self.pre.validate_preflight(self.manifest([{'path':'bad'}]),{})
  m=self.manifest([self.new()]);path=self.folder/'0.json';r=json.loads(path.read_text());r['source_sha256']={};path.write_text(json.dumps(r))
  with self.assertRaises(ValueError):self.pre.validate_preflight(m,{})
 def test_completed_prefix_not_revalidated(self):
  m=self.manifest([{'path':'bad'},self.new()]);self.pre.validate_preflight(m,{},1);self.assertEqual(len(self.calls),1)

if __name__=='__main__':unittest.main()
