"""Explicit STReasoner experiment-root reuse; dispatch-transaction write exclusion."""
import re
from pathlib import Path
REVISION='ugs-output-reuse-v1'
PROJECT_ROOT='/public/home/kzhang19/kezhang/cc/STReasoner-more'

def policy(manifest, project):
    spec=manifest.get('artifact_policy')
    if spec is None:return None
    if not isinstance(spec,dict) or set(spec)!={'mode','root'} or spec['mode']!='reuse':
        raise ValueError('artifact_policy requires mode=reuse and root')
    if str(Path(project['project_root']).resolve())!=PROJECT_ROOT:
        raise ValueError('Artifact reuse is only authorized for STReasoner-more')
    root=Path(spec['root'])
    if not root.is_absolute() or str(root.resolve())!=str(root) or root.parent!=Path(PROJECT_ROOT)/'evaluation' or not re.fullmatch(r'exp_[0-9]+',root.name):
        raise ValueError('Reuse root must be canonical project/evaluation/exp_N')
    version=manifest['submission_version']
    if not isinstance(version,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',version):
        raise ValueError('Reuse submission_version must be a safe unique name')
    for stage in manifest['stages']:
        sid=stage['stage_id']
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}',sid):raise ValueError('Unsafe stage_id')
        for key in ('output_dir','evaluation_dir'):
            p=Path(stage[key]).resolve()
            if p!=root and root not in p.parents:raise ValueError(key+' must remain within reuse root')
        # Scientific files and control receipt paths may all remain unchanged.
        failure=stage.get('failure_record')
        controls=[stage['result_path']]+([failure['path']] if failure else [])
        for value in controls:
            path=Path(value)
            if root not in path.resolve().parents or path.is_symlink():raise ValueError('Receipt must be a regular project path within reuse root')
        if len(set(controls))!=len(controls) or stage['log_path'] in controls:raise ValueError('Log/result/failure paths must be distinct within one stage')
    return root

def paths(job):
    m=job['manifest']
    if m.get('artifact_policy'):
        return [Path(m['artifact_policy']['root']).resolve()]
    return [Path(t[k]).resolve() for t in m['stages'] for k in ('output_dir','evaluation_dir')]

def overlap(a,b):return a==b or a in b.parents or b in a.parents

def blocker(c,state,job):
    if not job['manifest'].get('artifact_policy') and not any(j['manifest'].get('artifact_policy') for j in state['jobs'] if j['status'] not in c.TERMINAL or j['job_id'] in state['active_runs']):return None
    own=paths(job)
    for other in state['jobs']:
        if other['job_id']==job['job_id']:continue
        active=other['job_id'] in state['active_runs']
        if other['status'] in c.TERMINAL and not active:continue
        if not (job['manifest'].get('artifact_policy') or other['manifest'].get('artifact_policy')):continue
        # Whole chain claims its root, including gaps between stages. Queue moves
        # cannot overtake an earlier root claim and corrupt its downstream inputs.
        if not active and other['queue_seq']>=job['queue_seq']:continue
        if any(overlap(a,b) for a in own for b in paths(other)):return other['job_id']
    return None

def install(c,cli):
    from reuse_validation import validate_manifest
    old_base=c._job_validate
    c._job_validate=lambda m,p: validate_manifest(c,m,p) if policy(m,p) else old_base(m,p)
    old_validate,old_submit=c.job_validate,c.submit
    old_select,old_launch=c.select_profile,c.launch_valid
    def validate(manifest,project,from_index=0):
        policy(manifest,project)
        return old_validate(manifest,project,from_index)
    def submit(state,manifest,retry_of=None):
        root=policy(manifest,state['projects'][manifest['project_id']])
        if root is None or not retry_of:return old_submit(state,manifest,retry_of)
        prior=c.get_job(state,retry_of)
        if prior['project_id']!=manifest['project_id'] or prior['status'] not in c.TERMINAL or retry_of in state['active_runs']:
            raise ValueError('Reuse retry requires own fully settled terminal job')
        if prior.get('first_started_at') and not prior.get('gpu_release_verified_at'):
            raise ValueError('Prior GPU/process cleanup has not been verified')
        result=old_submit(state,manifest,None)
        job=c.get_job(state,result['job_id'])
        if result.get('duplicate'):
            if job.get('explicit_retry_of')!=retry_of:raise ValueError('Submission identity belongs to another attempt')
        else:
            job['explicit_retry_of']=retry_of
            job['artifact_reuse']={'root':str(root),'revision':REVISION,'overwrites_project_artifacts':True}
        return result
    def select(job,state,idle,age=True):
        if blocker(c,state,job):return None
        return old_select(job,state,idle,age)
    def launch(state,job,sha,gpus):
        policy(job['manifest'],state['projects'][job['project_id']])
        return not blocker(c,state,job) and old_launch(state,job,sha,gpus)
    c.job_validate,c.submit=validate,submit
    c.select_profile,c.launch_valid=select,launch
    prior_failure=cli.preflight.failure_view
    cli.preflight.failure_view=lambda j: j.get('artifact_failure_snapshot') or prior_failure(j)
    c.prepare_artifact_reuse=lambda state,job:prepare(c,cli,state,job)
    original=cli.preflight.enrich_output
    def enrich(data,args):
        data=original(data,args)
        if args[:2] not in (['experiment','show'],['experiment','list'],['queue','list'],['scheduler','status'],['scheduler','get']):return data
        with c.Store().tx() as state:
            jobs={j['job_id']:j for j in state['jobs']}
            rows=[data] if args[:2]==['experiment','show'] else data if isinstance(data,list) else data.get('queue',data.get('jobs',[]))
            for row in rows:
                j=jobs.get(row.get('job_id'))
                if not j or j['status'] in c.TERMINAL or j['job_id'] in state['active_runs']:continue
                owner=blocker(c,state,j)
                if owner:row.update(failure_category='DISPATCH_BLOCKED',failure_reason='OUTPUT_PATH_BUSY: '+owner)
        return data
    cli.preflight.enrich_output=enrich
    cli.help_zh.NOTES.insert(0,'STReasoner-more显式artifact_policy={mode:reuse,root:项目/evaluation/exp_N}允许同实验训练/验证/测试共用目录及终态retry复用产物；项目日志/预测/指标可原路径覆盖，回执路径也可复用，旧回执由UGS归档后清除；job/generation历史保留。重叠路径整链顺序运行，提交不覆盖产物。')


def prepare(c,cli,state,job):
    """Called under final launch transaction, before GPU scans. No scientific files touched."""
    import json,os,stat
    root=policy(job['manifest'],state['projects'][job['project_id']])
    if root is None:return
    if blocker(c,state,job):raise ValueError('OUTPUT_PATH_BUSY')
    stage=c.project_stage(job)
    for source,kind in [(stage['result_path'],'result'),((stage.get('failure_record') or {}).get('path'),'failure')]:
        if not source:continue
        path=Path(source)
        if not path.exists():continue
        st=path.lstat()
        if not stat.S_ISREG(st.st_mode) or st.st_nlink!=1 or st.st_uid!=os.getuid() or st.st_size>65536:
            raise ValueError('Unsafe or oversized old UGS receipt')
        raw=path.read_bytes();record=json.loads(raw)
        prior=c.get_job(state,record.get('job_id'))
        if prior['project_id']!=job['project_id'] or record.get('run_id')!=prior['run_id']:
            raise ValueError('Old receipt belongs to a different run/project')
        prior_stage=next(t for t in prior['manifest']['stages'] if t['stage_id']==record.get('stage_id'))
        expected=prior_stage['result_path'] if kind=='result' else (prior_stage.get('failure_record') or {}).get('path')
        if expected!=source:raise ValueError('Old receipt path identity mismatch')
        completed_prefix=prior['job_id']==job['job_id'] and prior['manifest']['stages'].index(prior_stage)<job['stage_index']
        if not completed_prefix and (prior['status'] not in c.TERMINAL or prior['job_id'] in state['active_runs']):
            raise ValueError('Receipt owner not settled')
        if prior.get('first_started_at') and not completed_prefix and not prior.get('gpu_release_verified_at'):
            raise ValueError('Old receipt owner cleanup not verified')
        sha=c.digest(raw);folder=c.ROOT/'artifact-receipts'/prior['run_id'];folder.mkdir(parents=True,exist_ok=True,mode=0o700)
        archive=folder/(sha+'.json')
        if archive.exists():
            if archive.read_bytes()!=raw:raise ValueError('Receipt archive collision')
        else:
            with archive.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            if archive.read_bytes()!=raw:raise ValueError('Receipt archive readback failed')
        fd=os.open(folder,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        # Preserve the verified historical failure view before the same path changes.
        if kind=='failure':prior['artifact_failure_snapshot']=cli.preflight.failure_view(prior)
        history={'path':source,'archive_path':str(archive),'sha256':sha,'stage_id':record['stage_id'],'kind':kind,'reused_by':job['job_id']}
        if history not in prior.setdefault('artifact_receipt_history',[]):prior['artifact_receipt_history'].append(history)
        # Independently durable audit survives a later aborted launch transaction.
        c.append_json(c.ROOT/'service-events.jsonl',{'event':'receipt_archived_for_reuse','at':c.stamp(),**history})
        if path.read_bytes()!=raw:raise ValueError('Receipt changed during preparation')
        path.unlink()
