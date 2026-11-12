"""Explicitly approved resource changes for inactive unstarted suffixes."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys

REVISION = 'ugs-resource-override-v1'


def validate_gpu_indices(profile):
    ids = profile.get('gpu_indices')
    if ids is None:return
    if (not isinstance(ids,list) or not ids or any(type(g) is not int or not 0 <= g <= 7 for g in ids)
            or len(ids)!=len(set(ids)) or len(ids)<profile['gpu_count']):
        raise ValueError('gpu_indices must be unique integer GPU indexes and cover the requested card count')


def preserve_except_resources(core, old, new, start):
    # Only engineering bindings and explicitly granted resource profiles may change.
    if {k:v for k,v in old.items() if k not in ('stages','admission','submission_version')} != {k:v for k,v in new.items() if k not in ('stages','admission','submission_version')}:
        raise ValueError('Frozen experiment/scientific fields changed')
    if len(old['stages']) != len(new['stages']):raise ValueError('Stage count changed')
    engineering = {'argv','source_sha256','resource_profiles'}
    for i,(a,b) in enumerate(zip(old['stages'],new['stages'])):
        if i<start:
            if a!=b or old['admission']['stages'][a['stage_id']]!=new['admission']['stages'][b['stage_id']]:
                raise ValueError('Completed/started prefix is immutable')
        elif {k:v for k,v in a.items() if k not in engineering}!={k:v for k,v in b.items() if k not in engineering}:
            raise ValueError('Only argv/source pins/resource profiles may change in authorized suffix')
    normalized=copy.deepcopy(new)
    for i in range(start,len(new['stages'])):
        normalized['stages'][i]['resource_profiles']=copy.deepcopy(old['stages'][i]['resource_profiles'])
    core.same_semantics(old,normalized,start)


def apply(core, state, jid, manifest, expected, authorization, project=None):
    job=core.scoped(state,jid,project)
    if not expected or job['manifest_sha256']!=expected:raise ValueError('Expected SHA mismatch')
    if job['status'] in core.TERMINAL:raise ValueError('Terminal experiment immutable')
    active=state['active_runs'].get(jid)
    if active and not active.get('continuation'):raise ValueError('Resource override requires no running/starting stage')
    if job['status'] not in ('QUEUED','WAITING_RESOURCES','WAITING_CONTINUATION','WAITING_GATE','BLOCKED'):
        raise ValueError('Unsafe job state for resource override')
    if job.get('pending_revision'):raise ValueError('Resolve pending revision before resource override')
    if any(x.get('target_job_id')==jid and x.get('status') in ('WAITING_CLEANUP','BLOCKED') for x in state.get('replacements',{}).values()):
        raise ValueError('Replacement pending')
    pin=state.get('resource_change_grants',{}).get(authorization)
    if not pin or pin.get('path')!=authorization or not core.pin_ok(pin):raise ValueError('Resource authorization is not registered or has changed')
    grant=json.loads(Path(authorization).read_text())
    newsha=core.digest(core.canonical(manifest));start=job['stage_index']
    required={'contract':REVISION,'job_id':jid,'run_id':job['run_id'],'project_id':job['project_id'],
              'old_manifest_sha256':expected,'new_manifest_sha256':newsha,'from_stage_index':start}
    if any(grant.get(k)!=v for k,v in required.items()):raise ValueError('Authorization identity/SHA/stage mismatch')
    if not grant.get('reason') or not grant.get('user_instruction'):raise ValueError('Explicit user instruction required')
    core.checked_pin(grant.get('project_authorization'),state['projects'][job['project_id']]['allowed_roots'])
    suffix=manifest['stages'][start:]
    if not suffix or grant.get('stage_ids')!=[t['stage_id'] for t in suffix]:raise ValueError('Authorization suffix mismatch')
    if grant.get('resource_profiles')!={t['stage_id']:t['resource_profiles'] for t in suffix}:
        raise ValueError('Authorized resources do not match manifest')
    for t in suffix:
        for profile in t['resource_profiles']:
            validate_gpu_indices(profile)
            if not set(map(str,profile.get('gpu_indices',[]))).issubset(state['policy']['gpu_inventory']):
                raise ValueError('Requested GPU index outside reviewed inventory')
    preserve_except_resources(core,job['manifest'],manifest,start)
    old_semantics=core.same_semantics
    # Scoped only to this CLI process, inside the state transaction, after exact grant checks.
    def checked(old,new,index):
        if core.digest(core.canonical(old))!=expected or core.digest(core.canonical(new))!=newsha or index!=start:
            raise ValueError('Authorized update changed during validation')
        core.same_semantics=old_semantics
        try:preserve_except_resources(core,old,new,index)
        finally:core.same_semantics=checked
    core.same_semantics=checked
    try:result=core.update_manifest(state,job,manifest,expected,start)
    finally:core.same_semantics=old_semantics
    audit={'at':core.stamp(),'authorization':copy.deepcopy(pin),'from_stage_index':start,
           'old_manifest_sha256':expected,'new_manifest_sha256':newsha,'stage_ids':grant['stage_ids']}
    job.setdefault('resource_override_history',[]).append(audit)
    result.update(resource_authorization=authorization,resource_changed_stages=grant['stage_ids'])
    return result


def install(core):
    if getattr(core,'_resource_override_v1',False):return
    old_cli,old_profile,old_launch=core.cli,core.profile_validate,core.launch_valid
    def profile_validate(profile):
        old_profile(profile);validate_gpu_indices(profile)
    def select_profile(job,state,idle,age=True):
        if job.get('paused') or state['projects'].get(job['project_id'],{}).get('paused') or not core.accepted_stage(job):return None
        used={g for jid,a in state['active_runs'].items() if jid!=job['job_id'] for g in a.get('gpus',[])}
        window=core.stage_idle_seconds(job,state['policy']) if age else 0
        pool=[g for g in idle.candidates(core.time.monotonic(),window) if g not in used and g not in state.get('gpu_reservations',{})]
        for profile in core.project_stage(job)['resource_profiles']:
            validate_gpu_indices(profile)
            allowed=profile.get('gpu_indices')
            candidates=[g for g in pool if allowed is None or int(g) in allowed]
            if len(candidates)>=profile['gpu_count'] and core.budgets_ready(job,state,profile,exclude=job['job_id']):
                return profile,candidates[:profile['gpu_count']]
        return None
    def launch_valid(state,job,sha,gpus):
        if not old_launch(state,job,sha,gpus):return False
        return any(len(gpus)==p['gpu_count'] and (p.get('gpu_indices') is None or set(gpus).issubset(map(str,p['gpu_indices'])))
                   for p in core.project_stage(job)['resource_profiles'])
    def cli():
        args=sys.argv[1:]
        if '--resource-authorization' not in args:return old_cli()
        core.node_check()
        if os.getuid()!=2087:raise ValueError('Wrong account')
        parser=argparse.ArgumentParser()
        parser.add_argument('object',choices=['experiment']);parser.add_argument('action',choices=['update'])
        parser.add_argument('target');parser.add_argument('--file',required=True)
        parser.add_argument('--expected-sha',required=True);parser.add_argument('--resource-authorization',required=True)
        parser.add_argument('--project');parser.add_argument('--dry-run',action='store_true')
        a=parser.parse_args(args)
        manifest=json.loads(Path(a.file).read_text())
        with core.Store().tx(None if a.dry_run else 'experiment_resource_update') as stored:
            if stored.get('executor_release',{}).get('resource_override_revision')!=REVISION or not core.release_ok(stored):
                raise ValueError('Accepted resource override runtime is not active')
            if not core.live(stored.get('daemon',{}).get('identity')):raise ValueError('Coordinator unavailable')
            state=copy.deepcopy(stored) if a.dry_run else stored
            result=apply(core,state,a.target,manifest,a.expected_sha,a.resource_authorization,a.project)
        result['dry_run']=a.dry_run
        print(json.dumps(result,ensure_ascii=False))
    core.profile_validate=profile_validate
    core.select_profile=select_profile
    core.launch_valid=launch_valid
    core.cli=cli
    core._resource_override_v1=True
