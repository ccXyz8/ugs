#!/usr/bin/env python3
"""UGS runtime v3: project self-service and bounded experiment concurrency over SSH."""
import argparse,base64,contextlib,csv,fcntl,hashlib,io,json,os,signal,subprocess,sys,time,uuid
import xml.etree.ElementTree as ET
import copy,ctypes,pwd,shlex
from pathlib import Path
ROOT=Path("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler")
PYTHON="/public/home/kzhang19/kezhang/ycj/msswift_env/bin/python"
BOOT=Path("/proc/sys/kernel/random/boot_id").read_text().strip()
def digest(b): return hashlib.sha256(b).hexdigest()

def stamp(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def node_check():
    if os.uname().nodename.split(".")[0] != "gpu07":
        raise ValueError("Run this CLI on gpu07 through existing SSH, never on admin.")

def append_json(path, value):
    b = (json.dumps(value, ensure_ascii=True, sort_keys=True)+"\n").encode()
    fd = os.open(str(path), os.O_CREAT|os.O_WRONLY|os.O_APPEND, 0o600)
    try:
        n = os.write(fd,b)
        if n != len(b): raise OSError("Short append; recovery required")
        os.fsync(fd)
    finally: os.close(fd)

class Store:
    def __init__(self, root=ROOT):
        self.root=Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock=self.root/"state.lock"
        self.journal=self.root/"events.jsonl"
    def read(self):
        state=initial()
        if not self.journal.exists(): return state
        for line in self.journal.read_bytes().splitlines(keepends=True):
            if not line.endswith(b"\n"): raise ValueError("Incomplete journal tail; fail closed, do not remove it.")
            rec=json.loads(line)
            data=json.dumps(rec["state"],sort_keys=True,separators=(",",":")).encode()
            if digest(data)!=rec["sha256"] or rec["state"]["revision"]!=state["revision"]+1:
                raise ValueError("Journal integrity error; dispatch blocked")
            state=rec["state"]
        return state
    @contextlib.contextmanager
    def tx(self, event=None):
        with self.lock.open("a+b") as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            s=self.read()
            yield s
            if event:
                s["revision"]+=1
                data=json.dumps(s,sort_keys=True,separators=(",",":")).encode()
                append_json(self.journal,{"at":stamp(),"event":event,"sha256":digest(data),"state":s})

def exact_path(x):
    p=Path(x)
    if not p.is_absolute(): raise ValueError("Absolute paths required")
    return p.resolve()

def inside(path, roots):
    p=exact_path(path)
    return any(p==exact_path(r) or exact_path(r) in p.parents for r in roots)

def project_validate(p):
    for key in ("project_id","project_root","allowed_roots","python","owner","adapter_contract"):
        if not p.get(key): raise ValueError("Missing project field "+key)
    if p["adapter_contract"] not in ("ugs-stage-v1","ugs-stage-v2"): raise ValueError("Unsupported adapter contract")
    roots=p["allowed_roots"]
    if not isinstance(roots,list) or not roots: raise ValueError("allowed_roots must be a list")
    root=exact_path(p["project_root"])
    if not root.is_dir(): raise ValueError("Project root does not exist")
    if not inside(root,roots): raise ValueError("Project root outside allowed roots")
    if any(inside(ROOT,[r]) or exact_path(r)==Path("/") for r in roots):
        raise ValueError("Project roots must not include service directory or filesystem root")
    for r in roots:
        if not exact_path(r).is_dir(): raise ValueError("Allowed root missing")
    if not exact_path(p["python"]).is_file(): raise ValueError("Interpreter missing")
    return p

def get_job(s,jid):
    return next(j for j in s["jobs"] if j["job_id"]==jid)

def _job_validate(m,p):
    for k in ("project_id","experiment_id","submission_version","protocol","protocol_sha256","stages"):
        if not m.get(k): raise ValueError("Missing manifest field "+k)
    if m["protocol"]!=PROTOCOL: raise ValueError("Protocol version mismatch")
    if m["protocol_sha256"]!=digest(PROTOCOL_FILE.read_bytes()):
        raise ValueError("Protocol SHA mismatch")
    if m["project_id"]!=p["project_id"]: raise ValueError("Project mismatch")
    stages=m["stages"]
    if not isinstance(stages,list) or not 1<=len(stages)<=32:
        raise ValueError("Finite stages (1..32) required")
    ids=set()
    for i,t in enumerate(stages):
        for k in ("stage_id","kind","argv","cwd","output_dir","evaluation_dir","log_path",
                  "result_path","resource_profiles","source_sha256","adapter_accepted","requires_previous_gate"):
            if k not in t: raise ValueError("Missing stage field "+k)
        if t["stage_id"] in ids: raise ValueError("Duplicate stage_id")
        ids.add(t["stage_id"])
        if t["kind"] not in ("train","validation","test","interface","inference"):
            raise ValueError("Invalid stage kind")
        if not isinstance(t["argv"],list) or not all(isinstance(x,str) for x in t["argv"]) or len(t["argv"])<2:
            raise ValueError("argv must be a nonempty argument array")
        if exact_path(t["argv"][0])!=exact_path(p["python"]):
            raise ValueError("Use the registered fixed interpreter as argv[0]")
        for k in ("cwd","output_dir","evaluation_dir","log_path","result_path"):
            if not inside(t[k],p["allowed_roots"]): raise ValueError("Project path violation: "+k)
        for k in ("cwd","output_dir","evaluation_dir"):
            if not exact_path(t[k]).is_dir(): raise ValueError("Required project directory missing: "+k)
        if not inside(t["log_path"],[t["output_dir"]]) or not inside(t["result_path"],[t["output_dir"]]):
            raise ValueError("Stage log and result receipt must be inside its output_dir")
        if not Path(t["log_path"]).parent.is_dir() or not Path(t["result_path"]).parent.is_dir():
            raise ValueError("Log/result parent must already exist")
        if Path(t["result_path"]).exists(): raise ValueError("Use a fresh result_path, never overwrite a receipt")
        if not isinstance(t["source_sha256"],dict) or not t["source_sha256"]:
            raise ValueError("Source hashes required")
        for name,h in t["source_sha256"].items():
            if not inside(name,p["allowed_roots"]) or digest(Path(name).read_bytes())!=h:
                raise ValueError("Source identity mismatch")
        if len(t["resource_profiles"])<1: raise ValueError("Explicit resolved profiles required")
        for r in t["resource_profiles"]: profile_validate(r)
        if not isinstance(t["adapter_accepted"],bool) or not isinstance(t["requires_previous_gate"],bool):
            raise ValueError("Boolean adapter/gate fields required")
        if i==0 and t["requires_previous_gate"]: raise ValueError("First stage cannot require an absent gate")
    if any(t["kind"]=="train" for t in stages) and not m.get("evaluation_plan"):
        raise ValueError("Training needs explicit validation/test plan or NOT_APPLICABLE reasons")
    return m

def scan_devices():
    data=subprocess.check_output(["nvidia-smi","-q","-x"],timeout=12)
    root=ET.fromstring(data); cards=[]
    for g in root.findall("gpu"):
        def num(path):
            text=g.findtext(path)
            if text is None or text.strip()=="N/A": raise ValueError("Missing GPU telemetry "+path)
            return int(text.split()[0])
        processes=g.find("processes")
        if processes is None: raise ValueError("Missing process visibility")
        procs=[{"pid":x.findtext("pid"),"type":x.findtext("type")} for x in processes.findall("process_info")]
        ecc=g.find("ecc_errors/volatile")
        if ecc is None: raise ValueError("Missing ECC telemetry")
        bad=[]
        for node in ecc.iter():
            if "uncorrectable" in node.tag and node.text and node.text.strip() not in ("N/A",""):
                bad.append(int(node.text.strip()))
        if not bad: raise ValueError("No uncorrectable ECC counters")
        health_ok=(g.findtext("mig_mode/current_mig")=="Disabled"
            and g.findtext("gpu_reset_status/reset_required")=="No"
            and g.findtext("gpu_reset_status/drain_and_reset_recommended")=="No"
            and g.findtext("remapped_rows/remapped_row_failure")=="No"
            and g.findtext("remapped_rows/remapped_row_pending")=="No")
        cards.append({"uuid":g.findtext("uuid"),"name":g.findtext("product_name"),"health_ok":health_ok,
                      "memory_mib":num("fb_memory_usage/used"),"util":num("utilization/gpu_util"),
                      "processes":procs,"ecc_uncorrectable":sum(bad)})
    if len(cards)!=8: raise ValueError("Expected the eight physical A100 GPUs on gpu07")
    return cards

class Idle:
    def __init__(self): self.since={}; self.last=None
    def feed(self,cards,policy,now):
        if self.last is None or now-self.last>policy["max_sample_gap_seconds"]:
            self.since={}
        good=set()
        for c in cards:
            b=policy["baseline"].get(c["uuid"])
            ok=(b and b.get("reviewed") is True and b.get("evidence") and
                c["memory_mib"]==b["memory_mib"] and c["util"]==0 and not c["processes"]
                and c["ecc_uncorrectable"]==0 and c.get("health_ok",True) and "A100" in c["name"])
            if ok: good.add(c["uuid"]); self.since.setdefault(c["uuid"],now)
        self.since={u:t for u,t in self.since.items() if u in good}
        self.last=now
    def reset(self): self.since={};self.last=None
    def candidates(self,now,window=300):
        return sorted((u for u,t in self.since.items() if now-t>=window),key=lambda u:(self.since[u],u))

def mem_available():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"): return int(line.split()[1])//1024
    raise ValueError("MemAvailable missing")

def storage_ready(t,profile):
    for k in ("output_dir","evaluation_dir"):
        p=Path(t[k])
        if not p.is_dir() or not os.access(p,os.W_OK): return False
        v=os.statvfs(p)
        if v.f_bavail*v.f_frsize < profile["disk_min_gib"]*1024**3: return False
    return True

def identity(pid):
    try:
        p=Path("/proc")/str(pid); tail=(p/"stat").read_text().rsplit(")",1)[1].split()
        return {"pid":int(pid),"start":tail[19],"ppid":int(tail[1]),"session":int(tail[3]),
                "state":tail[0],"uid":p.stat().st_uid,"boot":BOOT}
    except (OSError,ValueError): return None

def same_process(a,b):
    return bool(a and b and all(a.get(k)==b.get(k) for k in ("pid","start","boot"))
                and b["state"] not in ("Z","X"))

def live(a): return same_process(a,identity(a["pid"])) if a else False

def canonical(x): return json.dumps(x,sort_keys=True,separators=(",",":")).encode()

def hash_file(p):
    h=hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def fresh_json(path,obj):
    with Path(path).open("x") as f:
        json.dump(obj,f,sort_keys=True,indent=2);f.flush();os.fsync(f.fileno())

def pin_ok(pin):
    return bool(pin and Path(pin["path"]).is_file() and hash_file(pin["path"])==pin["sha256"])

def project_stage(j):
    return j["manifest"]["stages"][j["stage_index"]]

def receipt_verified(j,a):
    if not a.get("preload_authorized_at"):raise ValueError("no final preload authorization")
    t=project_stage(j)
    r=json.loads(Path(t["result_path"]).read_text())
    for k in ("run_id","stage_id","stage_nonce"):
        if r.get(k)!=a[k]: raise ValueError("receipt identity "+k)
    if r.get("outcome")!="success" or r.get("artifacts_verified") is not True:
        raise ValueError("project acceptance did not succeed")
    if r.get("gate_passed") is not None and type(r["gate_passed"]) is not bool:
        raise ValueError("gate is not bool/null")
    if not r.get("artifacts"): raise ValueError("no verified artifact evidence")
    for item in r["artifacts"]:
        if not inside(item["path"],j["allowed_roots"]) or not pin_ok(item):
            raise ValueError("artifact pin mismatch")
        if item.get("type")=="manifest":
            m=json.loads(Path(item["path"]).read_text())
            if not m.get("files"): raise ValueError("empty artifact manifest")
            for q in m["files"]:
                if not inside(q["path"],j["allowed_roots"]) or not pin_ok(q):
                    raise ValueError("manifest artifact pin mismatch")
    return r

def verify_record(s,env,caller,now,lookup,check_release):
    def deny(reason,code=2): return code,{"allowed":False,"reason":reason,"verifier":VERSION}
    a=s.get("active")
    if not a or a.get("continuation"):return deny("NO_ACTIVE_LAUNCH")
    if s.get("recovery") or not s["policy"].get("dispatch_enabled"):
        return deny("DISPATCH_BLOCKED")
    if not s["policy"].get("migration_cleared") or not check_release(s):
        return deny("RELEASE_OR_MIGRATION_NOT_ACCEPTED")
    daemon=s.get("daemon") or {}
    if daemon.get("version")!=VERSION or not same_process(daemon.get("identity"),lookup((daemon.get("identity") or {}).get("pid",0))):
        return deny("DAEMON_IDENTITY")
    if a.get("boot")!=BOOT or now>a.get("grant_deadline",0):
        return deny("EXPIRED_OR_OLD_BOOT")
    pairs={"UGS_JOB_ID":"job_id","UGS_RUN_ID":"run_id","UGS_STAGE_ID":"stage_id",
           "UGS_STAGE_NONCE":"stage_nonce","UGS_MANIFEST_SHA256":"manifest_sha256",
           "UGS_GENERATION":"generation"}
    if env.get("UGS_MANAGED")!="1" or any(str(a.get(v))!=env.get(k) for k,v in pairs.items()):
        return deny("IDENTITY_MISMATCH")
    j=get_job(s,a["job_id"]);t=project_stage(j)
    if j["manifest_sha256"]!=a["manifest_sha256"] or t["stage_id"]!=a["stage_id"]:
        return deny("STAGE_REVISION_MISMATCH")
    if env.get("CUDA_VISIBLE_DEVICES")!=",".join(a["gpus"]) or env.get("UGS_RESULT_PATH")!=t["result_path"]:
        return deny("ALLOCATION_OR_PATH_MISMATCH")
    try:
        if json.loads(env.get("UGS_PROFILE_JSON","null"))!=a["profile"]:return deny("PROFILE_MISMATCH")
    except ValueError:return deny("PROFILE_INVALID")
    if not a.get("process"):return deny("PROCESS_REGISTRATION_PENDING",3)
    worker=lookup(caller); guardian=a.get("guardian")
    if not same_process(a["process"],worker) or not guardian or worker["ppid"]!=guardian["pid"]:
        return deny("CALLER_NOT_REGISTERED_ADAPTER")
    if not same_process(guardian,lookup(guardian["pid"])) or worker["session"]!=guardian["session"]:
        return deny("GUARDIAN_IDENTITY")
    if worker["uid"]!=os.getuid() or j["status"] not in ("STARTING","RUNNING"):
        return deny("WORKER_STATE")
    return 0,{"allowed":True,"verifier":VERSION,**{k:a[k] for k in
        ("job_id","run_id","stage_id","generation","manifest_sha256","gpus","profile")},
        "result_path":t["result_path"]}

def require_idle_window(records,gpus,policy,now,daemon):
    start=daemon["monotonic"]
    rows=[x for x in records if x.get("daemon_pid")==daemon["identity"]["pid"]
          and x.get("boot")==BOOT and x["monotonic"]>=start and x["monotonic"]<=now]
    cutoff=now-300
    before=[i for i,x in enumerate(rows) if x["monotonic"]<=cutoff]
    if not before:raise ValueError("No complete common 300-second window")
    rows=rows[before[-1]:]
    if len(rows)<11 or now-rows[-1]["monotonic"]>35:raise ValueError("Missing observations")
    if any(b["monotonic"]-a["monotonic"]>35 for a,b in zip(rows,rows[1:])):
        raise ValueError("Observation gap")
    for row in rows:
        byuuid={x["uuid"]:x for x in row["cards"]}
        for gpu in gpus:
            c=byuuid.get(gpu);b=policy["baseline"].get(gpu)
            if not c or not b or c["memory_mib"]!=b["memory_mib"] or c["processes"] or c["util"]!=0 or c["ecc_uncorrectable"]!=0 or not c.get("health_ok",False):
                raise ValueError("GPU not continuously completely idle")
    return rows

def release_used_devices(idle,gpus):
    # A known short stage may use CUDA between polls. Reset its selected cards
    # while retaining the complete histories of every untouched idle card.
    for gpu in gpus:idle.since.pop(gpu,None)

def observed_records():
    with (ROOT/"resources.jsonl").open() as f:return [json.loads(line) for line in f]

def expected_env(j,a):
    t=project_stage(j);e=dict(os.environ)
    e.update({"CUDA_VISIBLE_DEVICES":",".join(a["gpus"]),"UGS_MANAGED":"1",
              "UGS_RESULT_PATH":t["result_path"],"UGS_PROFILE_JSON":json.dumps(a["profile"]),
              "PYTHONDONTWRITEBYTECODE":"1"})
    for k in ("job_id","run_id","stage_id","stage_nonce","manifest_sha256","generation"):
        e["UGS_"+k.upper()]=str(a[k])
    return e

def enable_subreaper():
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(36,1,0,0,0)!=0:raise OSError(ctypes.get_errno(),"PR_SET_CHILD_SUBREAPER")

def process_children(pid):
    found=[]
    for q in Path("/proc").iterdir():
        if q.name.isdigit():
            v=identity(int(q.name))
            if v and v["ppid"]==pid:found.append(v)
    return found

def finalizable(a):
    g=a.get("guardian")
    if not g or live(g):return False
    p=Path(a["guardian_result"])
    if not p.is_file():raise ValueError("guardian died without reaping evidence")
    r=json.loads(p.read_text())
    if r.get("generation")!=a["generation"] or r.get("stage_nonce")!=a["stage_nonce"] or r.get("all_children_reaped") is not True or r.get("remaining"):
        raise ValueError("invalid guardian evidence")
    if live(a.get("process")):raise ValueError("worker still alive")
    return r

def source_preflight(j):
    t=project_stage(j)
    for p,h in t["source_sha256"].items():
        if hash_file(p)!=h:raise ValueError("source changed "+p)
    if Path(t["result_path"]).exists():raise ValueError("unexpected receipt")

def key_for(m):return ":".join(m[k] for k in ("project_id","experiment_id","submission_version"))

def find_submission(s,m):
    key=key_for(m);h=digest(canonical(m))
    for j in s["jobs"]:
        versions=j.get("manifest_history",[])+[{"key":j["idempotency_key"],"sha256":j["manifest_sha256"]}]
        for v in versions:
            if v["key"]==key:
                if v["sha256"]!=h:raise ValueError("idempotency key content differs")
                return j
    return None

VERSION="ugs-exec-v3"
PROTOCOL="global-gpu-scheduler-v2.0"
PROTOCOL_FILE=ROOT/"protocol-v2.md"
SELF=ROOT/"source/ugs_exec_v3.py"
VERIFY=ROOT/"source/verify_launch_v3.py"
TERMINAL={"SUCCEEDED","FAILED","CANCELLED","WITHDRAWN","GATE_REJECTED"}
def initial():
    return {"revision":0,"schema_version":3,"projects":{},"ring":[],"served":[],"round":1,
      "jobs":[],"active":None,"active_runs":{},"recovery":None,"daemon":None,
      "policy":{"max_experiment":1,"scheduler_paused":False,"dispatch_enabled":False,"migration_cleared":False,
       "execution_mode":"authorized_direct_ssh","reservation_mode":"polling_only",
       "baseline":{},"scan_seconds":30,"idle_seconds":300,"max_sample_gap_seconds":35}}
def require_v3(s):
    if s.get("schema_version")!=3:raise ValueError("Runtime v3 not installed; do not mutate legacy state")
def release_ok(s):
    r=s.get("executor_release") or {}
    return bool(r.get("version")==VERSION and r.get("cpu_accepted") is True
      and r.get("files") and all(hash_file(p)==h for p,h in r["files"].items())
      and pin_ok(r.get("cpu_evidence")))
def active_for(s,env):
    a=s["active_runs"].get(env.get("UGS_JOB_ID"))
    if not a or a.get("continuation"):return None
    return a
def view(s,a):
    v=dict(s);v["active"]=a
    return v
def check_permission(s,env,caller,now,lookup=identity):
    a=active_for(s,env)
    if not a or a.get("cancel_requested"):
        return 2,{"allowed":False,"reason":"STOPPED_OR_NO_ACTIVE","verifier":VERSION}
    return verify_record(view(s,a),env,caller,now,lookup,release_ok)
def verify_launch(preload=False):
    node_check();store=Store();env=dict(os.environ);caller=os.getppid()
    if not preload:
        with store.tx("authorization_check") as s:
            require_v3(s);code,result=check_permission(s,env,caller,time.monotonic())
            if code==0:
                a=active_for(s,env);source_preflight(get_job(s,a["job_id"]))
                a["authorized_at"]=stamp();a["authorized_pid"]=caller
            return code,result
    with store.tx() as s:
        require_v3(s);original=copy.deepcopy(s);a=active_for(original,env)
        if not a or not a.get("authorized_at") or time.monotonic()>a["prepare_deadline"]:
            return 2,{"allowed":False,"reason":"INITIAL_AUTH_REQUIRED_OR_EXPIRED","verifier":VERSION}
        a["grant_deadline"]=a["prepare_deadline"]
        code,result=check_permission(original,env,caller,time.monotonic())
        if code:return code,result
    try:
        records=observed_records();fresh=[]
        require_idle_window(records,a["gpus"],original["policy"],time.monotonic(),original["daemon"])
        for n in range(2):
            if n:time.sleep(2)
            moment=time.monotonic()
            fresh.append({"monotonic":moment,"cards":scan_devices(),"boot":BOOT,
                          "daemon_pid":original["daemon"]["identity"]["pid"]})
            require_idle_window(sorted(records+fresh,key=lambda x:x["monotonic"]),a["gpus"],
                                original["policy"],moment,original["daemon"])
        with store.tx("preload_authorized") as s:
            current=active_for(s,env)
            if not current or current["generation"]!=a["generation"]:raise ValueError("generation changed")
            check=copy.deepcopy(s);active_for(check,env)["grant_deadline"]=current["prepare_deadline"]
            code,result=check_permission(check,env,caller,time.monotonic())
            if code:return code,result
            require_idle_window(sorted(observed_records()+fresh,key=lambda x:x["monotonic"]),
                                a["gpus"],s["policy"],time.monotonic(),s["daemon"])
            j=get_job(s,a["job_id"]);source_preflight(j)
            if not budgets_ready(j,s,current["profile"],exclude=j["job_id"]):
                raise ValueError("host/storage budget changed")
            current["preload_checks"]=fresh;current["preload_authorized_at"]=stamp()
            current["preload_authorized_monotonic"]=time.monotonic()
            result["preload"]=True
            return 0,result
    except Exception as e:return 2,{"allowed":False,"reason":"PRELOAD_RECHECK_FAILED","message":str(e),"verifier":VERSION}
def profile_validate(p):
    for k in ("profile_id","gpu_count","gpu_model","precision","execution","world_size",
              "host_start_mib","host_running_mib","disk_min_gib","acceptance_ref"):
        if k not in p:raise ValueError("Missing resource field "+k)
    if type(p["gpu_count"]) is not int or not 1<=p["gpu_count"]<=8 or p["world_size"]!=p["gpu_count"]:
        raise ValueError("Explicit same-node 1..8 GPU profile and matching world_size required")
    if p["gpu_model"]!="A100" or p["precision"] not in ("bf16","fp32-fp16","fp16","fp32"):
        raise ValueError("Unsupported device/precision")
    if p["execution"] not in ("ddp","fsdp2","distributed_inference","single"):
        raise ValueError("Unsupported execution")
    if min(p["host_start_mib"],p["host_running_mib"],p["disk_min_gib"])<=0:
        raise ValueError("Positive budgets required")
def checked_pin(pin,roots):
    if not pin or not inside(pin["path"],roots) or not pin_ok(pin):raise ValueError("Evidence pin/path mismatch")
    return pin
def admission(m,p,from_index=0):
    a=m.get("admission") or {}
    if a.get("contract")!="ugs-stage-v2" or a.get("automatic_retry") is not False:
        raise ValueError("Self-service admission contract and automatic_retry=false required")
    result={}
    for t in m["stages"][from_index:]:
        spec=a.get("stages",{}).get(t["stage_id"]) or {}
        if spec.get("scope") not in ("accepted_stage","bounded_interface","validation_as_acceptance"):
            raise ValueError("Explicit stage admission scope required")
        cp=checked_pin(spec.get("cpu"),p["allowed_roots"]);report=json.loads(Path(cp["path"]).read_text())
        if report.get("contract")!="ugs-cpu-admission-v1" or report.get("passed") is not True:
            raise ValueError("Standard project CPU admission evidence required")
        if report.get("automatic_retry") is not False or not report.get("checks") or not all(
           isinstance(c,dict) and c.get("passed") is True for c in report["checks"]):
            raise ValueError("CPU evidence contains missing/failed checks")
        for path,sha in t["source_sha256"].items():
            if report.get("source_sha256",{}).get(path)!=sha:raise ValueError("CPU report not bound to stage source")
        if report.get("resource_profiles")!=t["resource_profiles"]:raise ValueError("CPU resource profile binding")
        evidence=[cp]
        for item in spec.get("interface_evidence",[]):evidence.append(checked_pin(item,p["allowed_roots"]))
        if spec["scope"]=="accepted_stage" and len(evidence)==1:
            raise ValueError("Formal stage needs existing interface evidence")
        result[t["stage_id"]]={"scope":spec["scope"],"evidence":evidence,"manifest_sha256":digest(canonical(m))}
    return result
def job_validate(m,p,from_index=0):
    candidate=copy.deepcopy(m);candidate["stages"]=candidate["stages"][from_index:]
    if not candidate["stages"]:raise ValueError("No unstarted stages")
    candidate["stages"][0]["requires_previous_gate"]=False
    _job_validate(candidate,p)
    admission(m,p,from_index)
    return m
def accepted_stage(j):
    if j["manifest"].get("protocol")!=PROTOCOL or j["manifest"].get("admission",{}).get("contract")!="ugs-stage-v2":return False
    t=project_stage(j);a=j.get("stage_acceptance",{}).get(t["stage_id"])
    if not a or a["manifest_sha256"]!=j["manifest_sha256"]:return False
    pins=a.get("evidence")
    if isinstance(pins,dict):return pin_ok(pins) # preserved legacy acceptance for adopted current stage
    return bool(pins and all(pin_ok(x) for x in pins))
def pending_heads(s):
    out={}
    for j in s["jobs"]:
        if j["status"] not in TERMINAL and j["job_id"] not in s["active_runs"]:
            out.setdefault(j["project_id"],j)
    return out
def choose(s,ready):
    h=pending_heads(s)
    for p in s["ring"]:
        if p in h and p not in s["served"] and ready(h[p]):return h[p]
    eligible=[p for p in s["ring"] if p in h and ready(h[p])]
    if eligible:s["round"]+=1;s["served"]=[];return h[eligible[0]]
    return None
def mark_served(s,p):
    if p not in s["served"]:s["served"].append(p)
    if p in s["ring"]:s["ring"].remove(p)
    s["ring"].append(p)
def budgets_ready(j,s,p,exclude=None):
    others=[a["profile"] for jid,a in s["active_runs"].items()
            if jid!=exclude and not a.get("continuation")]
    if mem_available()<p["host_start_mib"]+sum(x["host_start_mib"] for x in others):return False
    # Same shared filesystem: conservatively reserve all other declared disk budgets.
    budget=dict(p);budget["disk_min_gib"]+=sum(x["disk_min_gib"] for x in others)
    return storage_ready(project_stage(j),budget)
def select_profile(j,s,idle,age=True):
    if j.get("paused") or s["projects"].get(j["project_id"],{}).get("paused") or not accepted_stage(j):return None
    used={g for jid,a in s["active_runs"].items() if jid!=j["job_id"] for g in a.get("gpus",[])}
    pool=[g for g in idle.candidates(time.monotonic(),300 if age else 0) if g not in used]
    for p in project_stage(j)["resource_profiles"]:
        if len(pool)>=p["gpu_count"] and budgets_ready(j,s,p,exclude=j["job_id"]):
            return p,pool[:p["gpu_count"]]
    return None
def eligible(s):
    p=s["policy"]
    return (p.get("dispatch_enabled") and p.get("migration_cleared") and not p.get("scheduler_paused",False)
            and p.get("execution_mode")=="authorized_direct_ssh" and not s.get("recovery") and release_ok(s))

def slot_available(s,jid=None):
    return jid in s["active_runs"] or len(s["active_runs"])<s["policy"]["max_experiment"]
def scoped(s,jid,project):
    j=get_job(s,jid)
    if not project or j["project_id"]!=project:raise ValueError("Explicit matching --project required")
    return j
def process_tree(root):
    if not live(root):return []
    records={}
    for q in Path("/proc").iterdir():
        if q.name.isdigit():
            v=identity(int(q.name))
            if v and v["uid"]==os.getuid() and v["state"] not in ("Z","X"):records[v["pid"]]=v
    owned={root["pid"]};changed=True
    while changed:
        changed=False
        for pid,v in records.items():
            if pid not in owned and v["ppid"] in owned:owned.add(pid);changed=True
    return [records[p] for p in owned if p!=root["pid"] and p in records]
def exact_signal(record,sig):
    if record.get("uid")!=os.getuid() or not live(record):return False
    fd=None
    try:
        if hasattr(os,"pidfd_open") and hasattr(signal,"pidfd_send_signal"):
            try:fd=os.pidfd_open(record["pid"])
            except OSError:fd=None
        if not live(record):return False
        if fd is not None:signal.pidfd_send_signal(fd,sig)
        else:os.kill(record["pid"],sig)
        return True
    except ProcessLookupError:return False
    finally:
        if fd is not None:os.close(fd)
def stop_owned(jid):
    store=Store()
    with store.tx() as s:
        a=copy.deepcopy(s["active_runs"].get(jid))
    if not a or a.get("continuation"):return
    guardian_record=a.get("guardian") or a.get("guardian_spawn")
    if not guardian_record or not live(guardian_record):
        return # Missing guardian is recovered only through proof, never guessed PGIDs.
    owned={(x["pid"],x["start"]):x for x in a.get("stop_identities",[])}
    for unused in range(3):
        for rec in process_tree(guardian_record):
            owned[(rec["pid"],rec["start"])]=rec;exact_signal(rec,signal.SIGSTOP)
        time.sleep(.05)
    with store.tx("stop_identity_snapshot") as s:
        current=s["active_runs"].get(jid)
        if not current or current.get("generation")!=a["generation"]:return
        current["stop_identities"]=list(owned.values())
        current["stop_signalled_at"]=stamp()
    for rec in owned.values():exact_signal(rec,signal.SIGKILL)
def request_stop(s,j,reason,terminal="CANCELLED"):
    a=s["active_runs"].get(j["job_id"])
    if a and not a.get("continuation"):
        a["cancel_requested"]={"at":stamp(),"reason":reason,"terminal":terminal}
        j["status"]="STOPPING"
    else:
        s["active_runs"].pop(j["job_id"],None);j["status"]=terminal;j["terminal_reason"]=reason
    j["automatic_retry"]=False
def started(j,s):
    return bool(j["job_id"] in s["active_runs"] or j.get("first_started_at") or j["stage_index"] or j["results"])

def set_limit(n):
    if type(n) is not int or n<0:raise ValueError("max_experiment must be a nonnegative integer")
    with Store().tx("max_experiment_changed") as s:
        require_v3(s);old=s["policy"]["max_experiment"];s["policy"]["max_experiment"]=n
    return {"max_experiment":n,"previous":old,"stop_requested":[],"automatic_restart":False}

def guardian(nonce):
    node_check();enable_subreaper();store=Store();me=identity(os.getpid())
    with store.tx("guardian_and_worker_started") as s:
        a=next((x for x in s["active_runs"].values() if x.get("stage_nonce")==nonce),None)
        if not a or a.get("guardian"):raise ValueError("stale guardian")
        if not same_process(a.get("guardian_spawn"),me):raise ValueError("guardian launch identity")
        if not live(s["daemon"]["identity"]) or os.getppid()!=s["daemon"]["identity"]["pid"]:
            raise ValueError("guardian parent")
        j=get_job(s,a["job_id"]);t=project_stage(j)
        a["guardian"]=me;path=Path(t["output_dir"])/("ugs-guardian-"+a["generation"]+".json")
        a["guardian_result"]=str(path)
        child=None
        grant_state=copy.deepcopy(s);grant_state["policy"]["scheduler_paused"]=False
        if eligible(grant_state) and not a.get("cancel_requested") and time.monotonic()<=a["grant_deadline"]:
            source_preflight(j)
            with open(t["log_path"],"ab",buffering=0) as log:
                child=subprocess.Popen(t["argv"],cwd=t["cwd"],env=expected_env(j,a),
                    stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True)
            a["process"]=identity(child.pid)
            if not a["process"]:raise ValueError("adapter registration failed")
            j["status"]="RUNNING"
        else:
            request_stop(s,j,"dispatch revoked before worker creation")
        frozen=copy.deepcopy(a)
    exits=[];leader_rc=None
    while child is not None:
        try:pid,status=os.waitpid(-1,0)
        except InterruptedError:continue
        except ChildProcessError:break
        rc=os.waitstatus_to_exitcode(status);exits.append({"pid":pid,"exit_code":rc,"at":stamp()})
        if pid==child.pid:
            leader_rc=rc
            if rc!=0:
                with store.tx("leader_failed") as s:
                    a=s["active_runs"].get(frozen["job_id"])
                    if a and a["generation"]==frozen["generation"] and not a.get("cancel_requested"):
                        request_stop(s,get_job(s,a["job_id"]),"adapter exited "+str(rc),"FAILED")
                stop_owned(frozen["job_id"])
    if process_children(os.getpid()):raise ValueError("descendants remain after ECHILD")
    fresh_json(path,{"generation":frozen["generation"],"stage_nonce":nonce,"guardian":me,
      "leader":frozen.get("process"),"leader_exit_code":leader_rc,"all_children_reaped":True,
      "remaining":[],"exits":exits,"at":stamp()})
def gpu_contexts_gone(a):
    pids={str(v["pid"]) for v in a.get("stop_identities",[])}
    if a.get("process"):pids.add(str(a["process"]["pid"]))
    data=subprocess.check_output(["nvidia-smi","--query-compute-apps=gpu_uuid,pid","--format=csv,noheader,nounits"],text=True,timeout=12)
    return not any(len(row)>=2 and row[0].strip() in a.get("gpus",[]) and row[1].strip() in pids
                   for row in csv.reader(io.StringIO(data)))
def apply_pending(j):
    change=j.pop("pending_revision",None)
    if not change:return
    old={"key":j["idempotency_key"],"sha256":j["manifest_sha256"],"manifest":j["manifest"],"at":stamp()}
    j.setdefault("manifest_history",[]).append(old)
    j["manifest"]=change["manifest"];j["manifest_sha256"]=change["sha256"]
    j["idempotency_key"]=key_for(j["manifest"]);j["stage_acceptance"]=change["admission"]
def complete_stage(s,a,r):
    j=get_job(s,a["job_id"]);t=project_stage(j)
    j["results"].append({"stage_index":j["stage_index"],"receipt":t["result_path"],
        "receipt_sha256":hash_file(t["result_path"]),"gate_passed":r.get("gate_passed"),
        "guardian_result":a["guardian_result"],"generation":a["generation"],"manifest_sha256":a["manifest_sha256"]})
    j["stage_index"]+=1;s["active_runs"].pop(j["job_id"],None);apply_pending(j)
    if j["stage_index"]==len(j["manifest"]["stages"]):j["status"]="SUCCEEDED"
    elif project_stage(j)["requires_previous_gate"] and r.get("gate_passed") is False:
        j["status"]="GATE_REJECTED";j["remaining"]="SKIPPED_GATE"
    elif project_stage(j)["requires_previous_gate"] and r.get("gate_passed") is not True:j["status"]="WAITING_GATE"
    else:
        j["status"]="WAITING_RESOURCES"
        s["active_runs"][j["job_id"]]={"job_id":j["job_id"],"continuation":True,"created_at":stamp()}
def finalize_available(idle=None):
    store=Store()
    with store.tx() as s:records=copy.deepcopy(s["active_runs"])
    for jid,a in records.items():
        if a.get("continuation"):continue
        if a.get("cancel_requested"):
            stop_owned(jid)
            with store.tx() as current:a=copy.deepcopy(current["active_runs"].get(jid) or a)
        try:
            done=finalizable(a)
            if not done:continue
            if a.get("cancel_requested") and not gpu_contexts_gone(a):continue
            with store.tx("stage_finalized") as s:
                current=s["active_runs"].get(jid)
                if not current or current.get("generation")!=a["generation"]:continue
                j=get_job(s,jid)
                if current.get("cancel_requested"):
                    request=current["cancel_requested"];j["status"]=request["terminal"]
                    j["terminal_reason"]=request["reason"];j["guardian_result"]=a["guardian_result"]
                    j["automatic_retry"]=False;s["active_runs"].pop(jid)
                elif done["leader_exit_code"]!=0:
                    j["status"]="FAILED";j["error"]="adapter exit "+str(done["leader_exit_code"])
                    j["automatic_retry"]=False;s["active_runs"].pop(jid)
                else:
                    try:complete_stage(s,a,receipt_verified(j,a))
                    except Exception as e:
                        j["status"]="FAILED";j["error"]=str(e);j["automatic_retry"]=False;s["active_runs"].pop(jid,None)
            if idle is not None:release_used_devices(idle,a["gpus"])
        except Exception as e:
            with store.tx("recovery_blocked") as s:
                if jid in s["active_runs"]:s["recovery"]={"job_id":jid,"reason":str(e),"at":stamp()}
def launch_valid(s,j,sha,selected_gpus):
    if not eligible(s) or not slot_available(s,j["job_id"]) or j.get("paused") or s["projects"].get(j["project_id"],{}).get("paused"):return False
    if j["status"] not in ("QUEUED","WAITING_RESOURCES","WAITING_CONTINUATION"):return False
    if j["manifest_sha256"]!=sha or not accepted_stage(j):return False
    if j["job_id"] not in s["active_runs"] and pending_heads(s).get(j["project_id"],{}).get("job_id")!=j["job_id"]:return False
    used={g for jid,a in s["active_runs"].items() if jid!=j["job_id"] for g in a.get("gpus",[])}
    return not used.intersection(selected_gpus)
def serve():
    node_check();store=Store()
    with (ROOT/"daemon.lock").open("a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with store.tx("executor_start") as s:
            require_v3(s)
            if not release_ok(s):raise ValueError("release not accepted")
            if s["active_runs"] and not s.pop("handoff_ready",False):
                s["recovery"]={"reason":"Unplanned coordinator restart: verify existing identities; no relaunch"}
            s["daemon"]={"identity":identity(os.getpid()),"version":VERSION,"started":stamp(),"monotonic":time.monotonic()}
        idle=Idle();next_scan=0;children={}
        while True:
            now=time.monotonic()
            if now>=next_scan:
                with store.tx() as s:policy=copy.deepcopy(s["policy"])
                try:
                    cards=scan_devices();idle.feed(cards,policy,now)
                    append_json(ROOT/"resources.jsonl",{"at":stamp(),"monotonic":now,"cards":cards,
                       "idle_since":idle.since,"daemon_pid":os.getpid(),"version":VERSION,"boot":BOOT})
                except Exception as e:
                    idle.reset();append_json(ROOT/"service-events.jsonl",{"at":stamp(),"event":"scan_failed","error":str(e)})
                next_scan=now+30
            for pid,child in list(children.items()):
                if child.poll() is not None:del children[pid]
            detect_lost_leaders()
            finalize_available(idle)
            with store.tx() as s:s=copy.deepcopy(s)
            if not eligible(s):time.sleep(.5);continue
            selected=None;chosen_state=copy.deepcopy(s)
            for jid,a in s["active_runs"].items():
                if not a.get("continuation"):continue
                j=get_job(chosen_state,jid)
                if j.get("paused") or not select_profile(j,chosen_state,idle,False):
                    with store.tx("continuation_yield") as z:
                        if (z["active_runs"].get(jid) or {}).get("continuation"):
                            z["active_runs"].pop(jid);get_job(z,jid)["status"]="WAITING_CONTINUATION"
                    continue
                if select_profile(j,chosen_state,idle):selected=j;break
            if selected is None and slot_available(chosen_state):
                selected=choose(chosen_state,lambda j:j["status"] in ("QUEUED","WAITING_RESOURCES","WAITING_CONTINUATION")
                    and bool(select_profile(j,chosen_state,idle)))
            if not selected:time.sleep(.5);continue
            found=select_profile(selected,chosen_state,idle)
            if not found:time.sleep(.5);continue
            profile,gpus=found
            try:source_preflight(selected)
            except Exception as e:
                with store.tx("configuration_blocked") as z:
                    j=get_job(z,selected["job_id"]);j["status"]="BLOCKED";j["error"]=str(e)
                    if (z["active_runs"].get(j["job_id"]) or {}).get("continuation"):z["active_runs"].pop(j["job_id"])
                continue
            try:
                before=[]
                for n in range(2):
                    if n:time.sleep(2)
                    moment=time.monotonic();cards=scan_devices();idle.feed(cards,s["policy"],moment)
                    before.append({"monotonic":moment,"cards":cards})
                    if not all(g in idle.candidates(moment) for g in gpus):raise ValueError("candidate changed")
                with store.tx("launch_intent") as z:
                    j=get_job(z,selected["job_id"])
                    if not launch_valid(z,j,selected["manifest_sha256"],gpus):continue
                    if not budgets_ready(j,z,profile,exclude=j["job_id"]):continue
                    source_preflight(j)
                    if j["job_id"] not in z["active_runs"]:
                        z["round"]=chosen_state["round"];z["served"]=chosen_state["served"];mark_served(z,j["project_id"])
                    t=project_stage(j);generation=uuid.uuid4().hex
                    a={"job_id":j["job_id"],"run_id":j["run_id"],"stage_id":t["stage_id"],"manifest_sha256":j["manifest_sha256"],
                       "stage_nonce":uuid.uuid4().hex,"generation":generation,"boot":BOOT,"gpus":gpus,"profile":profile,
                       "process":None,"guardian":None,"grant_deadline":time.monotonic()+15,"prepare_deadline":time.monotonic()+900,
                       "checks":before,"intent_at":stamp()}
                    z["active_runs"][j["job_id"]]=a;j["status"]="STARTING";j.setdefault("first_started_at",stamp())
                    with (ROOT/"service.log").open("ab",buffering=0) as log:
                        child=subprocess.Popen([PYTHON,"-B",str(SELF),"internal","guardian","--nonce",a["stage_nonce"]],
                          stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                    a["guardian_spawn"]=identity(child.pid);children[child.pid]=child
                    if not a["guardian_spawn"]:raise ValueError("guardian identity missing")
            except Exception as e:
                with store.tx("dispatch_issue") as z:
                    if (z["active_runs"].get(selected["job_id"]) or {}).get("generation"):
                        z["recovery"]={"job_id":selected["job_id"],"reason":"ambiguous launch: "+str(e)}
                idle.reset()
            time.sleep(.5)
def summary(s):
    runs=[{k:a.get(k) for k in ("job_id","stage_id","generation","gpus","continuation","authorized_at","preload_authorized_at","cancel_requested")}
          for a in s["active_runs"].values()]
    limit=s["policy"]["max_experiment"]
    return {"version":VERSION,"protocol":PROTOCOL,"revision":s["revision"],"max_experiment":limit,
       "active_count":len(runs),"scheduler_paused":s["policy"].get("scheduler_paused",False),"draining":len(runs)>limit,"active":runs,
       "daemon":s["daemon"],"daemon_alive":live(s["daemon"]["identity"]) if s["daemon"] else False,
       "recovery":s["recovery"],"round":s["round"],"ring":s["ring"],
       "jobs":[{**{k:j.get(k) for k in ("job_id","project_id","experiment_id","queue_seq","status","paused","stage_index","error")}, "stage_id":j["manifest"]["stages"][min(j["stage_index"],len(j["manifest"]["stages"])-1)]["stage_id"]} for j in s["jobs"]]}


def same_semantics(old,new,start=0):
    if any(old.get(k)!=new.get(k) for k in ("project_id","experiment_id","scientific_contract","evaluation_plan")):
        raise ValueError("Frozen experiment/scientific identity changed")
    if len(old["stages"])!=len(new["stages"]):raise ValueError("Stage count changed")
    for index,(a,b) in enumerate(zip(old["stages"],new["stages"])):
        if index<start and a!=b:raise ValueError("Started/completed prefix is immutable")
        for k in ("stage_id","kind","project_budget","requires_previous_gate","cwd","output_dir","evaluation_dir","log_path","result_path"):
            if a.get(k)!=b.get(k):raise ValueError("Frozen stage field changed: "+k)
        def resources(t):return [{k:v for k,v in p.items() if k!="acceptance_ref"} for p in t["resource_profiles"]]
        if resources(a)!=resources(b):raise ValueError("Frozen resource semantics changed")
def submit(s,m,retry_of=None):
    previous=find_submission(s,m)
    if previous:return {"job_id":previous["job_id"],"status":previous["status"],"duplicate":True}
    p=s["projects"][m["project_id"]];job_validate(m,p)
    if any(j["project_id"]==m["project_id"] and j["experiment_id"]==m["experiment_id"] and j["status"] not in TERMINAL for j in s["jobs"]):
        raise ValueError("Unfinished experiment exists; use update/amend, not duplicate submit")
    if retry_of:
        previous=get_job(s,retry_of)
        if previous["project_id"]!=m["project_id"] or previous["status"] not in TERMINAL:
            raise ValueError("Retry requires own terminal experiment")
        old_paths={t["output_dir"] for t in previous["manifest"]["stages"]}
        if any(t["output_dir"] in old_paths for t in m["stages"]):raise ValueError("Explicit retry needs new output directories")
    seq=len(s["jobs"])+1
    j={"job_id":"job-%06d"%seq,"queue_seq":seq,"project_id":m["project_id"],"experiment_id":m["experiment_id"],
       "idempotency_key":key_for(m),"manifest_sha256":digest(canonical(m)),"manifest":m,"manifest_history":[],
       "run_id":uuid.uuid4().hex,"stage_index":0,"results":[],"paused":False,"status":"QUEUED",
       "submitted_at":stamp(),"allowed_roots":p["allowed_roots"],"automatic_retry":False,
       "stage_acceptance":admission(m,p)}
    if retry_of:j["explicit_retry_of"]=retry_of
    s["jobs"].append(j)
    return {"job_id":j["job_id"],"queue_seq":seq,"status":j["status"],"manifest_sha256":j["manifest_sha256"]}
def update_manifest(s,j,m,oldsha,start=None):
    if j["manifest_sha256"]!=oldsha:raise ValueError("Expected SHA mismatch")
    if j["status"] in TERMINAL:raise ValueError("Terminal experiment immutable")
    running=s["active_runs"].get(j["job_id"])
    if start is None:
        if running or j["stage_index"] or j["results"] or j.get("first_started_at"):
            raise ValueError("update only supports never-started experiments")
        start=0
    elif start<j["stage_index"]+(1 if running and not running.get("continuation") else 0):
        raise ValueError("Only unstarted future stages may change")
    same_semantics(j["manifest"],m,start)
    job_validate(m,s["projects"][j["project_id"]],start)
    other=find_submission(s,m)
    if other and other["job_id"]!=j["job_id"]:raise ValueError("Submission identity already used")
    change={"manifest":m,"sha256":digest(canonical(m)),
            "admission":admission(m,s["projects"][j["project_id"]],start),"from_stage_index":start,"at":stamp()}
    if j.get("pending_revision"):
        j.setdefault("pending_revision_history",[]).append(j["pending_revision"])
    j["pending_revision"]=change
    if not running or running.get("continuation"):
        apply_pending(j)
        if j["status"]=="BLOCKED":j["status"]="QUEUED";j.pop("error",None)
    return {"job_id":j["job_id"],"queue_seq":j["queue_seq"],"current_sha256":j["manifest_sha256"],
            "pending_sha256":(j.get("pending_revision") or {}).get("sha256")}
def register(s,p):
    project_validate(p)
    if p["adapter_contract"]!="ugs-stage-v2":raise ValueError("Self-service projects use ugs-stage-v2")
    evidence=checked_pin(p.get("migration_evidence"),p["allowed_roots"])
    record=json.loads(Path(evidence["path"]).read_text())
    if record.get("old_dispatch_disabled") is not True or record.get("existing_jobs_reconciled") is not True:
        raise ValueError("Project migration evidence required; no stopping existing jobs by registration")
    previous=s["projects"].get(p["project_id"])
    if previous:
        for k in ("project_root","allowed_roots","python"):
            if previous[k]!=p[k]:raise ValueError("Existing project storage/interpreter identity immutable")
    for key,other in s["projects"].items():
        if key==p["project_id"]:continue
        if any(inside(a,[b]) or inside(b,[a]) for a in p["allowed_roots"] for b in other["allowed_roots"]):
            raise ValueError("Overlapping project roots/alias prohibited")
    p.setdefault("paused",previous.get("paused",False) if previous else False)
    s["projects"][p["project_id"]]=p
    if p["project_id"] not in s["ring"]:s["ring"].append(p["project_id"])
    return {"registered":p["project_id"],"self_service":True}
def settle_stops(targets,seconds=5):
    deadline=time.monotonic()+seconds
    while True:
        finalize_available()
        with Store().tx() as s:
            pending=[j for j in targets if j in s["active_runs"]]
            states={j:get_job(s,j)["status"] for j in targets}
        if not pending or time.monotonic()>=deadline:return {"states":states,"still_stopping":pending}
        time.sleep(.25)

def migrate_legacy(s,release):
    if s.get("schema_version")==3:raise ValueError("Already migrated")
    if s.get("recovery"):raise ValueError("Resolve legacy recovery before migration")
    if s.get("daemon") and live(s["daemon"]["identity"]):raise ValueError("Old coordinator must be stopped after disabling dispatch")
    old=copy.deepcopy(s);active=s.get("active");runs={}
    if active:
        if active.get("continuation"):
            runs[active["job_id"]]=copy.deepcopy(active)
        else:
            if active.get("boot")!=BOOT or not active.get("preload_authorized_at"):
                raise ValueError("Only fully authorized existing stage can be adopted")
            if not live(active.get("guardian")) and not finalizable(active):
                raise ValueError("Cannot prove existing guardian identity")
            if live(active.get("guardian")) and not live(active.get("process")) and not Path(active["guardian_result"]).exists():
                raise ValueError("Adapter exited during handoff; finalize legacy stage first")
            runs[active["job_id"]]=copy.deepcopy(active)
            runs[active["job_id"]]["legacy_backend"]="ugs-exec-v2.3"
            runs[active["job_id"]]["adopted_at"]=stamp()
            get_job(s,active["job_id"]).setdefault("first_started_at",active["intent_at"])
    s.setdefault("runtime_history",[]).append({"at":stamp(),"daemon":old.get("daemon"),"release":old.get("executor_release"),
                                               "active":old.get("active"),"policy":old["policy"]})
    s["schema_version"]=3;s["active_runs"]=runs;s["active"]=None;s["executor_release"]=release
    s["policy"]["max_experiment"]=1;s["policy"]["scheduler_paused"]=False;s["policy"]["protocol"]=PROTOCOL;s["policy"]["dispatch_enabled"]=False
    for key,p in s["projects"].items():
        if not p.get("migration_evidence"):
            p["migration_evidence"]=old["policy"].get("migration_evidence",{}).get(key)
        p.setdefault("paused",False)
    s["handoff_ready"]=True
    if not release_ok(s):raise ValueError("New release evidence invalid")
    return {"adopted":list(runs),"restarted_experiments":[],"max_experiment":1}
def detect_lost_leaders():
    store=Store()
    with store.tx() as s:records=copy.deepcopy(s["active_runs"])
    for jid,a in records.items():
        if a.get("continuation") or a.get("cancel_requested"):continue
        if not a.get("guardian") and time.monotonic()>a.get("grant_deadline",0):
            with store.tx("launch_registration_missing") as s:
                if jid in s["active_runs"]:s["recovery"]={"job_id":jid,"reason":"Guardian registration missing; never relaunch"}
        elif a.get("legacy_backend") and live(a.get("guardian")) and a.get("process") and not live(a["process"]):
            with store.tx() as s:j=copy.deepcopy(get_job(s,jid))
            if Path(project_stage(j)["result_path"]).exists():continue
            missing=a.get("leader_missing_since")
            with store.tx("legacy_leader_exit_observed") as s:
                current=s["active_runs"].get(jid)
                if not current or current.get("generation")!=a["generation"]:continue
                if missing is None:current["leader_missing_since"]=time.monotonic()
                elif time.monotonic()-missing>=5:
                    request_stop(s,get_job(s,jid),"adopted adapter disappeared without receipt","FAILED")

def cli():
    parser=argparse.ArgumentParser(description="UGS: existing SSH only, project self-service, no automatic experiment restart")
    parser.add_argument("object",choices=["scheduler","project","experiment","resource","internal"])
    parser.add_argument("action")
    parser.add_argument("target",nargs="?")
    parser.add_argument("--file");parser.add_argument("--project");parser.add_argument("--expected-sha")
    parser.add_argument("--all",action="store_true",dest="all_jobs")
    parser.add_argument("--stop-running",action="store_true")
    parser.add_argument("--status",action="append")
    parser.add_argument("--from-stage");parser.add_argument("--stage");parser.add_argument("--nonce")
    parser.add_argument("--decision",choices=["pass","fail"])
    parser.add_argument("--tail",type=int,default=50)
    args=parser.parse_args();node_check()
    if args.all_jobs and (args.object!="experiment" or args.action not in ("stop","remove") or args.target):
        raise ValueError("--all requires experiment stop/remove and no individual ID")
    if args.stop_running and (args.object!="scheduler" or args.action!="pause"):
        raise ValueError("--stop-running only belongs to scheduler pause")
    if args.status and (args.object!="experiment" or args.action!="list"):
        raise ValueError("--status only belongs to experiment list")
    if pwd.getpwuid(os.getuid()).pw_name!="kzhang19":raise ValueError("wrong account")
    if args.object=="internal":
        if args.action=="serve":serve();return
        if args.action=="guardian":guardian(args.nonce);return
        raise ValueError("Unknown internal action")
    store=Store();out=None;stops=[]
    if args.object=="scheduler":
        if args.action in ("status","get"):
            with store.tx() as s:require_v3(s);out=summary(s)
        elif args.action=="set":
            import re
            match=re.fullmatch(r"max_experiment=(0|[1-9][0-9]*)",args.target or "")
            if not match:raise ValueError("Use exactly: scheduler set max_experiment=N")
            out=set_limit(int(match.group(1)));stops=out["stop_requested"]
        elif args.action in ("pause","resume"):
            with store.tx("scheduler_"+args.action) as s:
                require_v3(s);s["policy"]["scheduler_paused"]=args.action=="pause"
                if args.stop_running:
                    stops=[j["job_id"] for j in s["jobs"] if started(j,s) and j["status"] not in TERMINAL]
                    for jid in stops:request_stop(s,get_job(s,jid),"scheduler pause --stop-running")
                out={"scheduler_paused":s["policy"]["scheduler_paused"],"scheduler_paused":s["policy"].get("scheduler_paused",False),"max_experiment":s["policy"]["max_experiment"],
                     "stop_requested":stops,"daemon_kept_running":True}
            for jid in stops:stop_owned(jid)
        elif args.action=="start":
            with store.tx() as s:
                require_v3(s)
                if s["daemon"] and live(s["daemon"]["identity"]):
                    print(json.dumps({"already_running":True,"daemon":s["daemon"]}));return
                if not release_ok(s):raise ValueError("Runtime release not installed")
            with (ROOT/"service.log").open("ab",buffering=0) as log:
                subprocess.Popen([PYTHON,"-B",str(SELF),"internal","serve"],stdin=subprocess.DEVNULL,
                    stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            time.sleep(1)
            with store.tx() as s:out=summary(s)
        elif args.action=="recover":
            finalize_available()
            with store.tx("recovery_review") as s:
                require_v3(s)
                if s["active_runs"]:raise ValueError("Unknown/running identities remain; recovery does not relaunch or release them")
                s["recovery"]=None;out={"recovery":None,"restarted_experiments":[]}
        else:raise ValueError("Scheduler actions: status, get, set, pause, resume, start, recover")
    elif args.object=="project":
        if args.action in ("list","show"):
            with store.tx() as s:
                require_v3(s);out=s["projects"] if args.action=="list" else s["projects"][args.target]
        elif args.action=="register":
            p=json.loads(Path(args.file).read_text())
            with store.tx("project_register") as s:require_v3(s);out=register(s,p)
        elif args.action in ("pause","resume","stop","unregister"):
            with store.tx("project_"+args.action) as s:
                require_v3(s);p=s["projects"][args.target]
                if args.action in ("pause","resume"):p["paused"]=args.action=="pause"
                elif args.action=="stop":
                    p["paused"]=True
                    stops=[j["job_id"] for j in s["jobs"] if j["project_id"]==args.target and started(j,s) and j["status"] not in TERMINAL]
                    for jid in stops:request_stop(s,get_job(s,jid),"project stop")
                else:
                    if any(j["project_id"]==args.target and j["status"] not in TERMINAL for j in s["jobs"]):
                        raise ValueError("Project has unfinished experiments")
                    s.setdefault("project_history",[]).append({"at":stamp(),"project":p})
                    del s["projects"][args.target];s["ring"]=[x for x in s["ring"] if x!=args.target]
                out={"project_id":args.target,"action":args.action,"files_deleted":False}
            for jid in stops:stop_owned(jid)
        else:raise ValueError("Project actions: register, list, show, pause, resume, stop, unregister")
    elif args.object=="experiment":
        action=args.action
        if action in ("submit","validate","retry","update","amend"):
            m=json.loads(Path(args.file).read_text())
            with store.tx("experiment_"+action if action!="validate" else None) as s:
                require_v3(s)
                if action=="validate":
                    job_validate(m,s["projects"][m["project_id"]]);out={"valid":True,"manifest_sha256":digest(canonical(m))}
                elif action in ("submit","retry"):
                    if action=="retry":scoped(s,args.target,args.project)
                    out=submit(s,m,args.target if action=="retry" else None)
                else:
                    j=scoped(s,args.target,args.project)
                    index=None
                    if action=="amend":
                        index=next(i for i,t in enumerate(j["manifest"]["stages"]) if t["stage_id"]==args.from_stage)
                    out=update_manifest(s,j,m,args.expected_sha,index)
        elif action=="list":
            with store.tx() as s:
                require_v3(s);out=summary(s)["jobs"]
                if args.project:out=[x for x in out if x["project_id"]==args.project]
                if args.status:
                    known=TERMINAL|{"QUEUED","STARTING","RUNNING","STOPPING","WAITING_RESOURCES","WAITING_CONTINUATION","WAITING_GATE","BLOCKED"}
                    if any(x not in known for x in args.status):raise ValueError("Unknown status filter")
                    out=[x for x in out if x["status"] in args.status]
        elif action in ("show","logs","events"):
            with store.tx() as s:
                require_v3(s);j=copy.deepcopy(scoped(s,args.target,args.project))
            if action=="show":out=j
            elif action=="logs":
                from collections import deque
                if not 1<=args.tail<=2000:raise ValueError("tail must be 1..2000")
                stage=next(t for t in j["manifest"]["stages"] if t["stage_id"]==args.stage) if args.stage else j["manifest"]["stages"][min(j["stage_index"],len(j["manifest"]["stages"])-1)]
                path=Path(stage["log_path"])
                if not path.exists():out={"path":str(path),"lines":[]}
                else:
                    with path.open(errors="replace") as f:out={"path":str(path),"lines":[x.rstrip("\n") for x in deque(f,maxlen=args.tail)]}
            else:
                out=[];previous=None
                for line in (ROOT/"events.jsonl").read_text().splitlines():
                    r=json.loads(line);job=next((x for x in r["state"]["jobs"] if x["job_id"]==j["job_id"]),None)
                    if job:
                        fields={k:job.get(k) for k in ("status","paused","stage_index","error","terminal_reason")}
                        if fields!=previous:out.append({"at":r["at"],"event":r["event"],**fields});previous=fields
        elif action in ("stop","remove") and args.all_jobs:
            if action=="remove" and not args.project:raise ValueError("Bulk remove requires --project")
            with store.tx("experiment_"+action+"_all") as s:
                require_v3(s)
                if args.project and args.project not in s["projects"]:raise ValueError("Unknown project")
                targets=[j for j in s["jobs"] if j["status"] not in TERMINAL and
                         (not args.project or j["project_id"]==args.project) and
                         (started(j,s) if action=="stop" else not started(j,s))]
                for j in targets:
                    if action=="stop":request_stop(s,j,"experiment stop --all")
                    else:j["status"]="WITHDRAWN";j["terminal_reason"]="project withdrew queued experiment";j["automatic_retry"]=False
                stops=[j["job_id"] for j in targets] if action=="stop" else []
                out={"action":action,"job_ids":[j["job_id"] for j in targets],"project_id":args.project,
                     "queue_dispatch_unchanged":True,"scheduler_paused":s["policy"].get("scheduler_paused",False),"max_experiment":s["policy"]["max_experiment"],"files_deleted":False}
            for jid in stops:stop_owned(jid)
        elif action in ("pause","resume","remove","stop","gate"):
            with store.tx("experiment_"+action) as s:
                require_v3(s);j=scoped(s,args.target,args.project);a=s["active_runs"].get(j["job_id"])
                if j["status"] in TERMINAL:
                    if action in ("remove","stop"):out={"job_id":j["job_id"],"status":j["status"],"unchanged":True}
                    else:raise ValueError("Terminal experiment cannot be resumed")
                elif action=="remove":
                    if a or j["stage_index"] or j["results"] or j.get("first_started_at"):
                        raise ValueError("remove only withdraws never-started experiments; use stop for started work")
                    j["status"]="WITHDRAWN";j["terminal_reason"]="project withdrew queued experiment";j["automatic_retry"]=False
                elif action=="stop":
                    if not a and not j.get("first_started_at") and not j["stage_index"]:
                        raise ValueError("Never-started experiment: use remove")
                    request_stop(s,j,"project experiment stop");stops=[j["job_id"]]
                elif action in ("pause","resume"):
                    if a and not a.get("continuation"):raise ValueError("Cannot pause running processes; use stop to terminate")
                    j["paused"]=action=="pause"
                    if action=="resume" and j["status"]=="BLOCKED":
                        source_preflight(j)
                        if not accepted_stage(j):raise ValueError("Admission evidence invalid")
                        j["status"]="QUEUED";j.pop("error",None)
                else:
                    if j["status"]!="WAITING_GATE" or not args.file or not args.decision:raise ValueError("gate requires WAITING_GATE and evidence")
                    if not inside(args.file,j["allowed_roots"]):raise ValueError("Gate evidence outside project")
                    j["gate_evidence"]={"path":str(exact_path(args.file)),"sha256":hash_file(args.file),"decision":args.decision}
                    j["status"]="WAITING_CONTINUATION" if args.decision=="pass" else "GATE_REJECTED"
                if out is None:out={k:j.get(k) for k in ("job_id","status","paused")}
            for jid in stops:stop_owned(jid)
        else:raise ValueError("Experiment actions: submit, validate, list, show, logs, events, update, amend, pause, resume, remove, stop, retry, gate")
    elif args.object=="resource" and args.action=="status":
        with store.tx() as s:
            require_v3(s);state=summary(s)
            allocations={gpu:{"job_id":jid,"project_id":get_job(s,jid)["project_id"]} for jid,a in s["active_runs"].items() for gpu in a.get("gpus",[])}
        rows=observed_records();out={"observation":rows[-1] if rows else None,"allocations":allocations,
                                     "max_experiment":state["max_experiment"],"active_count":state["active_count"]}
    else:raise ValueError("Unknown object/action")
    if stops:out["stop_result"]=settle_stops(stops)
    print(json.dumps(out,ensure_ascii=False,sort_keys=True,indent=2))
if __name__=="__main__":
    try:cli()
    except Exception as error:
        print(json.dumps({"error":type(error).__name__,"message":str(error)}),file=sys.stderr);sys.exit(2)
