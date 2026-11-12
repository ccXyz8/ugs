"""New-submission preflight contract and bounded, identity-checked failure views.
Does not change frozen jobs, scheduling, GPU admission or automatic retry policy.
"""
import json
from pathlib import Path
import ugs_exec_v3_1 as core

PROJECTS = {'ms-swift-main', 'time-r1'}
VERIFIER = str(core.ROOT/'candidates/preload_v3_2/verify_launch_v3_2.py')
DEPENDENCY = str(core.ROOT/'candidates/preload_v3_2/preload.py')
VERIFIER_SHA = '26e5e077924f67837c01bf0cee38b34f8bd331661dea9c48b3dd53560b5682a5'
DEPENDENCY_SHA = '6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f'
VERIFIER_SPEC = {'path': VERIFIER, 'sha256': VERIFIER_SHA,
    'dependency_path': DEPENDENCY, 'dependency_sha256': DEPENDENCY_SHA,
    'candidate_revision': 'ugs-preload-v3.2-candidate1',
    'initial_timeout_seconds': 10, 'preload_timeout_seconds': 20}
CHECKS = {'final_argv_parse', 'generation_uuid_rank', 'authorization_response',
          'model_interface', 'storage_preflight', 'launch_boundary', 'failure_record'}
LIMIT = 65536


def read_json(path):
    with Path(path).open('rb') as stream:
        data = stream.read(LIMIT+1)
    if len(data)>LIMIT:
        raise ValueError('Evidence exceeds 64 KiB: '+str(path))
    value=json.loads(data)
    if not isinstance(value,dict):raise ValueError('Evidence must be an object')
    return value,core.digest(data)


def validate_preflight(manifest, project, from_index=0):
    if manifest.get('project_id') not in PROJECTS:return
    for stage in manifest['stages'][from_index:]:
        spec=manifest['admission']['stages'][stage['stage_id']]
        report=json.loads(Path(spec['cpu']['path']).read_text())
        pre=report.get('preflight',{})
        if pre.get('contract')!='ugs-preflight-v1' or pre.get('stage_id')!=stage['stage_id']:
            raise ValueError('Missing stage-bound ugs-preflight-v1: '+stage['stage_id'])
        if pre.get('stage_sha256')!=core.digest(core.canonical(stage)):
            raise ValueError('Preflight stage SHA mismatch: rerun checks for the final stage configuration')
        if pre.get('verifier')!=VERIFIER_SPEC:
            raise ValueError('Use accepted verifier PIN/revision and initial=10s, preload=20s')
        for path,sha in [(VERIFIER,VERIFIER_SHA),(DEPENDENCY,DEPENDENCY_SHA)]:
            if report.get('source_sha256',{}).get(path)!=sha or core.hash_file(path)!=sha:
                raise ValueError('CPU evidence not bound to accepted verifier dependency: '+path)
        failure=stage.get('failure_record')
        if (not isinstance(failure,dict) or failure.get('contract')!='ugs-failure-v1'
                or pre.get('failure_record')!=failure):
            raise ValueError('Stage/CPU failure_record contract mismatch')
        path=failure.get('path')
        if not isinstance(path,str) or not core.inside(path,[stage['output_dir']]):
            raise ValueError('failure_record must remain in the stage output directory')
        if any(Path(path).resolve()==Path(stage[k]).resolve() for k in ('log_path','result_path')):
            raise ValueError('failure_record must not overwrite the stage log or result receipt')
        if not Path(path).parent.is_dir() or Path(path).exists():
            raise ValueError('failure_record requires an existing parent and fresh file path')
        checks=pre.get('checks')
        if not isinstance(checks,list):raise ValueError('Preflight checks missing')
        names=[x.get('name') for x in checks if isinstance(x,dict)]
        if len(names)!=len(checks) or len(names)!=len(set(names)) or not CHECKS.issubset(names):
            raise ValueError('Preflight requires seven distinct named checks: '+','.join(sorted(CHECKS)))
        for check in checks:
            if check.get('passed') is not True:raise ValueError('Preflight check did not pass: '+str(check.get('name')))
            pin=check.get('evidence')
            core.checked_pin(pin,project['allowed_roots'])
            # A real, pinned JSON object is required, not an unbound yes/no claim.
            # Project-specific assertions remain the responsibility of its CPU checker.
            with Path(pin['path']).open() as stream:
                evidence=json.load(stream)
            if not isinstance(evidence,dict) or not evidence:
                raise ValueError('Preflight evidence must be a nonempty object')
            if evidence.get('passed') is False or any(evidence.get(k,0) not in (0,None) for k in ('failures','errors')):
                raise ValueError('Preflight evidence reports failures')


def failure_view(job):
    if job.get('status') not in ('FAILED','CANCELLED','STOPPING'):return {}
    stages=job['manifest']['stages'];stage=stages[min(job['stage_index'],len(stages)-1)]
    view={'failure_log_path':stage.get('log_path')}
    spec=stage.get('failure_record')
    if not spec:
        return {**view,'failure_record_status':'LEGACY_NO_STRUCTURED_RECORD',
                'failure_reason':job.get('terminal_reason') or job.get('error') or 'Unknown; consult stage log'}
    path=spec.get('path');view['failure_evidence_path']=path
    try:
        if spec.get('contract')!='ugs-failure-v1' or not core.inside(path,[stage['output_dir']]):
            raise ValueError('Untrusted failure record path/contract')
        record,sha=read_json(path)
        expected={'contract':'ugs-failure-v1','job_id':job['job_id'],'run_id':job['run_id'],
                  'stage_id':stage['stage_id'],'manifest_sha256':job['manifest_sha256'],
                  'automatic_retry':False}
        if any(record.get(k)!=v for k,v in expected.items()):raise ValueError('Failure record identity mismatch')
        proof_path=job.get('guardian_result')
        if not proof_path or not core.inside(proof_path,job['allowed_roots']):
            raise ValueError('Guardian generation proof not yet available')
        proof,_=read_json(proof_path)
        if not record.get('generation') or not record.get('stage_nonce') or any(
                record.get(k)!=proof.get(k) for k in ('generation','stage_nonce')):
            raise ValueError('Failure record generation/nonce mismatch')
        for k in ('phase','category','reason','message'):
            if not isinstance(record.get(k),str) or not record[k] or len(record[k])>8192:
                raise ValueError('Invalid failure field: '+k)
        if record.get('exit_code') is not None and type(record['exit_code']) is not int:
            raise ValueError('Invalid failure exit code')
        return {**view,'failure_record_status':'VERIFIED','failure_evidence_sha256':sha,
                'failure_phase':record['phase'],'failure_category':record['category'],
                'failure_reason':record['reason'],'failure_message':record['message'],
                'failure_detail':record}
    except (OSError,ValueError,TypeError,KeyError) as exc:
        return {**view,'failure_record_status':'UNVERIFIED','failure_record_error':str(exc),
                'failure_reason':job.get('terminal_reason') or job.get('error') or 'Unknown; consult stage log'}


def enrich_output(data,args):
    if len(args)<2:return data
    obj,action=args[:2]
    if obj=='experiment' and action=='show':
        return {**data,**failure_view(data)}
    if (obj=='experiment' and action=='list') or (obj=='scheduler' and action in ('status','get','start') and 'jobs' in data):
        with core.Store().tx() as state:
            by_id={j['job_id']:j for j in state['jobs']}
        rows=data if isinstance(data,list) else data['jobs']
        for row in rows:
            if row['job_id'] in by_id:row.update(failure_view(by_id[row['job_id']]))
    return data


def install():
    if getattr(core,'_ugs_preflight_v1',False):return
    original=core.job_validate
    def validate(manifest,project,from_index=0):
        result=original(manifest,project,from_index)
        validate_preflight(manifest,project,from_index)
        return result
    core.job_validate=validate
    core._ugs_preflight_v1=True
