"""Real Linux core/Store/flock/process integration; synthetic GPU telemetry only.
Run from an isolated bundle containing source/ and candidates/preload_v3_2/.
Never import a project adapter or access production scheduler state.
"""
import contextlib,copy,json,os,runpy,subprocess,sys,time,uuid
from pathlib import Path
HERE=Path(__file__).resolve(); BUNDLE=HERE.parents[2]
sys.path.insert(0,str(BUNDLE/'source'))
import ugs_exec_v3_1 as u
BASE_STORE=u.Store


def setup(root):
    root=Path(root).resolve()
    assert root.is_relative_to(BUNDLE/'fixtures')
    u.ROOT=root;u.Store=lambda:BASE_STORE(root)
    u.PROTOCOL_FILE=BUNDLE/'protocol-v2.md'
    # Fail loudly if any test code accidentally uses production scheduler state.
    def audit(event,args):
        if event=='open' and isinstance(args[0],(str,bytes)):
            p=Path(os.fsdecode(args[0]))
            if p.name in {'events.jsonl','resources.jsonl','state.lock','observation.lock','service-events.jsonl'}:
                if not p.resolve().is_relative_to(BUNDLE):raise RuntimeError('Production state access prohibited')
    sys.addaudithook(audit)
    u.scan_devices=lambda:copy.deepcopy(json.loads((root/'cards.json').read_text()))
    return root


def wait(path):
    until=time.monotonic()+15
    while not path.exists():
        if time.monotonic()>until:raise TimeoutError(str(path))
        time.sleep(.01)


def cmd(role,root,mode):return [sys.executable,'-B',str(HERE),role,str(root),mode]


def verifier(root,mode,preload):
    root=setup(root)
    original_append=u.append_json;original_observe=u.observe;original_budget=u.budgets_ready
    count=[0];lockers=[]
    def observe(*args):
        count[0]+=1
        result=original_observe(*args)
        if count[0]==1 and mode in ('slow_lock','cancel'):
            lockers.append(subprocess.Popen(cmd('locker',root,mode)))
            wait(root/'locker-ready')
        return result
    def append(path,value):
        if mode=='slow_commit' and Path(path).name=='events.jsonl' and value.get('event')=='preload_authorized':time.sleep(2.2)
        if mode=='slow_audit' and Path(path).name=='service-events.jsonl':time.sleep(2.2)
        original_append(path,value)
    u.observe=observe;u.append_json=append
    u.budgets_ready=lambda *a,**k:False if mode=='budget_change' and count[0]>=2 else original_budget(*a,**k)
    sys.argv=[str(HERE.with_name('verify_launch_v3_2.py'))]+(['--preload'] if preload else [])
    try:runpy.run_path(sys.argv[0],run_name='__main__')
    finally:
        for p in lockers:p.wait(timeout=15)


def adapter(root,mode):
    root=setup(root);wait(root/'registered')
    with u.Store().tx() as s:
        a=s['active_runs']['job-fixture'];j=u.get_job(s,'job-fixture');env=u.expected_env(j,a)
    if mode=='bad_identity':env['UGS_GENERATION']='wrong'
    def call(preload):
        p=subprocess.run(cmd('verifier',root,mode)+(['preload'] if preload else []),env=env,text=True,capture_output=True,timeout=20)
        return {'code':p.returncode,'result':json.loads(p.stdout),'stderr':p.stderr}
    initial=call(False);preload=call(True) if initial['code']==0 else None
    worker_started=False
    if preload and preload['code']==0 and preload['result'].get('allowed') is True:
        # CPU worker stand-in proves adapter only starts a child after both permits.
        worker=subprocess.run([sys.executable,'-B','-c','from pathlib import Path; import sys; Path(sys.argv[1]).write_text("CPU only")',str(root/'cpu-worker-started')],check=True)
        worker_started=True
    repeat=call(True) if mode=='reuse' and worker_started else None
    (root/'adapter-result.json').write_text(json.dumps({'initial':initial,'preload':preload,'repeat':repeat,'cpu_worker_started':worker_started},indent=2))


def guardian(root,mode):
    root=setup(root)
    child=subprocess.Popen(cmd('adapter',root,mode))
    try:
        with u.Store().tx('fixture_process_registration') as s:
            a=s['active_runs']['job-fixture'];a['guardian']=u.identity(os.getpid());a['process']=u.identity(child.pid)
        (root/'registered').touch()
        code=child.wait(timeout=40)
        (root/'guardian-result.json').write_text(json.dumps({'adapter_exit':code,'all_children_reaped':True}))
        if code:raise RuntimeError('Adapter failed')
    finally:
        if child.poll() is None:child.kill();child.wait()


def locker(root,mode):
    root=setup(root)
    with u.Store().tx('fixture_concurrent_cancel' if mode=='cancel' else None) as s:
        if mode=='cancel':s['active_runs']['job-fixture']['cancel_requested']={'reason':'CPU integration cancellation','terminal':'CANCELLED'}
        (root/'locker-ready').touch()
        time.sleep(5)


def initialize(root):
    root.mkdir(parents=True)
    setup(root)
    proof=root/'cpu-evidence.json';proof.write_text('{"passed":true,"fixture_only":true}')
    cards=[{'uuid':g,'name':'A100 fixture','health_ok':True,'ecc_uncorrectable':0,'processes':[],
            'util':0,'memory_util':0,'memory_mib':4,'memory_reserved_mib':620,'memory_total_mib':40960,'driver_version':'fixture'} for g in ['fixture-'+str(i) for i in range(8)]]
    (root/'cards.json').write_text(json.dumps(cards))
    files=[BUNDLE/'source/ugs_exec_v3_1.py',HERE,HERE.with_name('preload.py'),HERE.with_name('verify_launch_v3_2.py')]
    profile={'gpu_count':2,'host_start_mib':1,'host_running_mib':1,'disk_min_gib':1}
    stage={'stage_id':'cpu-stage','source_sha256':{str(p):u.hash_file(p) for p in files},'result_path':str(root/'stage-result.json'),
           'output_dir':str(root),'evaluation_dir':str(root)}
    manifest={'stages':[stage]};sha=u.digest(u.canonical(manifest))
    active={'job_id':'job-fixture','run_id':'fixture-run','stage_id':'cpu-stage','generation':uuid.uuid4().hex,'stage_nonce':uuid.uuid4().hex,
            'manifest_sha256':sha,'gpus':[c['uuid'] for c in cards[:2]],'profile':profile,'boot':u.BOOT,'grant_deadline':time.monotonic()+60,'prepare_deadline':time.monotonic()+90}
    with u.Store().tx('fixture_initialized') as s:
        s['policy'].update(dispatch_enabled=True,migration_cleared=True,gpu_inventory=[c['uuid'] for c in cards],baseline={c['uuid']:{'reviewed':True,'boot':u.BOOT,
            'evidence':str(proof),'evidence_sha256':u.hash_file(proof),**{k:c[k] for k in ['driver_version','memory_mib','memory_reserved_mib','memory_total_mib']}} for c in cards})
        s['daemon']={'version':u.VERSION,'identity':u.identity(os.getpid()),'monotonic':time.monotonic()-400}
        s['executor_release']={'version':u.VERSION,'cpu_accepted':True,'files':{str(p):u.hash_file(p) for p in files},'cpu_evidence':{'path':str(proof),'sha256':u.hash_file(proof)}}
        s['jobs']=[{'job_id':'job-fixture','manifest':manifest,'manifest_sha256':sha,'stage_index':0,'status':'RUNNING'}]
        s['active_runs']={'job-fixture':active}
    now=time.monotonic()
    for offset in range(-330,1,30):
        rec=u.observation_record(s['policy'],s['daemon'],'synthetic_cpu_history')
        rec.update(cards=cards,valid=True,monotonic=now+offset,started_monotonic=now+offset-.01)
        u.append_json(root/'resources.jsonl',rec)


def main():
    assert sys.platform.startswith('linux');u.node_check()
    rows=[]
    for mode in ['success','slow_lock','cancel','slow_commit','slow_audit','bad_identity','budget_change','reuse']:
        root=BUNDLE/'fixtures'/mode;initialize(root)
        p=subprocess.run(cmd('guardian',root,mode),start_new_session=True,capture_output=True,text=True,timeout=45)
        assert p.returncode==0,(mode,p.stdout,p.stderr)
        r=json.loads((root/'adapter-result.json').read_text())
        expected=mode in ('success','slow_lock','reuse')
        assert r['cpu_worker_started']==expected,(mode,r)
        if mode=='slow_lock':
            phases=r['preload']['result']['timings']
            assert next(x['seconds'] for x in phases if x['phase']=='final_state_lock_and_replay')>2
        if mode=='reuse':assert r['repeat']['code']==2
        if mode=='bad_identity':assert r['initial']['result']['reason']=='IDENTITY_MISMATCH'
        if not expected and mode!='bad_identity':assert r['preload']['code']==2
        assert json.loads((root/'guardian-result.json').read_text())['adapter_exit']==0
        rows.append({'case':mode,'passed':True,'cpu_worker_started':r['cpu_worker_started'],'result':r})
        print(mode,'passed',flush=True)
    report={'passed':True,'cases':rows,'tests':len(rows),'real_linux_store_locks_identities':True,'synthetic_gpu_telemetry':True,
            'cuda_used':False,'production_state_accessed':False,'project_adapter_used':False,
            'adapter_protocol':'real guardian -> CPU fixture adapter -> initial verifier -> preload verifier -> conditional CPU worker',
            'bundle':str(BUNDLE),'candidate_files':{p.name:u.hash_file(p) for p in [HERE,HERE.with_name('preload.py'),HERE.with_name('verify_launch_v3_2.py')]}}
    (BUNDLE/'linux-integration-result.json').write_text(json.dumps(report,indent=2))
    print(json.dumps({k:v for k,v in report.items() if k!='cases'}))

if __name__=='__main__':
    if len(sys.argv)==1:main()
    else:
        role,root,mode=sys.argv[1:4]
        if role=='verifier':verifier(root,mode,len(sys.argv)>4)
        else:globals()[role](root,mode)
