#!/usr/bin/env python3
"""Unified GPU scheduler. SSH CLI only; no listening sockets."""
import argparse, base64, contextlib, csv, fcntl, hashlib, io, json, os
from pathlib import Path
import signal, subprocess, sys, time, uuid, xml.etree.ElementTree as ET

ROOT = Path("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler")
VERSION = "ugs-cli-v1"
PROTOCOL = "global-gpu-scheduler-v1.2"
PYTHON = "/public/home/kzhang19/kezhang/ycj/msswift_env/bin/python"

def digest(b): return hashlib.sha256(b).hexdigest()
def stamp(): return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
def ident(pid):
    try:
        s = Path("/proc/%s/stat" % pid).read_text()
        tail = s[s.rfind(")")+2:].split()
        return {"pid": int(pid), "start": tail[19], "session": int(tail[3]), "state": tail[0]}
    except (OSError, ValueError): return None
def alive(i):
    now = ident(i["pid"]) if i else None
    return bool(now and now["start"] == i["start"] and now["state"] != "Z")
def session_members(sid):
    result = []
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            v = ident(int(p.name))
            if v and v["session"] == sid and v["state"] != "Z": result.append(v)
    return result
def node_check():
    if os.uname().nodename.split(".")[0] != "gpu07":
        raise ValueError("Run this CLI on gpu07 through existing SSH, never on admin.")
def initial():
    return {"revision":0, "projects":{}, "ring":[], "served":[], "round":1,
            "jobs":[], "active":None, "recovery":None, "daemon":None,
            "policy":{"dispatch_enabled":False, "migration_cleared":False,
                      "reservation_mode":"polling_only", "baseline":{},
                      "host_start_mib":98304, "host_running_mib":16384,
                      "disk_min_gib":80, "scan_seconds":30,
                      "idle_seconds":300, "max_sample_gap_seconds":35,
                      "protocol":PROTOCOL}}
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
    if p["adapter_contract"]!="ugs-stage-v1": raise ValueError("Unsupported adapter contract")
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
def profile_validate(p):
    for k in ("profile_id","gpu_count","gpu_model","precision","execution","world_size",
              "host_start_mib","host_running_mib","disk_min_gib","acceptance_ref"):
        if k not in p: raise ValueError("Missing resource field "+k)
    if p["gpu_model"]!="A100" or p["gpu_count"]!=2 or p["world_size"]!=2:
        raise ValueError("v1 executable backend supports only authorized A100 two-GPU profiles")
    if p["precision"] not in ("bf16","fp32-fp16") or p["execution"] not in ("ddp","fsdp2","distributed_inference"):
        raise ValueError("Unsupported explicit profile")
    if min(p["host_start_mib"],p["host_running_mib"],p["disk_min_gib"])<=0:
        raise ValueError("Positive resource budgets required")
def job_validate(m,p):
    for k in ("project_id","experiment_id","submission_version","protocol","protocol_sha256","stages"):
        if not m.get(k): raise ValueError("Missing manifest field "+k)
    if m["protocol"]!=PROTOCOL: raise ValueError("Protocol version mismatch")
    if m["protocol_sha256"]!=digest((ROOT/"protocol.md").read_bytes()):
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

TERMINAL={"SUCCEEDED","FAILED","CANCELLED","GATE_REJECTED"}
def heads(s):
    out={}
    for j in s["jobs"]:
        if j["status"] not in TERMINAL: out.setdefault(j["project_id"],j)
    return out
def choose(s, ready):
    """Only inspect each project's oldest nonterminal job."""
    h=heads(s)
    remaining=[p for p in s["ring"] if p in h and p not in s["served"]]
    for p in remaining:
        if ready(h[p]): return h[p]
    # All currently runnable projects served, or unserved projects blocked.
    eligible=[p for p in s["ring"] if p in h and ready(h[p])]
    if eligible:
        s["round"]+=1; s["served"]=[]
        return h[eligible[0]]
    return None
def mark_served(s,p):
    if p not in s["served"]: s["served"].append(p)
    if p in s["ring"]: s["ring"].remove(p)
    s["ring"].append(p)
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
def allowed_policy(p):
    if p["protocol"]!=PROTOCOL: raise ValueError("Wrong policy protocol")
    if p["scan_seconds"]!=30 or p["idle_seconds"]!=300 or not 30<=p["max_sample_gap_seconds"]<=35:
        raise ValueError("Fixed 30s scan / 300s idle / maximum 35s gap")
    if p["reservation_mode"]!="polling_only":
        raise ValueError("Reservation integration not implemented; never bypass configured reservations")
    if not isinstance(p["dispatch_enabled"],bool) or not isinstance(p["migration_cleared"],bool):
        raise ValueError("Boolean enable/migration flags required")
    if p["dispatch_enabled"]:
        if not p["migration_cleared"] or not p.get("migration_evidence") or not p.get("acceptance_evidence"):
            raise ValueError("Migration and scheduler/adapter acceptance evidence required")
        if len(p["baseline"])<2: raise ValueError("At least two individually reviewed device baselines required")
        raise ValueError("GPU dispatch is locked in this CPU integration release. A separately accepted launcher release is required; register/validate/submit/status are available.")
    for u,b in p["baseline"].items():
        if not u.startswith("GPU-") or b.get("reviewed") is not True or not b.get("evidence") or b["memory_mib"]<0:
            raise ValueError("Invalid per-UUID control-only baseline")
    return p
def select_profile(j,s,idle,age=True):
    t=j["manifest"]["stages"][j["stage_index"]]
    if not t["adapter_accepted"] or j.get("paused"): return None
    pool=idle.candidates(time.monotonic(),300 if age else 0)
    for p in t["resource_profiles"]:
        if len(pool)>=p["gpu_count"] and mem_available()>=max(p["host_start_mib"],s["policy"]["host_start_mib"]) and storage_ready(t,p):
            return p,pool[:p["gpu_count"]]
    return None
def receipt_check(j,a):
    t=j["manifest"]["stages"][j["stage_index"]]
    r=json.loads(Path(t["result_path"]).read_text())
    for k in ("run_id","stage_id","stage_nonce"):
        if r.get(k)!=a[k]: raise ValueError("Receipt identity mismatch: "+k)
    if not a.get("preload_authorized_at"):raise ValueError("no final preload authorization")
    if r.get("outcome")!="success" or r.get("artifacts_verified") is not True:
        raise ValueError("Missing successful artifact verification")
    if r.get("gate_passed") is not None and type(r["gate_passed"]) is not bool: raise ValueError("Invalid gate result")
    for v in r.get("artifacts",[]):
        if not inside(v["path"],j["allowed_roots"]): raise ValueError("Artifact outside project roots")
        p=Path(v["path"])
        if not p.exists(): raise ValueError("Artifact missing")
    return r


# Executable release: all authorization is backed by the single journal.
import ctypes, copy, pwd, shlex
VERSION = "ugs-exec-v2.2"
SELF = ROOT / "source/ugs_exec_v2_2.py"
VERIFY = ROOT / "source/verify_launch_v2_2.py"
BOOT = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
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
def release_ok(s):
    r=s.get("executor_release")
    return bool(r and r.get("version")==VERSION and r.get("cpu_accepted") is True
                and all(hash_file(p)==h for p,h in r["files"].items())
                and pin_ok(r["cpu_evidence"]))
def project_stage(j):
    return j["manifest"]["stages"][j["stage_index"]]
def receipt_verified(j,a):
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
def verify_launch(preload=False):
    if preload: return verify_preload()
    node_check()
    with Store().tx("authorization_check") as s:
        code,result=verify_record(s,dict(os.environ),os.getppid(),time.monotonic(),identity,release_ok)
        if code==0:
            a=s["active"];j=get_job(s,a["job_id"])
            for p,h in project_stage(j)["source_sha256"].items():
                if hash_file(p)!=h: return 2,{"allowed":False,"reason":"SOURCE_CHANGED"}
            a["authorized_at"]=stamp();a["authorized_pid"]=os.getppid()
        return code,result
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
def verify_preload():
    node_check();store=Store();caller=os.getppid();env=dict(os.environ)
    with store.tx() as s:
        original=copy.deepcopy(s);a=original.get("active") or {}
        if not a.get("authorized_at") or time.monotonic()>a.get("prepare_deadline",0):
            return 2,{"allowed":False,"reason":"INITIAL_AUTH_REQUIRED_OR_PREPARATION_EXPIRED"}
        # Same registered caller may receive a final fresh grant after CPU preparation.
        a["grant_deadline"]=a["prepare_deadline"]
        code,result=verify_record(original,env,caller,time.monotonic(),identity,release_ok)
        if code:return code,result
    records=observed_records()
    now=time.monotonic()
    try:
        window=require_idle_window(records,a["gpus"],original["policy"],now,original["daemon"])
        fresh=[]
        for i in range(2):
            if i:time.sleep(2)
            stamp_mono=time.monotonic();cards=scan_devices()
            fresh.append({"monotonic":stamp_mono,"cards":cards,"daemon_pid":original["daemon"]["identity"]["pid"],"boot":BOOT})
            # Include fresh samples and retain coverage from the earlier anchor.
            require_idle_window(records+fresh,a["gpus"],original["policy"],stamp_mono,original["daemon"])
        with store.tx("preload_authorized") as current:
            active=current.get("active") or {}
            if active.get("generation")!=a["generation"]:raise ValueError("generation changed")
            check=copy.deepcopy(current);check["active"]["grant_deadline"]=active["prepare_deadline"]
            code,result=verify_record(check,env,caller,time.monotonic(),identity,release_ok)
            if code:return code,result
            latest=sorted(observed_records()+fresh,key=lambda row:row["monotonic"])
            require_idle_window(latest,active["gpus"],current["policy"],time.monotonic(),current["daemon"])
            j=get_job(current,active["job_id"]);source_preflight(j)
            if mem_available()<active["profile"]["host_start_mib"] or not storage_ready(project_stage(j),active["profile"]):
                raise ValueError("host/storage admission changed")
            active["preload_checks"]=fresh
            active["preload_authorized_at"]=stamp()
            active["preload_authorized_monotonic"]=time.monotonic()
            result["preload"]=True
            return 0,result
    except Exception as e:
        return 2,{"allowed":False,"reason":"PRELOAD_RECHECK_FAILED","message":str(e)}
def launch_still_eligible(s,j,expected,prior_active):
    if not eligibility(s):return False
    if bool(s["active"])!=bool(prior_active):return False
    if s["active"] and s["active"]["job_id"]!=j["job_id"]:return False
    if j["status"] not in ("QUEUED","WAITING_RESOURCES","WAITING_CONTINUATION"):return False
    if j.get("paused") or not accepted_stage(j) or j["manifest_sha256"]!=expected["manifest_sha256"]:return False
    if heads(s).get(j["project_id"],{}).get("job_id")!=j["job_id"]:return False
    return True
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
def guardian(nonce):
    node_check();enable_subreaper();store=Store();me=identity(os.getpid())
    with store.tx("guardian_and_worker_started") as s:
        a=s["active"]
        if not a or a.get("stage_nonce")!=nonce or a.get("guardian"):
            raise ValueError("stale or duplicate guardian")
        if not live(s["daemon"]["identity"]) or os.getppid()!=s["daemon"]["identity"]["pid"]:
            raise ValueError("guardian was not created by coordinator")
        if not release_ok(s) or not s["policy"]["dispatch_enabled"] or s.get("recovery"):
            raise ValueError("dispatch revoked")
        j=get_job(s,a["job_id"]);t=project_stage(j)
        if time.monotonic()>a["grant_deadline"]: raise ValueError("expired launch intent")
        if Path(t["result_path"]).exists():raise ValueError("receipt already exists")
        for p,h in t["source_sha256"].items():
            if hash_file(p)!=h:raise ValueError("source changed")
        a["guardian"]=me
        path=Path(t["output_dir"])/("ugs-guardian-"+a["generation"]+".json")
        a["guardian_result"]=str(path)
        with open(t["log_path"],"ab",buffering=0) as log:
            child=subprocess.Popen(t["argv"],cwd=t["cwd"],env=expected_env(j,a),
                stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,close_fds=True)
        a["process"]=identity(child.pid)
        if not a["process"]:raise ValueError("worker identity unavailable")
        j["status"]="RUNNING"
    # waitpid(-1) includes adopted descendants even if they detach into another session.
    exits=[];leader_rc=None
    while True:
        try:pid,status=os.waitpid(-1,0)
        except InterruptedError:continue
        except ChildProcessError:break
        rc=os.waitstatus_to_exitcode(status);exits.append({"pid":pid,"exit_code":rc,"at":stamp()})
        if pid==child.pid:leader_rc=rc
    remaining=process_children(os.getpid())
    if remaining:raise ValueError("children remain despite ECHILD")
    fresh_json(path,{"generation":a["generation"],"stage_nonce":nonce,
        "guardian":me,"leader":a["process"],"leader_exit_code":leader_rc,
        "all_children_reaped":True,"remaining":[],"exits":exits,"at":stamp()})
def accepted_stage(j):
    t=project_stage(j);a=j.get("stage_acceptance",{}).get(t["stage_id"])
    return bool(a and a["manifest_sha256"]==j["manifest_sha256"] and pin_ok(a["evidence"]))
def select_profile(j,s,idle,age=True):
    if j.get("paused") or not accepted_stage(j):return None
    t=project_stage(j);pool=idle.candidates(time.monotonic(),300 if age else 0)
    for p in t["resource_profiles"]:
        if len(pool)>=p["gpu_count"] and mem_available()>=p["host_start_mib"] and storage_ready(t,p):
            return p,pool[:p["gpu_count"]]
    return None
def eligibility(s):
    return (s["policy"].get("dispatch_enabled") and s["policy"].get("migration_cleared")
            and not s.get("recovery") and release_ok(s))
def complete_stage(s,a,r):
    j=get_job(s,a["job_id"]);t=project_stage(j)
    j["results"].append({"stage_index":j["stage_index"],"receipt":t["result_path"],
        "receipt_sha256":hash_file(t["result_path"]),"gate_passed":r.get("gate_passed"),
        "guardian_result":a["guardian_result"],"generation":a["generation"]})
    j["stage_index"]+=1
    if j["stage_index"]==len(j["manifest"]["stages"]):
        j["status"]="SUCCEEDED";s["active"]=None
    elif project_stage(j)["requires_previous_gate"] and r.get("gate_passed") is False:
        j["status"]="GATE_REJECTED";j["remaining"]="SKIPPED_GATE";s["active"]=None
    elif project_stage(j)["requires_previous_gate"] and r.get("gate_passed") is not True:
        j["status"]="WAITING_GATE";s["active"]=None
    else:j["status"]="WAITING_RESOURCES";s["active"]={"job_id":j["job_id"],"continuation":True}
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
def serve():
    node_check();store=Store()
    with (ROOT/"daemon.lock").open("a+b") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with store.tx("executor_start") as s:
            if not release_ok(s):raise ValueError("unaccepted executor release")
            if s["active"]:s["recovery"]="Restart: reconcile existing launch before any dispatch"
            s["daemon"]={"identity":identity(os.getpid()),"started":stamp(),"version":VERSION,"monotonic":time.monotonic()}
        idle=Idle();next_scan=0;children={};last_notice=None
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
            for pid,c in list(children.items()):
                if c.poll() is not None:del children[pid]
            with store.tx() as s:s=copy.deepcopy(s)
            a=s["active"]
            if a and not a.get("continuation"):
                try:
                    done=finalizable(a)
                    if done and not s.get("recovery"):
                        with store.tx("stage_finalized") as z:
                            if z["active"]["generation"]!=a["generation"]:raise ValueError("generation changed")
                            j=get_job(z,a["job_id"])
                            if done["leader_exit_code"]!=0:
                                j["status"]="FAILED";j["error"]="adapter exit "+str(done["leader_exit_code"]);z["active"]=None
                            else:
                                try:complete_stage(z,a,receipt_verified(j,a))
                                except Exception as e:j["status"]="FAILED";j["error"]=str(e);z["active"]=None
                        release_used_devices(idle,a["gpus"])
                    elif not a.get("guardian") and now>a["grant_deadline"]:
                        raise ValueError("launch identity registration missing")
                    elif idle.last is None or now-idle.last>60:
                        raise ValueError("resource observer stale")
                except Exception as e:
                    with store.tx("recovery_block") as z:
                        if not z.get("recovery"):z["recovery"]=str(e)
                time.sleep(1);continue
            if not eligibility(s):
                time.sleep(1);continue
            # Mode is explicit, never infer that installed Slurm is absent.
            if s["policy"].get("execution_mode")!="authorized_direct_ssh":
                time.sleep(1);continue
            selected=None;state_copy=copy.deepcopy(s)
            if a:
                selected=get_job(state_copy,a["job_id"])
                if not select_profile(selected,state_copy,idle,False):
                    with store.tx("continuation_yield") as z:
                        get_job(z,a["job_id"])["status"]="WAITING_CONTINUATION";z["active"]=None
                    time.sleep(1);continue
            else:
                selected=choose(state_copy,lambda j:j["status"] in ("QUEUED","WAITING_RESOURCES","WAITING_CONTINUATION")
                                and bool(select_profile(j,state_copy,idle)))
            if not selected:time.sleep(1);continue
            found=select_profile(selected,state_copy,idle)
            if not found:time.sleep(1);continue
            profile,gpus=found
            try:
                source_preflight(selected)
            except Exception as e:
                with store.tx("configuration_blocked") as z:
                    j=get_job(z,selected["job_id"])
                    if not z["active"] or z["active"].get("continuation"):
                        j["status"]="BLOCKED";j["error"]="Configuration preflight: "+str(e)
                        if z["active"]:z["active"]=None
                time.sleep(1);continue
            try:
                before=[]
                for n in range(2):
                    if n:time.sleep(2)
                    moment=time.monotonic();cards=scan_devices();idle.feed(cards,s["policy"],moment)
                    before.append({"monotonic":moment,"cards":cards})
                    if not all(u in idle.candidates(moment) for u in gpus):raise ValueError("candidate changed")
                with store.tx("launch_intent") as z:
                    j=get_job(z,selected["job_id"])
                    if not launch_still_eligible(z,j,selected,a):continue
                    if not select_profile(j,z,idle):continue
                    source_preflight(j)
                    if not a:
                        z["round"]=state_copy["round"];z["served"]=state_copy["served"];mark_served(z,j["project_id"])
                    t=project_stage(j);generation=uuid.uuid4().hex
                    z["active"]={"job_id":j["job_id"],"run_id":j["run_id"],"stage_id":t["stage_id"],
                        "manifest_sha256":j["manifest_sha256"],"stage_nonce":uuid.uuid4().hex,
                        "generation":generation,"boot":BOOT,"gpus":gpus,"profile":profile,
                        "process":None,"guardian":None,"grant_deadline":time.monotonic()+15,"prepare_deadline":time.monotonic()+900,
                        "checks":before,"idle_since":dict(idle.since),"intent_at":stamp()}
                    j["status"]="STARTING";intent=copy.deepcopy(z["active"])
                with (ROOT/"service.log").open("ab",buffering=0) as log:
                    c=subprocess.Popen([PYTHON,"-B",str(SELF),"guardian","--nonce",intent["stage_nonce"]],
                        stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                children[c.pid]=c
            except Exception as e:
                with store.tx("dispatch_issue") as z:
                    if z.get("active") and not z["active"].get("continuation"):z["recovery"]="Launch ambiguous: "+str(e)
                message=str(e)
                if message!=last_notice:
                    append_json(ROOT/"service-events.jsonl",{"at":stamp(),"event":"admission_wait","reason":message})
                    last_notice=message
                idle.reset()
            time.sleep(1)
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
def replace_manifest(s,jid,oldsha,m):
    j=get_job(s,jid)
    # Wiring may change; frozen experiment budget, stage sequence and resource semantics may not.
    oldm=j["manifest"]
    if len(oldm["stages"])!=len(m["stages"]):raise ValueError("stage count changed")
    for before,after in zip(oldm["stages"],m["stages"]):
        for field in ("stage_id","kind","resource_profiles","project_budget","requires_previous_gate",
                      "cwd","output_dir","evaluation_dir","log_path","result_path"):
            if before.get(field)!=after.get(field):raise ValueError("frozen stage semantics changed: "+field)
    if oldm.get("evaluation_plan")!=m.get("evaluation_plan"):raise ValueError("evaluation plan changed")
    if j["manifest_sha256"]!=oldsha:raise ValueError("CAS old SHA mismatch")
    if j["stage_index"]!=0 or j["results"] or j["status"] not in ("QUEUED","WAITING_RESOURCES","BLOCKED"):
        raise ValueError("only never-started queued jobs may be revised")
    if s["active"] and s["active"]["job_id"]==jid:raise ValueError("active job cannot be revised")
    if any(m[k]!=j["manifest"][k] for k in ("project_id","experiment_id")):raise ValueError("stable experiment identity")
    prior=find_submission(s,m)
    if prior and prior["job_id"]!=jid:raise ValueError("key owned by another job")
    j.setdefault("manifest_history",[]).append({"key":j["idempotency_key"],"sha256":j["manifest_sha256"],"manifest":j["manifest"],"at":stamp()})
    j["manifest"]=m;j["manifest_sha256"]=digest(canonical(m));j["idempotency_key"]=key_for(m)
    j["stage_acceptance"]={};j["status"]="QUEUED";j.pop("error",None)
    return j
def summary(s):
    return {"version":VERSION,"protocol":PROTOCOL,"revision":s["revision"],"daemon":s["daemon"],
            "daemon_alive":live(s["daemon"]["identity"]) if s["daemon"] else False,
            "dispatch_enabled":s["policy"]["dispatch_enabled"],"migration_cleared":s["policy"]["migration_cleared"],
            "execution_mode":s["policy"].get("execution_mode","UNRESOLVED"),"active":s["active"],
            "recovery":s["recovery"],"ring":s["ring"],"served":s["served"],"round":s["round"],
            "jobs":[{k:j.get(k) for k in ("job_id","queue_seq","project_id","experiment_id","status","stage_index","error")} for j in s["jobs"]]}
def cli():
    ap=argparse.ArgumentParser()
    ap.add_argument("command",choices=["about","status","queue","job","validate","register","submit",
        "replace","accept","policy-show","configure","start","serve","guardian","pause","resume","cancel","gate","reconcile"])
    ap.add_argument("--file");ap.add_argument("--job-id");ap.add_argument("--expected-sha")
    ap.add_argument("--stage-id");ap.add_argument("--nonce");ap.add_argument("--decision",choices=["pass","fail"])
    a=ap.parse_args();node_check()
    if pwd.getpwuid(os.getuid()).pw_name!="kzhang19":raise ValueError("wrong account")
    if a.command=="serve":serve();return
    if a.command=="guardian":guardian(a.nonce);return
    st=Store()
    if a.command=="about":
        out={"version":VERSION,"protocol":PROTOCOL,"protocol_sha256":hash_file(ROOT/"protocol.md"),
             "root":str(ROOT),"transport":"existing SSH only","listening_ports":[]}
    elif a.command in ("status","queue","job","policy-show"):
        with st.tx() as s:
            out=get_job(s,a.job_id) if a.command=="job" else s["policy"] if a.command=="policy-show" else summary(s)
    elif a.command=="register":
        p=project_validate(json.loads(Path(a.file).read_text()))
        with st.tx("register") as s:
            if p["project_id"] in s["projects"] and p!=s["projects"][p["project_id"]]:raise ValueError("project identity differs")
            if any(k!=p["project_id"] and exact_path(v["project_root"])==exact_path(p["project_root"]) for k,v in s["projects"].items()):raise ValueError("project alias")
            s["projects"][p["project_id"]]=p
            if p["project_id"] not in s["ring"]:s["ring"].append(p["project_id"])
            out={"registered":p["project_id"]}
    elif a.command in ("submit","validate","replace"):
        m=json.loads(Path(a.file).read_text())
        with st.tx(a.command if a.command!="validate" else None) as s:
            prev=find_submission(s,m)
            if prev:out={"job_id":prev["job_id"],"duplicate":True,"status":prev["status"]}
            else:
                job_validate(m,s["projects"][m["project_id"]])
                if a.command=="validate":out={"valid":True,"manifest_sha256":digest(canonical(m))}
                elif a.command=="replace":
                    j=replace_manifest(s,a.job_id,a.expected_sha,m);out={"job_id":j["job_id"],"manifest_sha256":j["manifest_sha256"],"queue_seq":j["queue_seq"]}
                else:
                    if any(j["project_id"]==m["project_id"] and j["experiment_id"]==m["experiment_id"] and j["status"] not in TERMINAL for j in s["jobs"]):raise ValueError("existing unfinished experiment; use CAS replace")
                    seq=len(s["jobs"])+1
                    j={"job_id":"job-%06d"%seq,"queue_seq":seq,"project_id":m["project_id"],
                       "experiment_id":m["experiment_id"],"idempotency_key":key_for(m),"manifest_sha256":digest(canonical(m)),
                       "manifest":m,"allowed_roots":s["projects"][m["project_id"]]["allowed_roots"],"stage_index":0,
                       "results":[],"run_id":uuid.uuid4().hex,"status":"QUEUED","submitted_at":stamp(),"paused":False}
                    s["jobs"].append(j);out={"job_id":j["job_id"],"status":j["status"]}
    elif a.command=="reconcile":
        with st.tx("recovery_reconcile") as s:
            active=s["active"]
            if not active or active.get("continuation"):
                raise ValueError("No interrupted launch to reconcile")
            done=finalizable(active)
            if not done:raise ValueError("Guardian/worker still running or no terminal proof")
            j=get_job(s,active["job_id"])
            if done["leader_exit_code"]!=0:
                j["status"]="FAILED";j["error"]="adapter exit "+str(done["leader_exit_code"]);s["active"]=None
            else:
                try:complete_stage(s,active,receipt_verified(j,active))
                except Exception as e:j["status"]="FAILED";j["error"]=str(e);s["active"]=None
            s["recovery"]=None
            out={"reconciled":j["job_id"],"status":j["status"]}
    elif a.command=="accept":
        e=json.loads(Path(a.file).read_text());pin={"path":str(Path(a.file).resolve()),"sha256":hash_file(a.file)}
        with st.tx("stage_cpu_acceptance") as s:
            j=get_job(s,a.job_id)
            if s["active"] and s["active"]["job_id"]==j["job_id"]:raise ValueError("active acceptance immutable")
            if e["manifest_sha256"]!=j["manifest_sha256"] or e["stage_id"]!=a.stage_id or e["cpu_passed"] is not True:
                raise ValueError("acceptance binding mismatch")
            if e["scope"] not in ("bounded_interface","validation_as_acceptance","accepted_stage"):
                raise ValueError("unknown acceptance scope")
            for p in e["evidence"]:
                if not pin_ok(p):raise ValueError("acceptance evidence changed")
            j.setdefault("stage_acceptance",{})[a.stage_id]={"manifest_sha256":j["manifest_sha256"],"evidence":pin,"scope":e["scope"]}
            out={"accepted":a.stage_id,"scope":e["scope"]}
    elif a.command=="configure":
        config=json.loads(Path(a.file).read_text())
        with st.tx("executor_configure") as s:
            if s["active"]:raise ValueError("no live policy edit")
            p=config["policy"];r=config["executor_release"]
            if p["scan_seconds"]!=30 or p["idle_seconds"]!=300 or not 30<=p["max_sample_gap_seconds"]<=35:raise ValueError("invalid timing")
            if p.get("execution_mode") not in ("UNRESOLVED","authorized_direct_ssh"):raise ValueError("execution mode unsupported")
            if p["dispatch_enabled"]:
                if p["execution_mode"]!="authorized_direct_ssh" or not p["migration_cleared"]:raise ValueError("mode/migration not authorized")
                if not pin_ok(p.get("mode_authorization")):raise ValueError("missing explicit mode authorization")
                if set(p["migration_evidence"])!=set(s["projects"]):raise ValueError("all registered projects need migration evidence")
                if not all(pin_ok(x) for x in p["migration_evidence"].values()):raise ValueError("migration pin mismatch")
            candidate=copy.deepcopy(s);candidate["executor_release"]=r
            if not release_ok(candidate):raise ValueError("release not accepted")
            s["policy"]=p;s["executor_release"]=r;out={"configured":True,"dispatch_enabled":p["dispatch_enabled"]}
    elif a.command in ("pause","resume","cancel","gate"):
        with st.tx(a.command) as s:
            j=get_job(s,a.job_id)
            if s["active"] and s["active"]["job_id"]==a.job_id:raise ValueError("active job protected")
            if j["status"] in TERMINAL:raise ValueError("terminal job immutable")
            if a.command=="pause":j["paused"]=True
            elif a.command=="resume":j["paused"]=False
            elif a.command=="cancel":j["status"]="CANCELLED"
            else:
                if j["status"]!="WAITING_GATE" or not a.file or not a.decision:raise ValueError("gate needs evidence")
                j["gate_evidence"]={"path":str(Path(a.file).resolve()),"sha256":hash_file(a.file)}
                j["status"]="WAITING_CONTINUATION" if a.decision=="pass" else "GATE_REJECTED"
            out={"job_id":j["job_id"],"status":j["status"],"paused":j["paused"]}
    elif a.command=="start":
        with st.tx() as s:
            if s["daemon"] and live(s["daemon"]["identity"]):
                out={"already_running":True,"daemon":s["daemon"]}
                print(json.dumps(out));return
            if not release_ok(s):raise ValueError("release not configured")
        with (ROOT/"service.log").open("ab",buffering=0) as log:
            c=subprocess.Popen([PYTHON,"-B",str(SELF),"serve"],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        time.sleep(1)
        with st.tx() as s:out=summary(s)
    print(json.dumps(out,sort_keys=True,indent=2))
if __name__=="__main__":
    try:cli()
    except Exception as e:
        print(json.dumps({"error":type(e).__name__,"message":str(e)}),file=sys.stderr);sys.exit(2)
