"""Human CLI presentation only. Execution and authorization remain in frozen ugs_exec_v3_1."""
import contextlib,io,json,shutil,sys,unicodedata,subprocess
import ugs_exec_v3_1 as core
import ugs_maintenance_v1_1 as maintenance
import ugs_help_zh_v1 as help_zh

def value(v):
    if v is None:return "-"
    if isinstance(v,bool):return "yes" if v else "no"
    if isinstance(v,(list,tuple)):return ", ".join(value(x) for x in v) if v else "-"
    return str(v)

def width(s):
    return sum(2 if unicodedata.east_asian_width(c) in ("W","F") else 1 for c in s)

def chunks(text,limit):
    result=[]
    for line in value(text).splitlines() or [""]:
        current="";size=0
        for c in line:
            if ord(c)<32:c=" "
            n=width(c)
            if size+n>limit and current:result.append(current);current="";size=0
            current+=c;size+=n
        result.append(current)
    return result or [""]

def table(headers,rows):
    rows=[[value(c) for c in row] for row in rows]
    widths=[min(52,max([width(h)]+[max(map(width,c.splitlines() or [""])) for c in col])) for h,col in zip(headers,zip(*rows))] if rows else [len(h) for h in headers]
    budget=max(80,shutil.get_terminal_size((160,24)).columns)-3*(len(headers)-1)
    while sum(widths)>budget:
        i=max(range(len(widths)),key=lambda i:widths[i])
        if widths[i]<=12:break
        widths[i]-=1
    def line(cells):return " | ".join(c+" "*(w-width(c)) for c,w in zip(cells,widths)).rstrip()
    print(line(headers));print("-+-".join("-"*w for w in widths))
    for row in rows:
        cells=[chunks(c,w) for c,w in zip(row,widths)]
        for i in range(max(map(len,cells))):
            print(line([c[i] if i<len(c) else "" for c in cells]))
    if not rows:print("(none)")

def flatten(data,prefix=""):
    out=[]
    if isinstance(data,dict):
        for key,v in data.items():out+=flatten(v,prefix+"."+key if prefix else key)
    elif isinstance(data,list) and any(isinstance(v,(dict,list)) for v in data):
        for i,v in enumerate(data):out+=flatten(v,prefix+"["+str(i)+"]")
    else:out.append((prefix,value(data)))
    return out

_gpu_indices=None
_allocations=None
def gpu_indices():
    global _gpu_indices
    if _gpu_indices is None:
        # Called only after the frozen CLI has verified gpu07 and kzhang19.
        core.node_check()
        try:
            result=subprocess.run(["nvidia-smi","--query-gpu=index,uuid","--format=csv,noheader,nounits"],
                                  capture_output=True,text=True,timeout=10)
        except (OSError,subprocess.TimeoutExpired):
            result=None
        _gpu_indices={}
        if result is not None and result.returncode==0:
            for line in result.stdout.splitlines():
                fields=[x.strip() for x in line.split(",")]
                if len(fields)==2 and fields[0].isdigit() and fields[1].startswith("GPU-"):
                    _gpu_indices[fields[1]]=fields[0]
        if not _gpu_indices:print("ugs: GPU index lookup unavailable; IDs shown as unknown, UUIDs remain authoritative.",file=sys.stderr)
    return _gpu_indices

def allocations():
    global _allocations
    if _allocations is None:
        with core.Store().tx() as state:
            _allocations={jid:list(a.get("gpus",[])) for jid,a in state["active_runs"].items()}
    return _allocations

def physical_ids(gpus):
    if not gpus:return "-"
    mapping=gpu_indices()
    return ",".join(mapping.get(g,"?") for g in gpus)

def jobs(rows):
    allocated=allocations()
    table(["JOB ID","PROJECT","EXPERIMENT","STATUS","STAGE","GPU ID","PAUSED","QUEUE"],
          [[j.get(k) for k in ("job_id","project_id","experiment_id","status","stage_id")]+
           [physical_ids(allocated.get(j.get("job_id"),[])),j.get("paused"),j.get("queue_seq")] for j in rows])

def render(data,args):
    obj=args[0] if args else "";action=args[1] if len(args)>1 else ""
    if obj=="experiment" and action=="list":jobs(data)
    elif obj=="project" and action=="list":
        table(["PROJECT","OWNER","CONTRACT","PAUSED","ROOT"],
              [[k,v.get("owner"),v.get("adapter_contract"),v.get("paused",False),v.get("project_root")] for k,v in data.items()])
    elif obj=="scheduler" and action in ("status","get","start") and "version" in data:
        fields=["version","daemon_alive","max_experiment","active_count","scheduler_paused","draining","recovery"]
        table(["SETTING","VALUE"],[(k,data.get(k)) for k in fields]+[("daemon_pid",(data.get("daemon") or {}).get("identity",{}).get("pid"))])
        print("\nACTIVE EXPERIMENTS")
        table(["JOB ID","STAGE","GPU ID","GPU UUIDS","CONTINUATION","STOP REQUESTED"],
          [[a.get("job_id"),a.get("stage_id"),physical_ids(a.get("gpus",[])),a.get("gpus"),a.get("continuation",False),bool(a.get("cancel_requested"))] for a in data.get("active",[])])
        print("\nEXPERIMENTS");jobs(data.get("jobs",[]))
    elif obj=="resource" and action=="status":
        observation=data.get("observation") or {};alloc=data.get("allocations") or {}
        table(["SETTING","VALUE"],[("observed_at",observation.get("at")),("max_experiment",data.get("max_experiment")),("active_count",data.get("active_count"))])
        print()
        table(["GPU ID","STATE","IDLE s","USED MiB","UTIL %","HEALTH","PIDS","JOB ID"],
         [[physical_ids([c.get("uuid")]),data.get("qualification",{}).get(c.get("uuid"),{}).get("reason","UNKNOWN"),
           int(data.get("qualification",{}).get(c.get("uuid"),{}).get("idle_seconds",0)),
           c.get("memory_mib"),c.get("util"),
           "OK" if c.get("health_ok") is True and not c.get("ecc_uncorrectable") else "BLOCKED",
           [p.get("pid") for p in c.get("processes",[])],alloc.get(c.get("uuid"),{}).get("job_id")]
          for c in observation.get("cards",[])])
        if data.get("reservations"):
            print("\nUGS AVOIDANCE RESERVATIONS")
            table(["GPU ID","GPU UUID","REASON"],[[physical_ids([g]),g,v["reason"]] for g,v in data["reservations"].items()])
    elif obj=="resource" and action=="reservations":
        table(["GPU ID","GPU UUID","REASON","CREATED AT"],[[physical_ids([v["gpu_uuid"]]),v["gpu_uuid"],v.get("reason"),v.get("created_at")] for v in data])
    elif obj=="experiment" and action=="show":
        fields=["job_id","project_id","experiment_id","queue_seq","run_id","status","paused","stage_index","manifest_sha256","error","terminal_reason"]
        table(["FIELD","VALUE"],[(k,data.get(k)) for k in fields]+[("gpu_ids",physical_ids(allocations().get(data["job_id"],[]))),("gpu_uuids",allocations().get(data["job_id"],[]))])
        stages=data.get("manifest",{}).get("stages",[])
        print("\nSTAGES")
        table(["STAGE","KIND","REQUIRES GATE","OUTPUT"],
              [[t.get(k) for k in ("stage_id","kind","requires_previous_gate","output_dir")] for t in stages])
        pending=data.get("pending_revision")
        if pending:
            print("\nPENDING REVISION");table(["FIELD","VALUE"],[(k,pending.get(k)) for k in ("sha256","from_stage_index","at")])
        if data.get("results"):
            print("\nSTAGE RESULTS");table(["FIELD","VALUE"],flatten(data["results"]))
        print("\nUse --json for the complete frozen manifest, evidence and revision history.")
    elif obj=="experiment" and action=="logs":
        print("LOG: "+data["path"])
        print("\n".join(data["lines"]) if data["lines"] else "(no log output)")
    elif obj=="experiment" and action=="events":
        fields=["at","event","status","stage_index","paused","error","terminal_reason"]
        table([k.upper() for k in fields],[[r.get(k) for k in fields] for r in data])
    else:table(["FIELD","VALUE"],flatten(data))

def main():
    raw=sys.argv[1:];as_json="--json" in raw;args=[x for x in raw if x!="--json"]
    if not args or any(x in args for x in ("-h","--help")) or args[0]=="help" or (len(args)==1 and args[0] in help_zh.DATA["catalog"]):
        return help_zh.show(args,table,as_json)
    sys.argv=[sys.argv[0]]+args
    if args and args[0]=="internal":return core.cli()
    output=io.StringIO()
    try:
        with contextlib.redirect_stdout(output):
            if args and args[0]=="maintenance":maintenance.cli(args[1:])
            else:core.cli()
    except SystemExit as e:
        print(output.getvalue(),end="")
        if e.code in (None,0) and any(x in args for x in ("-h","--help")):
            print("\nOutput defaults to tables. Add --json for machine-readable JSON.")
        raise
    except Exception as e:
        if as_json:print(json.dumps({"error":type(e).__name__,"message":str(e)}),file=sys.stderr)
        else:print("ugs: "+str(e),file=sys.stderr)
        return 2
    text=output.getvalue()
    if as_json:print(text,end="")
    elif text.strip():render(json.loads(text),args)
    return 0
if __name__=="__main__":sys.exit(main())
