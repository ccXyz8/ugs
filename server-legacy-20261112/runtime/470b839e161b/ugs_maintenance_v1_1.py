"""UGS-owned log retention; never traverses project paths or removes current jobs."""
import argparse,collections,contextlib,copy,fcntl,gzip,hashlib,json,os,signal,stat,subprocess,sys,time,uuid
from pathlib import Path
import ugs_exec_v3_1 as core
ROOT=core.ROOT
MIB=1024*1024
DEFAULT={"interval_seconds":1800,"ordinary_days":7,"resource_max_mib":64,"service_max_mib":16,
         "journal_trigger_mib":64,"journal_target_mib":32,"archive_days":30,"archive_max_mib":128}
def stamp():return core.stamp()
def safe(p):
    p=Path(p)
    if p.parent!=ROOT and p.parent!=ROOT/"maintenance-archives":raise ValueError("Not an allowed UGS maintenance path")
    if p.is_symlink():raise ValueError("Refusing symlink")
    if p.exists():
        st=p.stat()
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_nlink!=1:raise ValueError("Unsafe log identity")
    return p
def atomic(p,b):
    p=safe(p);q=safe(p.with_name(p.name+".pending-"+uuid.uuid4().hex))
    fd=os.open(str(q),os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"wb") as f:f.write(b);f.flush();os.fsync(f.fileno())
    os.replace(q,p)
    fd=os.open(str(p.parent),os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)
@contextlib.contextmanager
def lock(name):
    p=safe(ROOT/name)
    with p.open("a+b") as f:
        fcntl.flock(f,fcntl.LOCK_EX);yield
def policy():
    p=safe(ROOT/"retention-policy.json")
    v=json.loads(p.read_text()) if p.exists() else dict(DEFAULT)
    if set(v)!=set(DEFAULT):raise ValueError("Unknown retention policy keys")
    if any(type(x)!=int or x<=0 for x in v.values()):raise ValueError("Retention values must be positive integers")
    if not 60<=v["interval_seconds"]<=86400:raise ValueError("Interval must be 60..86400 seconds")
    if v["journal_target_mib"]>=v["journal_trigger_mib"]:raise ValueError("Journal target must be below trigger")
    return v
def archive_dir():
    p=ROOT/"maintenance-archives"
    if p.is_symlink():raise ValueError("Archive directory must not be a symlink")
    p.mkdir(mode=0o700,exist_ok=True)
    if not p.is_dir() or p.stat().st_uid!=os.getuid():raise ValueError("Unsafe archive directory")
    return p
def archive_files():
    d=ROOT/"maintenance-archives"
    if not d.exists():return []
    if d.is_symlink():raise ValueError("Unsafe archive directory")
    out=[]
    for p in d.iterdir():
        if p.name.startswith("journal-") and p.name.endswith(".jsonl.gz"):
            safe(p);out.append(p)
    return sorted(out,key=lambda p:(p.stat().st_mtime,p.name))
def archive_removals(pol,now,extra=0):
    files=archive_files();total=sum(p.stat().st_size for p in files)+extra;remove=[]
    for p in files:
        if now-p.stat().st_mtime>pol["archive_days"]*86400 or total>pol["archive_max_mib"]*MIB:
            remove.append(p);total-=p.stat().st_size
    return remove
def trim_jsonl(p,days,cap,now):
    p=safe(p)
    if not p.exists():return None
    kept=collections.deque();size=0;cutoff=now-days*86400
    with p.open("rb") as f:
        for line in f:
            if not line.endswith(b"\n"):raise ValueError("Incomplete log tail: "+p.name)
            row=json.loads(line)
            at=row.get("at")
            if not at:raise ValueError("Missing log timestamp: "+p.name)
            when=__import__("datetime").datetime.fromisoformat(at.replace("Z","+00:00")).timestamp()
            if when>=cutoff:
                kept.append(line);size+=len(line)
                while size>cap and len(kept)>1:size-=len(kept.popleft())
    data=b"".join(kept)
    if len(data)>cap:raise ValueError("Single record exceeds retention budget")
    return data if len(data)!=p.stat().st_size else None
def compact_journal(pol,now,dry):
    p=safe(ROOT/"events.jsonl")
    if not p.exists() or p.stat().st_size<=pol["journal_trigger_mib"]*MIB:return None
    with core.Store(ROOT).tx() as latest:
        original=p.read_bytes()
        rows=[json.loads(line) for line in original.splitlines()]
        if not original.endswith(b"\n"):raise ValueError("Incomplete state journal")
        # Keep the most recent raw events within target; never remove jobs or manifests.
        tail=[];size=0
        for row,line in zip(reversed(rows),reversed(original.splitlines(keepends=True))):
            if size+len(line)>pol["journal_target_mib"]*MIB and tail:break
            tail.append(row);size+=len(line)
        tail.reverse()
        if len(tail)==len(rows):return None
        epoch=uuid.uuid4().hex
        payload=gzip.compress(original,compresslevel=6,mtime=0)
        if len(payload)>pol["archive_max_mib"]*MIB:raise ValueError("Compressed audit exceeds archive budget; no state discarded")
        out=[]
        for n,row in enumerate(tail,1):
            state=copy.deepcopy(row["state"])
            state["journal_epoch"]=epoch;state["revision"]=n
            rec={**row,"state":state,"origin_revision":row["state"]["revision"],
                 "origin_epoch":row["state"].get("journal_epoch","original")}
            if n==1:
                rec["checkpoint"]={"previous_journal_sha256":core.digest(original),"previous_last_revision":latest["revision"],
                                   "created_at":stamp(),"retained_all_current_jobs":True}
            rec["sha256"]=core.digest(json.dumps(state,sort_keys=True,separators=(",",":")).encode())
            out.append(json.dumps(rec,sort_keys=True,separators=(",",":")).encode()+b"\n")
        new=b"".join(out)
        final=copy.deepcopy(tail[-1]["state"]);final["revision"]=len(tail);final["journal_epoch"]=epoch
        expected=copy.deepcopy(latest);expected["revision"]=len(tail);expected["journal_epoch"]=epoch
        assert final==expected,"Current state changed in compaction"
        report={"before_bytes":len(original),"after_bytes":len(new),"retained_events":len(tail),
                "archive_bytes":len(payload),"epoch":epoch,"all_jobs_preserved":True}
        if dry:return report
        archive_dir()
        for old in archive_removals(pol,now,len(payload)):safe(old).unlink()
        archive=safe(ROOT/"maintenance-archives"/("journal-"+epoch+".jsonl.gz"))
        atomic(archive,payload)
        assert core.digest(gzip.decompress(archive.read_bytes()))==core.digest(original)
        atomic(p,new)
        actual=core.Store(ROOT).read()
        assert actual==expected
        report["archive"]=str(archive)
        return report
def run(dry=False):
    pol=policy();now=time.time();actions=[]
    with lock("maintenance.lock"):
        # Shared state -> observation lock order matches the executor.
        with core.Store(ROOT).tx():
            with lock("observation.lock"):
                for name,cap in [("resources.jsonl",pol["resource_max_mib"]*MIB),("service-events.jsonl",4*MIB)]:
                    p=ROOT/name;data=trim_jsonl(p,pol["ordinary_days"],cap,now)
                    if data is not None:
                        actions.append({"file":str(p),"before_bytes":p.stat().st_size,"after_bytes":len(data)})
                        if not dry:atomic(p,data)
        journal=compact_journal(pol,now,dry)
        if journal:actions.append({"journal_compaction":journal})
        p=safe(ROOT/"service.log")
        if p.exists() and (p.stat().st_size>pol["service_max_mib"]*MIB or now-p.stat().st_mtime>pol["ordinary_days"]*86400):
            before=p.stat().st_size
            # Existing guardians hold this inode open. Copytruncate diagnostics only;
            # structured state and GPU evidence above always use locks + atomic replacement.
            keep=min(before,pol["service_max_mib"]*MIB//2) if now-p.stat().st_mtime<=pol["ordinary_days"]*86400 else 0
            with p.open("rb") as f:
                f.seek(max(0,before-keep));data=f.read()
            if keep and before>keep:
                pos=data.find(b"\n");data=data[pos+1:] if pos>=0 else b""
            actions.append({"file":str(p),"before_bytes":before,"after_bytes":len(data),"method":"copytruncate_diagnostic"})
            if not dry:
                with p.open("r+b") as f:f.seek(0);f.write(data);f.truncate();f.flush();os.fsync(f.fileno())
        for p in archive_removals(pol,now):
            actions.append({"delete_archive":str(p),"bytes":p.stat().st_size})
            if not dry:safe(p).unlink()
        result={"at":stamp(),"dry_run":dry,"actions":actions,"policy":pol,"scope":"UGS-owned logs only; no project files, sources, pinned acceptance or current jobs"}
        if not dry:
            atomic(ROOT/"retention-last-run.json",(json.dumps(result,indent=2)+"\n").encode())
        return result
def daemon_identity():
    p=safe(ROOT/"maintenance-daemon.json")
    return json.loads(p.read_text()).get("identity") if p.exists() else None
def status():
    pol=policy();ident=daemon_identity();sizes={}
    for name in ["events.jsonl","resources.jsonl","service.log","service-events.jsonl"]:
        p=safe(ROOT/name);sizes[name]=p.stat().st_size if p.exists() else 0
    last=ROOT/"retention-last-run.json"
    return {"maintenance_alive":bool(ident and core.live(ident)),"daemon":ident,"policy":pol,"bytes":sizes,
            "archive_bytes":sum(p.stat().st_size for p in archive_files()),
            "last_error":json.loads((ROOT/"retention-error.json").read_text()) if (ROOT/"retention-error.json").exists() else None,
            "last_run":json.loads(last.read_text()) if last.exists() else None}
def serve():
    p=safe(ROOT/"maintenance-daemon.lock")
    with p.open("a+b") as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        atomic(ROOT/"maintenance-daemon.json",(json.dumps({"identity":core.identity(os.getpid()),"started":stamp()})+"\n").encode())
        while True:
            try:run()
            except Exception as e:
                atomic(ROOT/"retention-error.json",(json.dumps({"at":stamp(),"error":str(e)})+"\n").encode())
            time.sleep(policy()["interval_seconds"])
def cli(args=None):
    parser=argparse.ArgumentParser(description="UGS service log maintenance; excludes project artifacts")
    parser.add_argument("action",choices=["status","run","start","stop","set","serve"])
    parser.add_argument("setting",nargs="?");parser.add_argument("--dry-run",action="store_true")
    a=parser.parse_args(args);core.node_check()
    if a.dry_run and a.action!="run":raise ValueError("--dry-run is only valid for maintenance run")
    if a.setting is not None and a.action!="set":raise ValueError("Only maintenance set accepts KEY=INTEGER")
    if os.getuid()!=2087:raise ValueError("Wrong account")
    if a.action=="serve":serve();return
    if a.action=="status":out=status()
    elif a.action=="run":out=run(a.dry_run)
    elif a.action=="set":
        if not a.setting or "=" not in a.setting:raise ValueError("Use maintenance set KEY=INTEGER")
        k,v=a.setting.split("=",1)
        with lock("maintenance.lock"):
            p=policy()
            if k not in p or not v.isdecimal() or int(v)<=0:raise ValueError("Unknown/invalid policy setting")
            p[k]=int(v)
            if not 60<=p["interval_seconds"]<=86400 or p["journal_target_mib"]>=p["journal_trigger_mib"]:raise ValueError("Invalid interval/journal thresholds")
            atomic(ROOT/"retention-policy.json",(json.dumps(p,indent=2)+"\n").encode())
        out={"policy":p,"takes_effect":"next maintenance run; restart maintenance to change current sleep"}
    elif a.action=="start":
        ident=daemon_identity()
        if not ident or not core.live(ident):
            subprocess.Popen([core.PYTHON,"-B",str(Path(__file__).resolve()),"serve"],stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            time.sleep(.5)
        out=status()
        if not out["maintenance_alive"]:raise ValueError("Maintenance daemon failed to start")
    else:
        ident=daemon_identity()
        if ident and core.live(ident):core.exact_signal(ident,signal.SIGTERM)
        out={"maintenance_stop_requested":True,"experiments_unchanged":True}
    print(json.dumps(out))
if __name__=="__main__":
    try:cli()
    except Exception as e:print(str(e),file=sys.stderr);sys.exit(2)
