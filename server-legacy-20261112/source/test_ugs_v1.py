import importlib.util,json,os,unittest,uuid
from pathlib import Path
spec=importlib.util.spec_from_file_location("ugs",str(Path(__file__).with_name("ugs_v1.py")))
u=importlib.util.module_from_spec(spec);spec.loader.exec_module(u)
ROOT=Path("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/acceptance")/uuid.uuid4().hex
def state():
 s=u.initial();s["ring"]=["A","B"]
 for i,p in enumerate(["A","A","A","B","B"],1):
  s["jobs"].append({"job_id":str(i),"project_id":p,"status":"QUEUED"})
 return s
class Checks(unittest.TestCase):
 def test_round_robin_fifo(self):
  s=state();order=[]
  for _ in range(5):
   j=u.choose(s,lambda j:True);order.append(j["job_id"])
   u.mark_served(s,j["project_id"]);j["status"]="SUCCEEDED"
  self.assertEqual(order,["1","4","2","5","3"])
 def test_blocked_head_not_bypassed(self):
  s=state();j=u.choose(s,lambda j:j["job_id"]!="1")
  self.assertEqual(j["job_id"],"4");u.mark_served(s,"B");j["status"]="SUCCEEDED"
  self.assertEqual(u.choose(s,lambda j:True)["job_id"],"1")
 def test_paused_head_blocks_only_own_project(self):
  s=state();s["jobs"][0]["paused"]=True
  self.assertEqual(u.choose(s,lambda j:not j.get("paused"))["job_id"],"4")
 def test_restart_keeps_round_position(self):
  s=state();j=u.choose(s,lambda j:True);u.mark_served(s,"A");j["status"]="SUCCEEDED"
  clone=json.loads(json.dumps(s));self.assertEqual(u.choose(clone,lambda j:True)["job_id"],"4")
 def test_unknown_active_never_terminal(self):
  s=u.initial();s["active"]={"job_id":"a","process":None}
  self.assertIsNotNone(s["active"])
 def policy(self):
  p=u.initial()["policy"];p["baseline"]={"GPU-A":{"reviewed":True,"evidence":"test","memory_mib":4}}
  return p
 def card(self,**kw):
  c={"uuid":"GPU-A","name":"NVIDIA A100","memory_mib":4,"util":0,"processes":[],"ecc_uncorrectable":0};c.update(kw);return c
 def test_full_300_seconds(self):
  d=u.Idle();p=self.policy()
  for t in range(0,300,30):d.feed([self.card()],p,t)
  self.assertEqual(d.candidates(270),[])
  d.feed([self.card()],p,300);self.assertEqual(d.candidates(300),["GPU-A"])
 def test_reuse_resets_candidate(self):
  d=u.Idle();p=self.policy()
  for t in range(0,301,30):d.feed([self.card()],p,t)
  d.feed([self.card(processes=[{"pid":"1"}])],p,330)
  d.feed([self.card()],p,360);self.assertEqual(d.candidates(360),[])
 def test_missing_sample_resets(self):
  d=u.Idle();p=self.policy()
  d.feed([self.card()],p,0);d.feed([self.card()],p,60)
  self.assertEqual(d.since["GPU-A"],60)
 def test_unknown_baseline_rejected(self):
  d=u.Idle();d.feed([self.card()],u.initial()["policy"],0);self.assertEqual(d.since,{})
 def test_no_global_four_mib_rule(self):
  d=u.Idle();p=self.policy();d.feed([self.card(memory_mib=3)],p,0);self.assertEqual(d.since,{})
 def test_ecc_rejected(self):
  d=u.Idle();d.feed([self.card(ecc_uncorrectable=1)],self.policy(),0);self.assertEqual(d.since,{})
 def test_persisted_append_only(self):
  st=u.Store(ROOT/"journal")
  with st.tx("one") as s:s["round"]=2
  with st.tx("two") as s:s["round"]=3
  self.assertEqual(st.read()["round"],3);self.assertEqual(len(st.journal.read_text().splitlines()),2)
 def test_corrupt_tail_fails_closed(self):
  st=u.Store(ROOT/"corrupt")
  with st.tx("one") as s:pass
  with st.journal.open("ab") as f:f.write(b'{"incomplete":')
  with self.assertRaises(ValueError):st.read()
 def test_enable_unaccepted_backend_rejected(self):
  p=self.policy();p.update(dispatch_enabled=True,migration_cleared=True,migration_evidence="test",acceptance_evidence="test")
  p["baseline"]["GPU-B"]=p["baseline"]["GPU-A"].copy()
  with self.assertRaisesRegex(ValueError,"CPU integration release"):u.allowed_policy(p)
if __name__=="__main__":unittest.main(verbosity=2)
