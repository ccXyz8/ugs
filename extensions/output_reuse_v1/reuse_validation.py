"""Original full manifest validation with only fresh-receipt restriction removed.
Used exclusively after explicit artifact-reuse policy validation.
"""
from pathlib import Path

def validate_manifest(c,m,p):
    for k in ("project_id","experiment_id","submission_version","protocol","protocol_sha256","stages"):
        if not m.get(k): raise ValueError("Missing manifest field "+k)
    if m["protocol"]!=c.PROTOCOL: raise ValueError("Protocol version mismatch")
    if m["protocol_sha256"]!=c.digest(c.PROTOCOL_FILE.read_bytes()):
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
        if c.exact_path(t["argv"][0])!=c.exact_path(p["python"]):
            raise ValueError("Use the registered fixed interpreter as argv[0]")
        for k in ("cwd","output_dir","evaluation_dir","log_path","result_path"):
            if not c.inside(t[k],p["allowed_roots"]): raise ValueError("Project path violation: "+k)
        for k in ("cwd","output_dir","evaluation_dir"):
            if not c.exact_path(t[k]).is_dir(): raise ValueError("Required project directory missing: "+k)
        if not c.inside(t["log_path"],[t["output_dir"]]) or not c.inside(t["result_path"],[t["output_dir"]]):
            raise ValueError("Stage log and result receipt must be c.inside its output_dir")
        if not Path(t["log_path"]).parent.is_dir() or not Path(t["result_path"]).parent.is_dir():
            raise ValueError("Log/result parent must already exist")
        # Existing receipt is archived at dispatch; never treated as this run's result.
        if not isinstance(t["source_sha256"],dict) or not t["source_sha256"]:
            raise ValueError("Source hashes required")
        for name,h in t["source_sha256"].items():
            if not c.inside(name,p["allowed_roots"]) or c.digest(Path(name).read_bytes())!=h:
                raise ValueError("Source identity mismatch")
        if len(t["resource_profiles"])<1: raise ValueError("Explicit resolved profiles required")
        for r in t["resource_profiles"]: c.profile_validate(r)
        if not isinstance(t["adapter_accepted"],bool) or not isinstance(t["requires_previous_gate"],bool):
            raise ValueError("Boolean adapter/gate fields required")
        if i==0 and t["requires_previous_gate"]: raise ValueError("First stage cannot require an absent gate")
    if any(t["kind"]=="train" for t in stages) and not m.get("evaluation_plan"):
        raise ValueError("Training needs explicit validation/test plan or NOT_APPLICABLE reasons")
    return m
