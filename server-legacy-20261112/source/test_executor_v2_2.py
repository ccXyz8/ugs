"""CPU-only executable contract tests; persistent fixtures, no CUDA or deletion."""
import copy,json,os,subprocess,sys,time,unittest,uuid,multiprocessing
from pathlib import Path
import ugs_exec_v2_2 as u
FIX=u.ROOT/"acceptance"/("cpu-v22-fixtures-"+uuid.uuid4().hex)
FIX.mkdir()
def tx_writer(root):
    for i in range(5):
        with u.Store(root).tx("concurrent") as s:s["counter"]=s.get("counter",0)+1
class Authorization(unittest.TestCase):
    def setUp(self):
        self.d={"pid":1,"start":"10","boot":u.BOOT,"ppid":0,"session":1,"state":"S","uid":os.getuid()}
        self.g={**self.d,"pid":2,"ppid":1,"session":2,"start":"20"}
        self.w={**self.d,"pid":3,"ppid":2,"session":2,"start":"30"}
        self.a={"job_id":"j","run_id":"r","stage_id":"v","stage_nonce":"n","manifest_sha256":"h","generation":"g",
          "boot":u.BOOT,"grant_deadline":200,"gpus":["GPU-a","GPU-b"],"profile":{"gpu_count":2},"process":self.w,"guardian":self.g}
        self.s={"active":self.a,"recovery":None,"policy":{"dispatch_enabled":True,"migration_cleared":True},
          "daemon":{"version":u.VERSION,"identity":self.d},
          "jobs":[{"job_id":"j","manifest_sha256":"h","stage_index":0,"status":"RUNNING","manifest":{"stages":[{"stage_id":"v","result_path":"/project/result"}]}}]}
        self.e=u.expected_env(self.s["jobs"][0],self.a)
        self.ids={1:self.d,2:self.g,3:self.w}
    def check(self):return u.verify_record(self.s,self.e,3,100,self.ids.get,lambda s:True)
    def test_valid_bound_adapter(self):
        code,r=self.check();self.assertEqual(code,0);self.assertEqual(r["gpus"],["GPU-a","GPU-b"])
    def test_forged_environment_other_pid(self):
        self.ids[3]={**self.w,"start":"999"};self.assertEqual(self.check()[0],2)
    def test_nonce_replay(self):
        self.e["UGS_STAGE_NONCE"]="old";self.assertEqual(self.check()[0],2)
    def test_mapping_order(self):
        self.e["CUDA_VISIBLE_DEVICES"]="GPU-b,GPU-a";self.assertEqual(self.check()[0],2)
    def test_dead_guardian(self):
        self.ids[2]={**self.g,"state":"Z"};self.assertEqual(self.check()[0],2)
    def test_parent_mismatch(self):
        self.ids[3]={**self.w,"ppid":19};self.assertEqual(self.check()[0],2)
    def test_pending(self):
        self.a["process"]=None;self.assertEqual(self.check()[0],3)
    def test_boot_mismatch(self):
        self.a["boot"]="oldboot";self.assertEqual(self.check()[0],2)
    def test_expired(self):
        self.a["grant_deadline"]=99;self.assertEqual(self.check()[0],2)
    def test_recovery(self):
        self.s["recovery"]="unknown";self.assertEqual(self.check()[0],2)
    def test_terminal(self):
        self.s["jobs"][0]["status"]="SUCCEEDED";self.assertEqual(self.check()[0],2)
    def test_manifest_mutation(self):
        self.s["jobs"][0]["manifest_sha256"]="other";self.assertEqual(self.check()[0],2)
    def test_no_release(self):
        self.assertEqual(u.verify_record(self.s,self.e,3,100,self.ids.get,lambda s:False)[0],2)
class Queue(unittest.TestCase):
    def make(self):
        s=u.initial()
        for p in ("A","B"):
            s["ring"].append(p)
        for p,n in (("A",1),("A",2),("A",3),("B",1),("B",2)):
            s["jobs"].append({"job_id":p+str(n),"project_id":p,"status":"QUEUED","paused":False})
        return s
    def test_round_robin(self):
        s=self.make();order=[]
        for i in range(5):
            j=u.choose(s,lambda x:not x["paused"]);order.append(j["job_id"])
            u.mark_served(s,j["project_id"]);j["status"]="SUCCEEDED"
        self.assertEqual(order,["A1","B1","A2","B2","A3"])
    def test_blocked_head_no_internal_bypass(self):
        s=self.make();s["jobs"][0]["paused"]=True
        self.assertEqual(u.choose(s,lambda x:not x["paused"])["job_id"],"B1")
    def test_restart_cursor(self):
        s=self.make();j=u.choose(s,lambda x:True);u.mark_served(s,j["project_id"]);j["status"]="SUCCEEDED"
        rebuilt=json.loads(json.dumps(s));self.assertEqual(u.choose(rebuilt,lambda x:True)["job_id"],"B1")
    def job(self):
        m={"project_id":"A","experiment_id":"E","submission_version":"1","stages":[]}
        j={"job_id":"job","queue_seq":1,"project_id":"A","experiment_id":"E","manifest":m,
           "manifest_sha256":u.digest(u.canonical(m)),"idempotency_key":u.key_for(m),"stage_index":0,"results":[],"status":"QUEUED"}
        return {"jobs":[j],"active":None},j
    def test_cas_preserves_fifo_and_old_replay(self):
        s,j=self.job();old=copy.deepcopy(j["manifest"]);sha=j["manifest_sha256"]
        m={**old,"submission_version":"2","adapter":"new"}
        u.replace_manifest(s,"job",sha,m)
        self.assertEqual(j["queue_seq"],1);self.assertIs(u.find_submission(s,old),j)
        self.assertIs(u.find_submission(s,m),j)
        with self.assertRaises(ValueError):u.replace_manifest(s,"job",sha,{**m,"submission_version":"3"})
    def test_no_active_revision(self):
        s,j=self.job();s["active"]={"job_id":"job"}
        with self.assertRaises(ValueError):u.replace_manifest(s,"job",j["manifest_sha256"],{**j["manifest"],"submission_version":"2"})
class IdleAndStore(unittest.TestCase):
    def policy(self):
        p=u.initial()["policy"];p["baseline"]={"GPU-a":{"reviewed":True,"evidence":"test","memory_mib":4}}
        return p
    def card(self):return {"uuid":"GPU-a","name":"A100","memory_mib":4,"util":0,"processes":[],"ecc_uncorrectable":0}
    def test_common_300_seconds(self):
        x=u.Idle();p=self.policy()
        for t in range(0,271,30):x.feed([self.card()],p,t)
        self.assertEqual(x.candidates(270),[])
        x.feed([self.card()],p,300);self.assertEqual(x.candidates(300),["GPU-a"])
    def test_gap_resets(self):
        x=u.Idle();p=self.policy();x.feed([self.card()],p,0);x.feed([self.card()],p,60)
        self.assertEqual(x.candidates(300),[])
    def test_tiny_process_invalidates(self):
        x=u.Idle();p=self.policy();x.feed([self.card()],p,0);c=self.card();c["processes"]=[{"pid":"4"}]
        x.feed([c],p,30);self.assertEqual(x.since,{})
    def test_ecc_invalidates(self):
        x=u.Idle();c=self.card();c["ecc_uncorrectable"]=1;x.feed([c],self.policy(),0);self.assertEqual(x.since,{})
    def test_concurrent_append_lock(self):
        root=FIX/"concurrency"
        ps=[multiprocessing.Process(target=tx_writer,args=(root,)) for i in range(2)]
        for p in ps:p.start()
        for p in ps:p.join(10);self.assertEqual(p.exitcode,0)
        self.assertEqual(u.Store(root).read()["counter"],10)
    def test_corrupt_tail(self):
        root=FIX/"corrupt";st=u.Store(root)
        with st.tx("first") as s:s["x"]=1
        with (root/"events.jsonl").open("ab") as f:f.write(b"broken")
        with self.assertRaises(ValueError):st.read()
class Supervision(unittest.TestCase):
    def test_detached_grandchild_reaped(self):
        # Real Linux subreaper: child forks and exits; grandchild setsid then exits.
        code="""import os,sys,time,json
sys.path.insert(0,sys.argv[1])
import ugs_exec_v2_2 as u
u.enable_subreaper()
pid=os.fork()
if pid==0:
    kid=os.fork()
    if kid==0:
        os.setsid();time.sleep(.1);os._exit(7)
    os._exit(0)
exits=[]
while True:
    try:p,s=os.waitpid(-1,0);exits.append(os.waitstatus_to_exitcode(s))
    except ChildProcessError:break
print(json.dumps(sorted(exits)))
"""
        r=subprocess.run([sys.executable,"-B","-c",code,str(u.SELF.parent)],capture_output=True,text=True,timeout=10)
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(json.loads(r.stdout),[0,7])

class PreloadWindow(unittest.TestCase):
    def setUp(self):
        self.d={"monotonic":0,"identity":{"pid":100}}
        self.p={"baseline":{"GPU-a":{"memory_mib":4},"GPU-b":{"memory_mib":4}}}
        self.cards=[{"uuid":g,"memory_mib":4,"util":0,"processes":[],"ecc_uncorrectable":0,"health_ok":True} for g in self.p["baseline"]]
        self.rows=[{"monotonic":t,"daemon_pid":100,"boot":u.BOOT,"cards":copy.deepcopy(self.cards)} for t in range(0,301,30)]
    def test_complete_window(self):
        self.assertEqual(len(u.require_idle_window(self.rows,["GPU-a","GPU-b"],self.p,300,self.d)),11)
    def test_missing_sample(self):
        self.rows.pop(4)
        with self.assertRaises(ValueError):u.require_idle_window(self.rows,list(self.p["baseline"]),self.p,300,self.d)
    def test_recent_foreign_user(self):
        self.rows[-1]["cards"][0]["processes"]=[{"pid":"foreign"}]
        with self.assertRaises(ValueError):u.require_idle_window(self.rows,list(self.p["baseline"]),self.p,300,self.d)
    def test_stale_observer(self):
        with self.assertRaises(ValueError):u.require_idle_window(self.rows,list(self.p["baseline"]),self.p,336,self.d)
    def test_intervening_busy_observation(self):
        busy=copy.deepcopy(self.rows[-1]);busy["monotonic"]=301;busy["cards"][0]["processes"]=[{"pid":"short_task"}]
        fresh=copy.deepcopy(self.rows[-1]);fresh["monotonic"]=303
        merged=sorted(self.rows+[fresh,busy],key=lambda r:r["monotonic"])
        with self.assertRaises(ValueError):u.require_idle_window(merged,list(self.p["baseline"]),self.p,303,self.d)
    def test_release_preserves_untouched_candidate(self):
        idle=u.Idle();idle.since={"used":10,"untouched":0};idle.last=330
        u.release_used_devices(idle,["used"])
        self.assertEqual(idle.since,{"untouched":0})
        self.assertEqual(idle.candidates(330),["untouched"])
    def test_other_boot(self):
        for row in self.rows:row["boot"]="old"
        with self.assertRaises(ValueError):u.require_idle_window(self.rows,list(self.p["baseline"]),self.p,300,self.d)
    def test_reset_health(self):
        self.rows[-1]["cards"][1]["health_ok"]=False
        with self.assertRaises(ValueError):u.require_idle_window(self.rows,list(self.p["baseline"]),self.p,300,self.d)
class LaunchRace(unittest.TestCase):
    def test_cancel_after_selection_rejected(self):
        from unittest.mock import patch
        j={"job_id":"a","project_id":"A","status":"CANCELLED","manifest_sha256":"h"}
        s={"active":None,"jobs":[j]}
        with patch.object(u,"eligibility",return_value=True),patch.object(u,"accepted_stage",return_value=True):
            self.assertFalse(u.launch_still_eligible(s,j,j,None))
    def test_paused_after_selection_rejected(self):
        from unittest.mock import patch
        j={"job_id":"a","project_id":"A","status":"QUEUED","manifest_sha256":"h","paused":True}
        s={"active":None,"jobs":[j]}
        with patch.object(u,"eligibility",return_value=True),patch.object(u,"accepted_stage",return_value=True):
            self.assertFalse(u.launch_still_eligible(s,j,j,None))
    def test_config_blocked_head_yields_other_project(self):
        s=Queue().make();s["jobs"][0]["status"]="BLOCKED"
        self.assertEqual(u.choose(s,lambda j:j["status"]=="QUEUED")["job_id"],"B1")

if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report={"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
        "passed":result.wasSuccessful(),"cuda_used":False,"fixtures":str(FIX),
        "files":{str(u.SELF):u.hash_file(u.SELF),str(u.VERIFY):u.hash_file(u.VERIFY)},"at":u.stamp()}
    path=FIX/"result.json";u.fresh_json(path,report);print(json.dumps({"evidence":str(path),**report}))
    sys.exit(0 if result.wasSuccessful() else 1)
