"""Index allocation with isolated legacy mapping; no mutable scientific fields."""
import copy
from pathlib import Path
import native_scan
import hash_scope

def install(c, cli=None):
    if getattr(c,'_native_index_v1',False):return
    hash_scope.install(c)
    scanner=native_scan.Scanner()
    c.scan_devices=scanner
    old_observe=c.observe
    def observe(policy,daemon,source):
        if policy.get('gpu_identity')!='index':raise ValueError('Native index policy not active')
        with c.hash_scope():return old_observe(policy,daemon,source)
    c.observe=observe
    old_idle_state=c.idle_state
    def idle_state(*args):
        with c.hash_scope():return old_idle_state(*args)
    c.idle_state=idle_state
    original_reason=c.card_reason
    def reason(card,baseline,valid):
        if baseline and baseline.get('pci_bus_id') != card.get('pci_bus_id'):return 'DEVICE_MAPPING_CHANGED'
        return original_reason(card,baseline,valid)
    c.card_reason=reason
    old_validate=c.profile_validate
    def validate(p):
        if 'gpu_uuids' in p:raise ValueError('gpu_uuids removed; use gpu_indices')
        old_validate(p)
        values=p.get('gpu_indices')
        if values is not None and (not isinstance(values,list) or not values or any(type(x) is not int or not 0<=x<8 for x in values) or len(set(values))!=len(values) or len(values)<p['gpu_count']):raise ValueError('gpu_indices requires unique integer indices 0..7 covering gpu_count')
        if values is not None and p.get('gpu_uuids') is not None:raise ValueError('Use gpu_indices or legacy gpu_uuids, not both')
    c.profile_validate=validate
    def allowed(profile,state):
        validate(profile)
        if 'gpu_indices' in profile:return {str(x) for x in profile['gpu_indices']}
        return set(state['policy']['gpu_inventory'])
    def select(job,state,idle,age=True):
        with c.hash_scope():
            if not c.stage_idle_policy(job,state['policy'])['compatible']:return None
            if job.get('paused') or state['projects'].get(job['project_id'],{}).get('paused') or not c.accepted_stage(job):return None
            used={g for jid,a in state['active_runs'].items() if jid!=job['job_id'] for g in a.get('gpus',[])}
            pool=[g for g in idle.candidates(c.time.monotonic(),0) if g not in used and g not in state.get('gpu_reservations',{})]
            for p in c.project_stage(job)['resource_profiles']:
                available=[g for g in pool if g in allowed(p,state)]
                if len(available)>=p['gpu_count'] and c.budgets_ready(job,state,p,exclude=job['job_id']):return p,available[:p['gpu_count']]
        return None
    c.select_profile=select
    old_launch=c.launch_valid
    def launch(state,job,sha,gpus):
        return any(set(gpus)<=allowed(p,state) and len(gpus)==p['gpu_count'] for p in c.project_stage(job)['resource_profiles']) and old_launch(state,job,sha,gpus)
    c.launch_valid=launch
    def targets(target,policy):
        if not target:raise ValueError('Provide GPU indices 0..7')
        out=[]
        for part in target.split(','):
            part=part.strip()
            if part not in policy['gpu_inventory'] or part in out:raise ValueError('Invalid/duplicate GPU index')
            out.append(part)
        return out
    c.resolve_gpu_targets=targets
    if cli:
        cli.gpu_indices=lambda:{str(i):str(i) for i in range(8)}
        cli.help_zh.NOTES.insert(0,'轻量原生采集：每30秒检查，GPU分配使用index 0–7；新资源表用gpu_indices。不再接受UUID资源限制；未知字段拒绝准入。')
    c._native_index_v1=True
