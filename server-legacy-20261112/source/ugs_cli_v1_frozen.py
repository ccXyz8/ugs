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
        cards.append({"uuid":g.findtext("uuid"),"name":g.findtext("product_name"),
                      "memory_mib":num("fb_memory_usage/used"),"util":num("utilization/gpu_util"),
                      "processes":procs,"ecc_uncorrectable":sum(bad)})
    if not cards: raise ValueError("No visible devices")
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
                and c["ecc_uncorrectable"]==0 and "A100" in c["name"])
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
    if r.get("outcome")!="success" or r.get("artifacts_verified") is not True:
        raise ValueError("Missing successful artifact verification")
    if r.get("gate_passed") is not None and type(r["gate_passed"]) is not bool: raise ValueError("Invalid gate result")
    for v in r.get("artifacts",[]):
        if not inside(v["path"],j["allowed_roots"]): raise ValueError("Artifact outside project roots")
        p=Path(v["path"])
        if not p.exists(): raise ValueError("Artifact missing")
    return r

def serve():
    node_check(); store=Store(); pid=os.getpid()
    with (ROOT/"daemon.lock").open("a+b") as daemon_lock:
        try: fcntl.flock(daemon_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise ValueError("A coordinator already holds daemon.lock")
        with store.tx("daemon_start") as s:
            if s["active"]:
                s["recovery"]="Existing active identity must be reconciled; no replay or timeout release"
            s["daemon"]={"identity":ident(pid),"started":stamp(),"version":VERSION}
        idle=Idle(); child=None; next_scan=0
        while True:
            now=time.monotonic()
            if now>=next_scan:
                with store.tx() as s: policy=s["policy"]
                try:
                    cards=scan_devices();idle.feed(cards,policy,now)
                    append_json(ROOT/"resources.jsonl",{"at":stamp(),"monotonic":now,"cards":cards,
                        "idle_since":idle.since,"daemon_pid":pid})
                except Exception as e:
                    idle.reset()
                    append_json(ROOT/"service-events.jsonl",{"at":stamp(),"event":"scan_failed","error":str(e)})
                next_scan=now+30
            with store.tx() as s:
                active=s["active"]; recovery=s["recovery"]; enabled=s["policy"]["dispatch_enabled"]
            if recovery: time.sleep(1);continue
            if active and active.get("process"):
                if child is None:
                    with store.tx("recovery_block") as s: s["recovery"]="Missing original subprocess handle"
                    continue
                rc=child.poll()
                if rc is None:
                    # Fail closed for future work on runtime-health problems; adapter owns safe termination.
                    try:
                        running_j=get_job(s,active["job_id"])
                        floor=max(active["profile"]["host_running_mib"],s["policy"]["host_running_mib"])
                        if mem_available()<floor or idle.last is None or time.monotonic()-idle.last>60:
                            with store.tx("runtime_attention") as z:
                                z["recovery"]="Runtime monitoring/host memory needs verified project intervention"
                    except Exception as e:
                        with store.tx("runtime_attention") as z: z["recovery"]=str(e)
                    time.sleep(1);continue
                if session_members(active["process"]["session"]):
                    with store.tx("descendants_remaining") as s:
                        s["recovery"]="Stage leader exited but process-session descendants remain"
                    continue
                with store.tx("stage_complete") as s:
                    j=get_job(s,active["job_id"])
                    try:
                        if rc!=0: raise ValueError("Worker exit code "+str(rc))
                        r=receipt_check(j,active)
                        j["results"].append({"stage_index":j["stage_index"],"receipt":j["manifest"]["stages"][j["stage_index"]]["result_path"],"gate_passed":r.get("gate_passed")})
                        j["stage_index"]+=1
                        if j["stage_index"]==len(j["manifest"]["stages"]):
                            j["status"]="SUCCEEDED";s["active"]=None
                        elif j["manifest"]["stages"][j["stage_index"]]["requires_previous_gate"] and r.get("gate_passed") is False:
                            j["status"]="GATE_REJECTED";j["remaining"]="SKIPPED_GATE";s["active"]=None
                        elif j["manifest"]["stages"][j["stage_index"]]["requires_previous_gate"] and r.get("gate_passed") is not True:
                            j["status"]="WAITING_GATE";s["active"]=None
                        else:
                            j["status"]="WAITING_RESOURCES";s["active"]={"job_id":j["job_id"],"continuation":True}
                    except Exception as e:
                        j["status"]="FAILED";j["error"]=str(e);s["active"]=None
                child=None;continue
            if not enabled:
                time.sleep(1);continue
            with store.tx() as s:
                selected=None
                if s["active"]:
                    selected=get_job(s,s["active"]["job_id"])
                    if not select_profile(selected,s,idle,age=False):
                        selected=None
                state_copy=json.loads(json.dumps(s))
            if s["active"] and selected is None:
                with store.tx("continuation_yield") as s:
                    j=get_job(s,s["active"]["job_id"]);j["status"]="WAITING_CONTINUATION";s["active"]=None
                continue
            if selected is None:
                def ready(j):
                    return j["status"] in ("QUEUED","WAITING_RESOURCES","WAITING_CONTINUATION") and select_profile(j,state_copy,idle)
                selected=choose(state_copy,ready)
            if not selected:
                time.sleep(1);continue
            found=select_profile(selected,state_copy,idle)
            if not found:
                time.sleep(1);continue
            profile,gpus=found
            # Two fresh non-CUDA rechecks; preserve common idle history.
            try:
                idle.feed(scan_devices(),state_copy["policy"],time.monotonic())
                if not all(u in idle.candidates(time.monotonic()) for u in gpus): continue
                time.sleep(2)
                idle.feed(scan_devices(),state_copy["policy"],time.monotonic())
                if not all(u in idle.candidates(time.monotonic()) for u in gpus): continue
            except Exception:
                idle.reset();continue
            jid=selected["job_id"]
            with store.tx("launch_intent") as s:
                if s["recovery"] or not s["policy"]["dispatch_enabled"]: continue
                if s["active"] and s["active"]["job_id"]!=jid: continue
                j=get_job(s,jid)
                if not select_profile(j,s,idle): continue
                t=j["manifest"]["stages"][j["stage_index"]]
                # Validate frozen sources again immediately before launching.
                for name,h in t["source_sha256"].items():
                    if digest(Path(name).read_bytes())!=h: raise ValueError("Frozen source changed")
                if Path(t["result_path"]).exists(): raise ValueError("Unexpected existing stage receipt")
                if not s["active"]:
                    s["round"]=state_copy["round"];s["served"]=state_copy["served"]
                    mark_served(s,j["project_id"])
                a={"job_id":jid,"run_id":j["run_id"],"stage_id":t["stage_id"],
                   "stage_nonce":uuid.uuid4().hex,"gpus":gpus,"profile":profile,"process":None}
                s["active"]=a;j["status"]="STARTING"
            # Durable intent precedes process creation. Crash here blocks automatic retry.
            env=dict(os.environ)
            env.update({"CUDA_VISIBLE_DEVICES":",".join(gpus),"UGS_RUN_ID":a["run_id"],
                        "UGS_STAGE_ID":a["stage_id"],"UGS_STAGE_NONCE":a["stage_nonce"],
                        "UGS_RESULT_PATH":t["result_path"],"UGS_PROFILE_JSON":json.dumps(profile),
                        "UGS_MANAGED":"1","PYTHONDONTWRITEBYTECODE":"1"})
            try:
                with open(t["log_path"],"ab",buffering=0) as log:
                    child=subprocess.Popen(t["argv"],cwd=t["cwd"],env=env,stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                child_id=ident(child.pid)
                if not child_id: raise ValueError("Worker identity unavailable")
                with store.tx("worker_started") as s:
                    s["active"]["process"]=child_id
                    get_job(s,jid)["status"]="RUNNING"
            except Exception as e:
                with store.tx("launch_unknown") as s: s["recovery"]="Launch needs reconciliation: "+str(e)

def load_json(name):
    return json.loads(sys.stdin.read()) if name=="-" else json.loads(Path(name).read_text())
def summary(s):
    return {"version":VERSION,"protocol":PROTOCOL,"revision":s["revision"],
            "daemon":s["daemon"],"daemon_alive":alive(s["daemon"]["identity"]) if s["daemon"] else False,
            "dispatch_enabled":s["policy"]["dispatch_enabled"],"migration_cleared":s["policy"]["migration_cleared"],
            "ring":s["ring"],"served":s["served"],"round":s["round"],"active":s["active"],"recovery":s["recovery"],
            "jobs":[{k:j.get(k) for k in ("job_id","queue_seq","project_id","experiment_id","status","stage_index","error")} for j in s["jobs"]]}
def cli():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command",choices=["about","register","validate","submit","status","queue","job","policy-show","policy-set","start","serve","pause","resume","cancel","gate"])
    ap.add_argument("--file");ap.add_argument("--job-id");ap.add_argument("--decision",choices=["pass","fail"]);ap.add_argument("--evidence")
    a=ap.parse_args();node_check()
    if a.command=="serve": serve();return
    st=Store();out=None
    if a.command=="about":
        out={"version":VERSION,"protocol":PROTOCOL,"protocol_sha256":digest((ROOT/"protocol.md").read_bytes()),
             "root":str(ROOT),"transport":"existing SSH stdin/stdout only","listening_ports":[],
             "dispatch_default":False,"notes":"GPU adapters, reviewed UUID baselines, migration and acceptance required before enable"}
    elif a.command in ("status","queue","job","policy-show"):
        with st.tx() as s:
            out=s["policy"] if a.command=="policy-show" else get_job(s,a.job_id) if a.command=="job" else summary(s)
    elif a.command=="register":
        p=project_validate(load_json(a.file))
        with st.tx("register") as s:
            if p["project_id"] in s["projects"] and s["projects"][p["project_id"]]!=p:
                raise ValueError("Project already registered differently; independent version/review required")
            for name,old in s["projects"].items():
                if name!=p["project_id"] and exact_path(old["project_root"])==exact_path(p["project_root"]):
                    raise ValueError("Same project root cannot obtain another fairness identity")
            s["projects"][p["project_id"]]=p
            if p["project_id"] not in s["ring"]: s["ring"].append(p["project_id"])
            out={"registered":p["project_id"]}
    elif a.command in ("validate","submit"):
        m=load_json(a.file)
        with st.tx("submit" if a.command=="submit" else None) as s:
            p=s["projects"][m["project_id"]];job_validate(m,p)
            h=digest(json.dumps(m,sort_keys=True,separators=(",",":")).encode())
            key=":".join([m["project_id"],m["experiment_id"],m["submission_version"]])
            prev=next((j for j in s["jobs"] if j["idempotency_key"]==key),None)
            if prev:
                if prev["manifest_sha256"]!=h: raise ValueError("Same idempotency key has different content")
                out={"job_id":prev["job_id"],"status":prev["status"],"duplicate":True}
            elif a.command=="validate":
                out={"valid":True,"manifest_sha256":h,"dispatch_ready":all(t["adapter_accepted"] for t in m["stages"])}
            else:
                seq=len(s["jobs"])+1
                j={"job_id":"job-%06d"%seq,"queue_seq":seq,"project_id":m["project_id"],
                   "experiment_id":m["experiment_id"],"idempotency_key":key,"manifest_sha256":h,
                   "manifest":m,"allowed_roots":p["allowed_roots"],"stage_index":0,"results":[],
                   "run_id":uuid.uuid4().hex,"status":"QUEUED","submitted_at":stamp(),"paused":False}
                s["jobs"].append(j);out={"job_id":j["job_id"],"status":j["status"],"dispatch_enabled":s["policy"]["dispatch_enabled"]}
    elif a.command=="policy-set":
        p=allowed_policy(load_json(a.file))
        with st.tx("policy_set") as s:
            if s["active"] and p!=s["policy"]: raise ValueError("No policy hot edit during active experiment")
            s["policy"]=p;out={"policy_saved":True,"dispatch_enabled":p["dispatch_enabled"]}
    elif a.command in ("pause","resume","cancel","gate"):
        with st.tx(a.command) as s:
            j=get_job(s,a.job_id)
            if s["active"] and s["active"]["job_id"]==j["job_id"]:
                raise ValueError("Active experiment is protected; coordinate verified project stop first")
            if a.command=="pause":j["paused"]=True
            elif a.command=="resume":j["paused"]=False
            elif a.command=="cancel":j["status"]="CANCELLED"
            else:
                if j["status"]!="WAITING_GATE" or not a.evidence or not a.decision: raise ValueError("Gate needs waiting state, decision and evidence")
                j["gate_evidence"]=a.evidence;j["status"]="WAITING_CONTINUATION" if a.decision=="pass" else "GATE_REJECTED"
            out={"job_id":j["job_id"],"status":j["status"],"paused":j["paused"]}
    elif a.command=="start":
        with st.tx() as s:
            if s["daemon"] and alive(s["daemon"]["identity"]):
                print(json.dumps({"already_running":True,"pid":s["daemon"]["identity"]["pid"]}));return
        with (ROOT/"service.log").open("ab",buffering=0) as log:
            p=subprocess.Popen([PYTHON,"-B",str(Path(__file__).resolve()),"serve"],stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        time.sleep(1)
        with st.tx() as s:
            if not s["daemon"] or not alive(s["daemon"]["identity"]): raise ValueError("Coordinator did not acquire live identity; inspect service.log")
            out=summary(s)
    print(json.dumps(out,ensure_ascii=True,indent=2))
if __name__=="__main__":
    try: cli()
    except Exception as e:
        print(json.dumps({"error":type(e).__name__,"message":str(e)}),file=sys.stderr)
        sys.exit(2)
