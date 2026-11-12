
"""CPU-only v3 tests. All fixtures are retained; production state and GPUs are untouched."""
import os,sys,time,json,copy,uuid,unittest,subprocess,contextlib,io
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import ugs_exec_v3_with_stop as u
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
    return ("import sys;from pathlib import Path;sys.path.insert(0,%r);import ugs_exec_v3_with_stop as u;"
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
    def test_hot_lower_drain_zero_non_killing_stop_all_no_restart(self):
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
            u.set_limit(0);self.assertTrue(u.live(leaders[b]))
            runcli("experiment","stop","--all")
            self.assertEqual(self.finish(b)["status"],"CANCELLED")
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
    def test_explicit_stop_between_intent_and_guardian_prevents_adapter(self):
        jid,folder=self.spawn(self.p,"revoked",[0,1],revoked=True)
        self.assertEqual(self.finish(jid)["status"],"CANCELLED");self.assertFalse((folder/"READY").exists())

class ExtendedContracts(Contracts):
    def test_project_pause_race(self):
        jid=submit(manifest(self.p))["job_id"];s=u.Store().read();s["projects"]["A"]["paused"]=True
        self.assertFalse(u.launch_valid(s,u.get_job(s,jid),u.get_job(s,jid)["manifest_sha256"],["one","two"]))
    def test_cas_update_and_old_submission_replay(self):
        old=manifest(self.p);jid=submit(old)["job_id"];new=copy.deepcopy(old);new["submission_version"]="2"
        with u.Store().tx("update") as s:
            j=u.get_job(s,jid)
            with self.assertRaises(ValueError):u.update_manifest(s,j,new,"wrong")
            u.update_manifest(s,j,new,j["manifest_sha256"])
        self.assertEqual(submit(old)["job_id"],jid)
        self.assertEqual(u.get_job(u.Store().read(),jid)["queue_seq"],1)
    def test_future_amend_keeps_current_prefix(self):
        m=manifest(self.p);future=manifest(self.p,"future")
        future["stages"][0]["stage_id"]="test";future["stages"][0]["kind"]="test"
        m["stages"]+=future["stages"];m["admission"]["stages"]["test"]=future["admission"]["stages"]["one"]
        jid=submit(m)["job_id"];new=copy.deepcopy(m);new["submission_version"]="2"
        with u.Store().tx("active_amend") as s:
            j=u.get_job(s,jid);s["active_runs"][jid]={"job_id":jid,"gpus":[]}
            bad=copy.deepcopy(new);bad["stages"][0]["argv"].append("changed")
            with self.assertRaises(ValueError):u.update_manifest(s,j,bad,j["manifest_sha256"],1)
            u.update_manifest(s,j,new,j["manifest_sha256"],1)
            self.assertEqual(j["manifest"]["submission_version"],"1")
            self.assertEqual(j["pending_revision"]["manifest"]["submission_version"],"2")
            self.assertEqual(j["manifest"]["stages"][0],new["stages"][0])
    def test_migration_rejects_live_coordinator(self):
        s=u.Store().read();s.pop("schema_version");s["active"]=None
        with self.assertRaises(ValueError):u.migrate_legacy(s,s["executor_release"])

class CoordinatorLoop(RealProcesses):
    def test_actual_coordinator_multi_dispatch_and_hot_control(self):
        wrapper=self.root/"coordinator.py"
        boot=self.boot.replace("u.SELF=Path(%r)"%str(SOURCE),"u.SELF=Path(%r)"%str(wrapper))
        script=boot+"""
import contextlib
Original=u.Store
class FixtureStore:
 def __init__(self):self.inner=Original()
 @contextlib.contextmanager
 def tx(self,event=None):
  with self.inner.tx(event) as s:
   yield s
   if event=='executor_start':
    s['daemon']['monotonic']=__import__('time').monotonic()-400
    start=__import__('time').monotonic()-330
    with (u.ROOT/'resources.jsonl').open('a') as f:
     for i in range(12):f.write(__import__('json').dumps({'monotonic':start+30*i,'daemon_pid':__import__('os').getpid(),'boot':u.BOOT,'cards':u.scan_devices()})+'\\n')
u.Store=FixtureStore
BaseIdle=u.Idle
class FixtureIdle(BaseIdle):
 def candidates(self,now,window=300):return super().candidates(now,0)
u.Idle=FixtureIdle
if sys.argv[1]=='verify':
 code,result=u.verify_launch(preload='--preload' in sys.argv)
 print(__import__('json').dumps(result));sys.exit(code)
else:u.cli()
"""
        wrapper.write_text(script)
        ids=[]
        for p,name in [(self.p,"a1"),(self.other,"b1"),(self.p,"a2"),(self.other,"b2")]:
            m=manifest(p,name);t=m["stages"][0];entry=Path(t["argv"][2])
            entry.write_text("""import subprocess,sys,time,os
from pathlib import Path
for flag in [[],['--preload']]:
 subprocess.run([sys.executable,'-B',%r,'verify']+flag,check=True)
Path(%r).write_text('ready')
if os.fork()==0:
 os.setsid()
 while True:time.sleep(1)
while True:time.sleep(1)
"""%(str(wrapper),str(entry.parent/"READY")))
            t["source_sha256"][str(entry)]=u.hash_file(entry)
            pin=m["admission"]["stages"]["one"]["cpu"];r=json.loads(Path(pin["path"]).read_text())
            r["source_sha256"]=t["source_sha256"];Path(pin["path"]).write_text(json.dumps(r));pin["sha256"]=u.hash_file(pin["path"])
            ids.append(submit(m)["job_id"])
        with (self.root/"coordinator.log").open("ab") as log:
            coordinator=subprocess.Popen([sys.executable,"-B",str(wrapper),"internal","serve"],stdout=log,stderr=log)
        record=u.identity(coordinator.pid)
        def until(predicate,seconds=30):
            end=time.monotonic()+seconds
            while time.monotonic()<end:
                s=u.Store().read()
                if predicate(s):return s
                if coordinator.poll() is not None:self.fail((self.root/"coordinator.log").read_text())
                time.sleep(.2)
            self.fail(str(u.summary(u.Store().read()))+" "+(self.root/"service.log").read_text())
        try:
            s=until(lambda s:len([a for a in s["active_runs"].values() if a.get("preload_authorized_at")])==3)
            allocations=[set(a["gpus"]) for a in s["active_runs"].values()]
            self.assertEqual(len(set.union(*allocations)),6)
            self.assertEqual(set(s["active_runs"]),set(ids[:3]))
            with self.assertRaises(ValueError):runcli("scheduler","stop")
            u.set_limit(1);time.sleep(1)
            self.assertEqual(len(u.Store().read()["active_runs"]),3)
            runcli("experiment","stop",ids[0],"--project","A")
            until(lambda s:len(s["active_runs"])==2)
            runcli("experiment","stop",ids[1],"--project","B")
            until(lambda s:len(s["active_runs"])==1)
            self.assertEqual(u.get_job(u.Store().read(),ids[3])["status"],"QUEUED")
            u.set_limit(0);self.assertTrue(u.Store().read()["active_runs"])
            runcli("scheduler","pause","--stop-running");until(lambda s:not s["active_runs"])
            self.assertIsNone(coordinator.poll())
            u.set_limit(1);runcli("scheduler","resume")
            until(lambda s:s["active_runs"].get(ids[3],{}).get("preload_authorized_at"))
            self.assertEqual([u.get_job(u.Store().read(),j)["status"] for j in ids[:3]],["CANCELLED"]*3)
            result=runcli("scheduler","stop","--stop-running")
            self.assertTrue(result["daemon_stopped"]);coordinator.wait(timeout=5)
            self.assertEqual(u.Store().read()["policy"]["max_experiment"],1)
            restarted=subprocess.run([sys.executable,"-B",str(wrapper),"scheduler","start"],capture_output=True,text=True)
            self.assertEqual(restarted.returncode,0,restarted.stderr)
            state=json.loads(restarted.stdout);self.assertTrue(state["daemon_alive"])
            self.assertTrue(state["scheduler_paused"]);self.assertEqual(state["max_experiment"],1)
            self.assertTrue(runcli("scheduler","stop")["daemon_stopped"])
        finally:
            runcli("scheduler","pause","--stop-running")
            end=time.monotonic()+10
            while u.Store().read()["active_runs"] and time.monotonic()<end:
                u.finalize_available();time.sleep(.2)
            u.exact_signal(record,15);coordinator.wait(timeout=5)


class LegacyAdoption(unittest.TestCase):
 def test_actual_v23_guardian_adopted_without_worker_restart(self):
    root=reset();p=project(root);m=manifest(p);stage=m["stages"][0];entry=Path(stage["argv"][2])
    cards=[{"uuid":"GPU-legacy-"+str(i),"name":"NVIDIA A100","memory_mib":4,"util":0,"processes":[],"ecc_uncorrectable":0,"health_ok":True} for i in range(2)]
    oldboot=bootstrap(root,cards).replace("ugs_exec_v3_with_stop","ugs_exec_v2_3")
    initial=oldboot+"code,r=u.verify_launch();print(__import__('json').dumps(r));sys.exit(code)"
    preload=oldboot+"code,r=u.verify_launch(preload=True);print(__import__('json').dumps(r));sys.exit(code)"
    entry.write_text("""import os,sys,json,subprocess,time,hashlib
from pathlib import Path
for code in [%r,%r]:
 r=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,text=True)
 assert r.returncode==0,(r.stdout,r.stderr)
Path(%r).write_text('ready')
while not Path(%r).exists():time.sleep(.1)
if os.fork()==0:
 os.setsid();time.sleep(.2);os._exit(0)
artifact=Path(%r);artifact.write_text('legacy CPU fixture')
receipt={'run_id':os.environ['UGS_RUN_ID'],'stage_id':os.environ['UGS_STAGE_ID'],'stage_nonce':os.environ['UGS_STAGE_NONCE'],'outcome':'success','artifacts_verified':True,'gate_passed':True,'artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
with open(os.environ['UGS_RESULT_PATH'],'x') as f:json.dump(receipt,f)
"""%(initial,preload,str(root/"READY"),str(root/"FINISH"),str(entry.parent/"artifact")))
    stage["source_sha256"][str(entry)]=u.hash_file(entry)
    cp=m["admission"]["stages"]["one"]["cpu"];r=json.loads(Path(cp["path"]).read_text())
    r["source_sha256"]=stage["source_sha256"];Path(cp["path"]).write_text(json.dumps(r));cp["sha256"]=u.hash_file(cp["path"])
    jid=submit(m)["job_id"]
    with u.Store().tx("legacy_fixture") as s:
        newrelease=copy.deepcopy(s["executor_release"]);s.pop("schema_version");s.pop("active_runs")
        s["policy"]["baseline"]={c["uuid"]:{"reviewed":True,"memory_mib":4,"evidence":"CPU fixture"} for c in cards}
        s["executor_release"]["version"]="ugs-exec-v2.3"
        j=u.get_job(s,jid)
        s["active"]={"job_id":jid,"run_id":j["run_id"],"stage_id":"one","stage_nonce":uuid.uuid4().hex,
          "generation":uuid.uuid4().hex,"manifest_sha256":j["manifest_sha256"],"boot":u.BOOT,
          "gpus":[c["uuid"] for c in cards],"profile":stage["resource_profiles"][0],"guardian":None,"process":None,
          "grant_deadline":time.monotonic()+15,"prepare_deadline":time.monotonic()+900,"intent_at":u.stamp()}
    supervisor=root/"legacy_supervisor.py"
    supervisor.write_text(oldboot+"""
import os,time,json,subprocess
with u.Store().tx('old_coordinator') as s:
 s['daemon']={'version':u.VERSION,'identity':u.identity(os.getpid()),'monotonic':time.monotonic()-400}
 nonce=s['active']['stage_nonce']
start=time.monotonic()-330
with (u.ROOT/'resources.jsonl').open('a') as f:
 for i in range(12):f.write(json.dumps({'monotonic':start+30*i,'daemon_pid':os.getpid(),'boot':u.BOOT,'cards':u.scan_devices()})+'\\n')
child=subprocess.Popen([sys.executable,'-B','-c',%r+'u.guardian('+repr(nonce)+')'],start_new_session=True)
while True:time.sleep(1)
"""%oldboot)
    with (root/"legacy_supervisor.log").open("ab") as log:
        supervisor_process=subprocess.Popen([sys.executable,"-B",str(supervisor)],stdout=log,stderr=log)
    supervisor_id=u.identity(supervisor_process.pid)
    try:
        end=time.monotonic()+15
        while not (root/"READY").exists() and time.monotonic()<end:time.sleep(.1)
        self.assertTrue((root/"READY").exists(),Path(stage["log_path"]).read_text())
        before=u.Store().read()["active"];self.assertTrue(before["preload_authorized_at"])
        u.exact_signal(supervisor_id,15);supervisor_process.wait(timeout=5)
        with u.Store().tx("adopt") as s:
            report=u.migrate_legacy(s,newrelease)
        self.assertEqual(report["restarted_experiments"],[])
        self.assertTrue(u.live(before["process"]));self.assertTrue(u.live(before["guardian"]))
        self.assertEqual(u.Store().read()["active_runs"][jid]["process"],before["process"])
        (root/"FINISH").write_text("continue")
        end=time.monotonic()+10
        while time.monotonic()<end:
            u.finalize_available()
            if u.get_job(u.Store().read(),jid)["status"] in u.TERMINAL:break
            time.sleep(.1)
        self.assertEqual(u.get_job(u.Store().read(),jid)["status"],"SUCCEEDED")
    finally:
        if supervisor_process.poll() is None:u.exact_signal(supervisor_id,15);supervisor_process.wait(timeout=5)
        with u.Store().tx("fixture_cleanup") as s:
            s.setdefault("active_runs",{})
            if s.get("active"):s["active_runs"][jid]=s["active"];s["active"]=None
            if jid in s["active_runs"]:u.request_stop(s,u.get_job(s,jid),"fixture cleanup")
        u.stop_owned(jid)


class ManagerOperations(unittest.TestCase):
 def setUp(self):self.root=reset();self.a=project(self.root);self.b=project(self.root,"B")
 def test_list_all_projects_ids_and_filters(self):
    a=submit(manifest(self.a,"one"))["job_id"];b=submit(manifest(self.b,"two"))["job_id"]
    runcli("experiment","remove",a,"--project","A")
    rows=runcli("experiment","list");self.assertEqual({x["job_id"] for x in rows},{a,b})
    self.assertEqual({x["project_id"] for x in rows},{"A","B"});self.assertTrue(all("stage_id" in x for x in rows))
    self.assertEqual([x["job_id"] for x in runcli("experiment","list","--project","A","--status","WITHDRAWN")],[a])
 def test_bulk_remove_is_project_scoped(self):
    a=submit(manifest(self.a,"one"))["job_id"];b=submit(manifest(self.b,"two"))["job_id"]
    with self.assertRaises(ValueError):runcli("experiment","remove","--all")
    runcli("experiment","remove","--all","--project","A")
    self.assertEqual(u.get_job(u.Store().read(),a)["status"],"WITHDRAWN")
    self.assertEqual(u.get_job(u.Store().read(),b)["status"],"QUEUED")
 def test_zero_drain_continuation_pause_separate(self):
    jid=submit(manifest(self.a))["job_id"];s=u.Store().read()
    s["active_runs"][jid]={"job_id":jid,"continuation":True};s["policy"]["max_experiment"]=0
    self.assertTrue(u.slot_available(s,jid));self.assertFalse(u.slot_available(s))
    self.assertTrue(u.eligible(s));s["policy"]["scheduler_paused"]=True;self.assertFalse(u.eligible(s))
    runcli("scheduler","set","max_experiment=0");runcli("scheduler","pause");runcli("scheduler","resume")
    self.assertEqual(u.Store().read()["policy"]["max_experiment"],0)
 def test_bulk_flag_requires_explicit_supported_action(self):
    for args in [("experiment","stop","job-000001","--all"),("experiment","submit","--all"),("scheduler","set","max_experiment=1","--stop-running")]:
        with self.assertRaises(ValueError):runcli(*args)

if __name__=="__main__":
    suite=unittest.TestSuite()
    for cls in [Contracts,ExtendedContracts,RealProcesses,CoordinatorLoop,LegacyAdoption,ManagerOperations]:
        for name in cls.__dict__:
            if name.startswith('test_'):suite.addTest(cls(name))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={"passed":result.wasSuccessful(),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
      "production_state_changed":False,"cuda_used":False,"fixture_root":str(FIX),"source_sha256":u.hash_file(SOURCE),"at":u.stamp()}
    u.fresh_json(FIX/"result.json",report);print(json.dumps(report));sys.exit(0 if result.wasSuccessful() else 1)
