
"""CPU-only v3 tests. All fixtures are retained; production state and GPUs are untouched."""
import os,sys,time,json,copy,uuid,unittest,subprocess,contextlib,io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import ugs_exec_v3_draft1 as u
SOURCE=Path(u.__file__);SERVICE=u.ROOT;BASE_STORE=u.Store
FIX=SERVICE/"acceptance"/("cpu-v3-"+uuid.uuid4().hex);FIX.mkdir()
def reset():
    root=FIX/uuid.uuid4().hex;root.mkdir()
    u.ROOT=root;u.PROTOCOL_FILE=root/"protocol-v2.md";u.PROTOCOL_FILE.write_text("isolated CPU protocol fixture")
    u.SELF=SOURCE;u.Store=lambda:BASE_STORE(root);u.gpu_contexts_gone=lambda a:True
    proof=root/"cpu-release.json";u.fresh_json(proof,{"passed":True,"fixture_only":True})
    with u.Store().tx("fixture_init") as s:
        s["policy"].update(dispatch_enabled=True,migration_cleared=True,max_experiment=3)
        s["executor_release"]={"version":u.VERSION,"cpu_accepted":True,"files":{str(SOURCE):u.hash_file(SOURCE)},
            "cpu_evidence":{"path":str(proof),"sha256":u.hash_file(proof)}}
        s["daemon"]={"version":u.VERSION,"identity":u.identity(os.getpid()),"monotonic":time.monotonic()-400}
    return root
def project(root,name="A"):
    r=root/name;r.mkdir(exist_ok=True)
    p={"project_id":name,"project_root":str(r),"allowed_roots":[str(r)],"python":sys.executable,
       "owner":"CPU fixture","adapter_contract":"ugs-stage-v2"}
    ev=r/"migration.json";u.fresh_json(ev,{"old_dispatch_disabled":True,"existing_jobs_reconciled":True})
    p["migration_evidence"]={"path":str(ev),"sha256":u.hash_file(ev)}
    with u.Store().tx("fixture_register") as s:u.register(s,p)
    return p
def manifest(p,name="one"):
    root=Path(p["project_root"])/name;root.mkdir()
    entry=root/"entry.py";entry.write_text("pass\n")
    report=root/"cpu.json"
    profile={"profile_id":"fixture","gpu_count":2,"gpu_model":"A100","precision":"bf16","execution":"ddp",
      "world_size":2,"host_start_mib":1,"host_running_mib":1,"disk_min_gib":1,"acceptance_ref":str(report)}
    stage={"stage_id":"one","kind":"interface","argv":[sys.executable,"-B",str(entry)],"cwd":str(root),
      "output_dir":str(root),"evaluation_dir":str(root),"log_path":str(root/"log"),"result_path":str(root/"result.json"),
      "source_sha256":{str(entry):u.hash_file(entry)},"resource_profiles":[profile],"adapter_accepted":False,"requires_previous_gate":False}
    u.fresh_json(report,{"contract":"ugs-cpu-admission-v1","passed":True,"automatic_retry":False,
      "checks":[{"name":"CPU fixture only","passed":True}],"source_sha256":stage["source_sha256"],"resource_profiles":[profile]})
    m={"project_id":p["project_id"],"experiment_id":name,"submission_version":"1","protocol":u.PROTOCOL,
       "protocol_sha256":u.hash_file(u.PROTOCOL_FILE),"stages":[stage],"admission":{"contract":"ugs-stage-v2","automatic_retry":False,
       "stages":{"one":{"scope":"bounded_interface","cpu":{"path":str(report),"sha256":u.hash_file(report)},"interface_evidence":[]}}}}
    return m
def submit(m):
    with u.Store().tx("fixture_submit") as s:return u.submit(s,m)
def runcli(*args):
    old=sys.argv;sys.argv=["ugs"]+list(args)
    try:
        with contextlib.redirect_stdout(io.StringIO()) as output:u.cli()
        return json.loads(output.getvalue())
    finally:sys.argv=old
class Contracts(unittest.TestCase):
    def setUp(self):self.root=reset();self.p=project(self.root)
    def test_default_one(self):self.assertEqual(u.initial()["policy"]["max_experiment"],1)
    def test_self_registration_admission_and_duplicate(self):
        m=manifest(self.p);a=submit(m);b=submit(m)
        self.assertEqual(a["job_id"],b["job_id"]);self.assertTrue(b["duplicate"])
        with u.Store().tx() as s:self.assertTrue(u.accepted_stage(s["jobs"][0]))
    def test_source_not_in_cpu_report_rejected(self):
        m=manifest(self.p);m["stages"][0]["source_sha256"][str(self.root/"other")]="bad"
        with self.assertRaises(Exception):submit(m)
    def test_failed_cpu_evidence_rejected(self):
        m=manifest(self.p);pin=m["admission"]["stages"]["one"]["cpu"];f=Path(pin["path"])
        r=json.loads(f.read_text());r["passed"]=False;f.write_text(json.dumps(r));pin["sha256"]=u.hash_file(f)
        with self.assertRaises(ValueError):submit(m)
    def test_wrong_project_and_withdrawal_replay(self):
        m=manifest(self.p);job=submit(m)["job_id"]
        with self.assertRaises(ValueError):runcli("experiment","remove",job,"--project","B")
        result=runcli("experiment","remove",job,"--project","A")
        self.assertEqual(result["status"],"WITHDRAWN");self.assertTrue(Path(m["stages"][0]["argv"][2]).exists())
        self.assertEqual(submit(m)["status"],"WITHDRAWN")
        with self.assertRaises(ValueError):runcli("experiment","resume",job,"--project","A")
    def test_typo_and_negative_limit_rejected_without_mutation(self):
        before=u.Store().read()["revision"]
        for argument in ["max_expriment=0","max_experiment=-1","max_experiment=true"]:
            with self.assertRaises(ValueError):runcli("scheduler","set",argument)
        self.assertEqual(before,u.Store().read()["revision"])
    def test_pause_resume_queue(self):
        j=submit(manifest(self.p))["job_id"]
        self.assertTrue(runcli("experiment","pause",j,"--project","A")["paused"])
        self.assertFalse(runcli("experiment","resume",j,"--project","A")["paused"])
    def test_slots_and_gpu_overlap(self):
        m=manifest(self.p);jid=submit(m)["job_id"];s=u.Store().read();j=u.get_job(s,jid)
        s["active_runs"]["other"]={"job_id":"other","gpus":["GPU-a"]}
        self.assertFalse(u.launch_valid(s,j,j["manifest_sha256"],["GPU-a","GPU-b"]))
        self.assertTrue(u.launch_valid(s,j,j["manifest_sha256"],["GPU-c","GPU-d"]))
        s["policy"]["max_experiment"]=1
        self.assertFalse(u.launch_valid(s,j,j["manifest_sha256"],["GPU-c","GPU-d"]))
        s["policy"]["max_experiment"]=0
        self.assertFalse(u.launch_valid(s,j,j["manifest_sha256"],["GPU-c","GPU-d"]))
    def test_fifo_and_project_rotation(self):
        b=project(self.root,"B")
        for p,n in [(self.p,"a1"),(self.p,"a2"),(b,"b1")]:submit(manifest(p,n))
        s=u.Store().read();j=u.choose(s,lambda x:True);self.assertEqual(j["experiment_id"],"a1")
        u.mark_served(s,"A");s["active_runs"][j["job_id"]]={"job_id":j["job_id"],"gpus":[]}
        j=u.choose(s,lambda x:True);self.assertEqual(j["experiment_id"],"b1")
        u.mark_served(s,"B");s["active_runs"][j["job_id"]]={"job_id":j["job_id"],"gpus":[]}
        self.assertEqual(u.choose(s,lambda x:True)["experiment_id"],"a2")
    def test_blocked_head_not_skipped(self):
        b=project(self.root,"B")
        for p,n in [(self.p,"a1"),(self.p,"a2"),(b,"b1")]:submit(manifest(p,n))
        s=u.Store().read();s["jobs"][0]["paused"]=True
        self.assertEqual(u.choose(s,lambda j:not j["paused"])["experiment_id"],"b1")
    def test_exact_identity_protects_unrelated_process(self):
        child=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(20)"])
        record=u.identity(child.pid)
        try:
            wrong=dict(record,start="wrong")
            self.assertFalse(u.exact_signal(wrong,9));self.assertIsNone(child.poll())
        finally:u.exact_signal(record,9);child.wait(timeout=5)

def bootstrap(root,cards):
    return ("import sys;from pathlib import Path;sys.path.insert(0,%r);import ugs_exec_v3_draft1 as u;"
      "B=u.Store;u.Store=lambda:B(Path(%r));u.ROOT=Path(%r);u.SELF=Path(%r);"
      "u.PROTOCOL_FILE=u.ROOT/'protocol-v2.md';u.scan_devices=lambda:%r;u.gpu_contexts_gone=lambda a:True;"
      %(str(SOURCE.parent),str(root),str(root),str(SOURCE),cards))
class RealProcesses(unittest.TestCase):
    def setUp(self):
        self.root=reset();self.p=project(self.root);self.other=project(self.root,"B");self.children=[]
        self.cards=[{"uuid":"GPU-fixture-"+str(i),"name":"NVIDIA A100","memory_mib":4,"util":0,
          "processes":[],"ecc_uncorrectable":0,"health_ok":True} for i in range(8)]
        self.boot=bootstrap(self.root,self.cards)
        with u.Store().tx("fixture_baselines") as s:
            s["policy"]["baseline"]={c["uuid"]:{"memory_mib":4,"reviewed":True,"evidence":"CPU fixture"} for c in self.cards}
        start=time.monotonic()-330
        with (self.root/"resources.jsonl").open("x") as f:
            for i in range(12):f.write(json.dumps({"monotonic":start+30*i,"daemon_pid":os.getpid(),"boot":u.BOOT,"cards":self.cards})+"\n")
    def spawn(self,p,name,indices,long=True,revoked=False):
        m=manifest(p,name);t=m["stages"][0];entry=Path(t["argv"][2])
        initial=self.boot+"code,r=u.verify_launch();print(__import__('json').dumps(r));sys.exit(code)"
        preload=self.boot+"code,r=u.verify_launch(preload=True);print(__import__('json').dumps(r));sys.exit(code)"
        source="""import os,sys,json,subprocess,time,hashlib
from pathlib import Path
for code in [%r,%r]:
 r=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
 assert r.returncode==0,(r.stdout,r.stderr)
assert json.loads(r.stdout)['preload'] is True
Path(%r).write_text('ready')
child=os.fork()
if child==0:
 os.setsid();time.sleep(%r);os._exit(0)
if %r:
 while True:time.sleep(1)
artifact=Path(%r);artifact.write_text('CPU fixture output')
receipt={'run_id':os.environ['UGS_RUN_ID'],'stage_id':os.environ['UGS_STAGE_ID'],'stage_nonce':os.environ['UGS_STAGE_NONCE'],'outcome':'success','artifacts_verified':True,'gate_passed':True,'artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
with open(os.environ['UGS_RESULT_PATH'],'x') as f:json.dump(receipt,f)
"""%(initial,preload,str(entry.parent/"READY"),60 if long else .25,long,str(entry.parent/"artifact"))
        entry.write_text(source);t["source_sha256"][str(entry)]=u.hash_file(entry)
        pin=m["admission"]["stages"]["one"]["cpu"];report=json.loads(Path(pin["path"]).read_text())
        report["source_sha256"]=t["source_sha256"];Path(pin["path"]).write_text(json.dumps(report));pin["sha256"]=u.hash_file(pin["path"])
        jid=submit(m)["job_id"]
        with u.Store().tx("fixture_launch") as s:
            j=u.get_job(s,jid);j["status"]="STARTING";j["first_started_at"]=u.stamp()
            a={"job_id":jid,"run_id":j["run_id"],"stage_id":"one","stage_nonce":uuid.uuid4().hex,"generation":uuid.uuid4().hex,
               "manifest_sha256":j["manifest_sha256"],"boot":u.BOOT,"gpus":[self.cards[i]["uuid"] for i in indices],
               "profile":t["resource_profiles"][0],"guardian":None,"process":None,"grant_deadline":time.monotonic()+15,
               "prepare_deadline":time.monotonic()+900}
            s["active_runs"][jid]=a
            child=subprocess.Popen([sys.executable,"-B","-c",self.boot+"u.guardian(%r)"%a["stage_nonce"]],
              stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            self.children.append(child);a["guardian_spawn"]=u.identity(child.pid)
            if revoked:s["policy"]["max_experiment"]=0;u.request_stop(s,j,"fixture revoked before spawn")
        return jid,entry.parent
    def wait_ready(self,folder):
        deadline=time.monotonic()+12
        while not (folder/"READY").exists() and time.monotonic()<deadline:time.sleep(.1)
        self.assertTrue((folder/"READY").exists(),(folder/"log").read_text() if (folder/"log").exists() else str(folder))
    def finish(self,jid):
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            for child in self.children:child.poll()
            u.finalize_available()
            j=u.get_job(u.Store().read(),jid)
            if j["status"] in u.TERMINAL:return j
            time.sleep(.1)
        self.fail("fixture did not terminate "+str(u.Store().read()["active_runs"]))
    def tearDown(self):
        # Only explicitly created CPU fixture process trees are terminated.
        with u.Store().tx("fixture_cleanup_requests") as s:
            for jid in list(s["active_runs"]):u.request_stop(s,u.get_job(s,jid),"CPU fixture teardown")
        for jid in list(u.Store().read()["active_runs"]):u.stop_owned(jid)
        for child in self.children:
            try:child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                record=u.identity(child.pid)
                if record:u.exact_signal(record,9)
                child.communicate(timeout=5)
    def test_real_initial_preload_completion(self):
        jid,folder=self.spawn(self.p,"success",[0,1],False)
        j=self.finish(jid);self.assertEqual(j["status"],"SUCCEEDED");self.assertEqual(len(j["results"]),1)
    def test_hot_lower_drain_project_stop_emergency_zero_and_no_restart(self):
        a,pa=self.spawn(self.p,"a",[0,1]);self.wait_ready(pa)
        b,pb=self.spawn(self.other,"b",[2,3]);self.wait_ready(pb)
        queued=submit(manifest(self.other,"later"))["job_id"]
        external=subprocess.Popen([sys.executable,"-B","-c","import time;time.sleep(30)"]);outside=u.identity(external.pid)
        try:
            s=u.Store().read();leaders={jid:s["active_runs"][jid]["process"] for jid in [a,b]}
            u.set_limit(1)
            self.assertTrue(all(u.live(x) for x in leaders.values()));self.assertTrue(u.summary(u.Store().read())["draining"])
            with self.assertRaises(ValueError):runcli("experiment","stop",a,"--project","B")
            out=runcli("experiment","stop",a,"--project","A")
            self.assertEqual(self.finish(a)["status"],"CANCELLED");self.assertTrue(u.live(leaders[b]))
            self.assertEqual(u.Store().read()["policy"]["max_experiment"],1)
            u.set_limit(0);self.assertEqual(self.finish(b)["status"],"CANCELLED")
            self.assertIsNone(external.poll());self.assertEqual(u.get_job(u.Store().read(),queued)["status"],"QUEUED")
            u.set_limit(3);s=u.Store().read();self.assertFalse(s["active_runs"])
            self.assertEqual([u.get_job(s,j)["status"] for j in [a,b]],["CANCELLED","CANCELLED"])
            self.assertEqual(len(s["jobs"]),3)
        finally:u.exact_signal(outside,9);external.wait(timeout=5)
    def test_killed_adapter_descendants_reaped_without_retry(self):
        jid,folder=self.spawn(self.p,"killed",[0,1]);self.wait_ready(folder)
        a=u.Store().read()["active_runs"][jid];descendants=u.process_tree(a["guardian"])
        self.assertGreaterEqual(len(descendants),2)
        u.exact_signal(a["process"],9)
        j=self.finish(jid);self.assertEqual(j["status"],"FAILED")
        self.assertTrue(all(not u.live(x) for x in descendants));self.assertEqual(len(u.Store().read()["jobs"]),1)
    def test_zero_between_intent_and_guardian_prevents_adapter(self):
        jid,folder=self.spawn(self.p,"revoked",[0,1],revoked=True)
        self.assertEqual(self.finish(jid)["status"],"CANCELLED");self.assertFalse((folder/"READY").exists())
if __name__=="__main__":
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    report={"passed":result.wasSuccessful(),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
      "production_state_changed":False,"cuda_used":False,"fixture_root":str(FIX),"source_sha256":u.hash_file(SOURCE),"at":u.stamp()}
    u.fresh_json(FIX/"result.json",report);print(json.dumps(report));sys.exit(0 if result.wasSuccessful() else 1)
