"""Real process integration in an isolated, persistent CPU fixture journal."""
import json,os,sys,subprocess,time,uuid
from pathlib import Path
import ugs_exec_v2_3 as u
root=u.ROOT/"acceptance"/("integration-v23-"+uuid.uuid4().hex)
root.mkdir();output=root/"project";output.mkdir()
baseStore=u.Store
stub=root/"cpu-evidence.json";u.fresh_json(stub,{"fixture_only":True})
bootstrap="import sys;from pathlib import Path;sys.path.insert(0,%r);import ugs_exec_v2_3 as u;old=u.Store;u.Store=lambda:old(Path(%r));u.ROOT=Path(%r);"%(str(u.SELF.parent),str(root),str(root))
verifier_code=bootstrap+"c,r=u.verify_launch();print(__import__('json').dumps(r));sys.exit(c)"
cards=[{"uuid":g,"memory_mib":4,"util":0,"processes":[],"ecc_uncorrectable":0,"health_ok":True}
       for g in ("GPU-fixture-a","GPU-fixture-b")]
preload_code=bootstrap+"u.scan_devices=lambda:"+repr(cards)+";c,r=u.verify_launch(preload=True);print(__import__('json').dumps(r));sys.exit(c)"
adapter=output/"adapter.py"
source="""import os,sys,json,subprocess,time,hashlib
from pathlib import Path
r=subprocess.run([sys.executable,'-B','-c',%r],capture_output=True,text=True,timeout=5)
assert r.returncode==0,(r.stdout,r.stderr)
v=json.loads(r.stdout);assert v['allowed'] is True
preload=subprocess.run([sys.executable,'-B','-c',%r],capture_output=True,text=True,timeout=10)
assert preload.returncode==0,(preload.stdout,preload.stderr)
assert json.loads(preload.stdout)['preload'] is True
artifact=Path(%r);artifact.write_text('CPU-only output')
receipt={'run_id':os.environ['UGS_RUN_ID'],'stage_id':os.environ['UGS_STAGE_ID'],'stage_nonce':os.environ['UGS_STAGE_NONCE'],'outcome':'success','artifacts_verified':True,'gate_passed':True,'artifacts':[{'path':str(artifact),'sha256':hashlib.sha256(artifact.read_bytes()).hexdigest()}]}
with open(os.environ['UGS_RESULT_PATH'],'x') as f:json.dump(receipt,f)
kid=os.fork()
if kid==0:
    os.setsid()
    time.sleep(.4)
    os._exit(0)
"""%(verifier_code,preload_code,str(output/"artifact.txt"))
adapter.write_text(source)
stage={"stage_id":"cpu-stage","kind":"interface","argv":[sys.executable,"-B",str(adapter)],
 "cwd":str(output),"output_dir":str(output),"evaluation_dir":str(output),"log_path":str(output/"log"),
 "result_path":str(output/"receipt.json"),"source_sha256":{str(adapter):u.hash_file(adapter)},
 "requires_previous_gate":False}
manifest={"stages":[stage,dict(stage,stage_id="test",requires_previous_gate=True)]}
j={"job_id":"cpu-job","run_id":"cpu-run","manifest_sha256":u.digest(u.canonical(manifest)),
 "manifest":manifest,"status":"STARTING","stage_index":0,"results":[],"allowed_roots":[str(output)]}
a={"job_id":"cpu-job","run_id":"cpu-run","stage_id":"cpu-stage","stage_nonce":uuid.uuid4().hex,
 "generation":uuid.uuid4().hex,"manifest_sha256":j["manifest_sha256"],"boot":u.BOOT,
 "grant_deadline":time.monotonic()+15,"prepare_deadline":time.monotonic()+900,"gpus":["GPU-fixture-a","GPU-fixture-b"],"profile":{"gpu_count":2,"host_start_mib":1,"disk_min_gib":1},
 "guardian":None,"process":None}
store=baseStore(root)
with store.tx("fixture_intent") as s:
 s["jobs"]=[j];s["active"]=a;s["policy"]["dispatch_enabled"]=True;s["policy"]["migration_cleared"]=True
 s["daemon"]={"version":u.VERSION,"identity":u.identity(os.getpid()),"monotonic":time.monotonic()-400}
 s["executor_release"]={"version":u.VERSION,"cpu_accepted":True,"files":{str(u.SELF):u.hash_file(u.SELF)},
  "cpu_evidence":{"path":str(stub),"sha256":u.hash_file(stub)}}
with store.tx("fixture_baseline") as s:
 s["policy"]["baseline"]={c["uuid"]:{"memory_mib":4} for c in cards}
base=time.monotonic()-330
with (root/"resources.jsonl").open("x") as f:
 for i in range(12):f.write(json.dumps({"daemon_pid":os.getpid(),"boot":u.BOOT,"monotonic":base+i*30,"cards":cards})+"\n")
started=time.monotonic()
p=subprocess.Popen([sys.executable,"-B","-c",bootstrap+"u.guardian(%r)"%a["stage_nonce"]],start_new_session=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
out,err=p.communicate(timeout=15)
assert p.returncode==0,(out,err)
s=store.read();a=s["active"];j=s["jobs"][0]
assert a.get("authorized_pid")==a["process"]["pid"]
done=u.finalizable(a)
assert done and done["all_children_reaped"] and len(done["exits"])==2
assert time.monotonic()-started>=.4
without_preload=dict(a);without_preload.pop("preload_authorized_at",None)
try:u.receipt_verified(j,without_preload)
except ValueError as error:assert "no final preload authorization" in str(error)
else:raise AssertionError("actual receipt path accepted missing preload")
receipt=u.receipt_verified(j,a)
passed=0
for decision,expected in [(True,"WAITING_RESOURCES"),(False,"GATE_REJECTED"),(None,"WAITING_GATE")]:
 z=json.loads(json.dumps(s));r=dict(receipt,gate_passed=decision)
 u.complete_stage(z,z["active"],r)
 assert z["jobs"][0]["stage_index"]==1 and z["jobs"][0]["status"]==expected
 passed+=1
assert store.read()["jobs"][0]["stage_index"]==0 # isolated checks never mutated running state
(Path(output)/"artifact.txt").write_text("changed")
try:u.receipt_verified(j,a)
except ValueError:passed+=1
else:raise AssertionError("modified artifact accepted")
report={"passed":True,"cuda_used":False,"real_guardian_and_adapter":True,"live_verifier_success":True,
 "detached_descendant_reaped":True,"gate_branches":3,"modified_artifact_rejected":True,"missing_preload_rejected":True,
 "source_sha256":u.hash_file(u.SELF),"verifier_sha256":u.hash_file(u.VERIFY),"fixture":str(root),"at":u.stamp()}
u.fresh_json(root/"result.json",report);print(json.dumps(report))
