"""Explicit repair transactions. No GPU policy changes; install in CLI and daemon."""
import argparse
import copy
import json
import os
import pwd
import sys
from pathlib import Path

OPEN = {'WAITING_CLEANUP', 'READY', 'BLOCKED'}


def pending(s):
    return [r for r in s.get('replacements', {}).values() if r['status'] in OPEN]


def view(r):
    return {k: v for k, v in r.items() if k != 'manifest'}


def validate_new(c, s, old, m):
    if m['project_id'] != old['project_id']:
        raise ValueError('替换不能跨项目')
    # Never let the new attempt overwrite old artifacts, including nested roots.
    for a in old['manifest']['stages']:
        for b in m['stages']:
            keys = ('output_dir', 'evaluation_dir', 'log_path', 'result_path')
            for old_key in keys:
                for new_key in keys:
                    x, y = Path(a[old_key]).resolve(), Path(b[new_key]).resolve()
                    if x == y or x in y.parents or y in x.parents:
                        raise ValueError('替换必须使用独立产物路径: ' + new_key)
    trial = copy.deepcopy(s)
    c.get_job(trial, old['job_id'])['status'] = 'CANCELLED'
    trial['active_runs'].pop(old['job_id'], None)
    result = c.submit(trial, m, old['job_id'])
    if result.get('duplicate'):
        raise ValueError('替换清单已用于其他提交，请使用独立提交版本')


def begin(c, s, jid, m, project=None, expected=None):
    j = c.scoped(s, jid, project)
    sha = c.digest(c.canonical(m))
    prior = s.get('replacements', {}).get(jid)
    if prior and prior['status'] != 'ABORTED':
        if prior['manifest_sha256'] == sha:
            return view(prior)  # Durable idempotency, including completed requests.
        raise ValueError('已有替换请求；先检查或取消该请求，不能覆盖')
    if expected is not None and expected != j['manifest_sha256']:
        raise ValueError('目标清单已变化')
    if j['status'] == 'SUCCEEDED' or j.get('replaced_by'):
        raise ValueError('已成功或已被替换的实验不能再次替换')
    validate_new(c, s, j, m)
    s['policy']['scheduler_paused'] = True
    r = {'target_job_id': jid, 'manifest': copy.deepcopy(m), 'manifest_sha256': sha,
         'target_sha256': j['manifest_sha256'], 'status': 'WAITING_CLEANUP',
         'requested_at': c.stamp(), 'queue_position': j.get('queue_position', j['queue_seq'])}
    s.setdefault('replacements', {})[jid] = r
    if j['status'] not in c.TERMINAL:
        c.request_stop(s, j, 'explicit experiment replace',
                       'CANCELLED' if c.started(j, s) else 'WITHDRAWN')
    advance(c, s, jid)
    return view(r)


def advance(c, s, jid):
    r = s['replacements'][jid]
    if r['status'] not in OPEN or r['status'] == 'BLOCKED':
        return
    if jid in s['active_runs'] or s.get('recovery'):
        return
    j = c.get_job(s, jid)
    if j['status'] not in c.TERMINAL:
        return
    try:
        if j['manifest_sha256'] != r['target_sha256']:
            raise ValueError('目标清单已变化')
        validate_new(c, s, j, r['manifest'])
        result = c.submit(s, r['manifest'], jid)
        new = c.get_job(s, result['job_id'])
        new['replaces'] = jid
        new['queue_position'] = r['queue_position']
        j['replaced_by'] = new['job_id']
        r.update(status='COMPLETED', replacement_job_id=new['job_id'], completed_at=c.stamp())
    except (ValueError, OSError, KeyError) as exc:
        r.update(status='BLOCKED', error=str(exc)[:4096])
    s['policy']['scheduler_paused'] = True


def abort(c, s, jid, project=None):
    c.scoped(s, jid, project)
    r = s.get('replacements', {}).get(jid)
    if not r:
        raise ValueError('没有替换请求')
    if r['status'] == 'COMPLETED':
        raise ValueError('替换已完成；请按新job-id使用remove/stop')
    r.update(status='ABORTED', aborted_at=c.stamp())
    s['policy']['scheduler_paused'] = True
    return view(r)  # Does NOT revoke cancellation or resurrect the old experiment.


def update(c, s, jid, m, project=None, expected=None):
    j = c.scoped(s, jid, project)
    if any(r['target_job_id'] == jid for r in pending(s)):
        raise ValueError('目标正在替换，不能同时update')
    active = s['active_runs'].get(jid)
    start = j['stage_index'] + bool(active and not active.get('continuation'))
    if not c.started(j, s):
        start = None
    return c.update_manifest(s, j, m, expected or j['manifest_sha256'], start)



def queue_rows(c, s, project=None):
    if project is not None and project not in s['projects']:
        raise ValueError('项目未注册')
    positions = {}
    rows = []
    for j in sorted(s['jobs'], key=lambda j: (j.get('queue_position', j['queue_seq']), j['queue_seq'])):
        if j['status'] in c.TERMINAL or (project and j['project_id'] != project):
            continue
        active = j['job_id'] in s['active_runs']
        p = j['project_id']
        if not active: positions[p] = positions.get(p, 0) + 1
        reasons = []
        if s['policy'].get('scheduler_paused'): reasons.append('SCHEDULER_PAUSED')
        if s['projects'][p].get('paused'): reasons.append('PROJECT_PAUSED')
        if s.get('recovery'): reasons.append('RECOVERY_BLOCKED')
        if pending(s): reasons.append('REPLACEMENT_PENDING')
        if not active and positions[p] > 1: reasons.append('PROJECT_FIFO')
        if not active and len(s['active_runs']) >= s['policy']['max_experiment']: reasons.append('CAPACITY_FULL')
        if j['status'] in ('BLOCKED','WAITING_GATE','STOPPING'): reasons.append(j['status'])
        rows.append({'job_id':j['job_id'], 'project_id':p, 'status':j['status'],
                     'project_position':None if active else positions[p],
                     'queue_position':j.get('queue_position',j['queue_seq']),
                     'active':active, 'blocking_controls':reasons,
                     'resource_check':'NOT_EVALUATED'})
    return {'revision':s['revision'], 'queue':rows,
            'note':'项目内顺序；跨项目按轮转，无全局固定启动序号。此查询不替代GPU/预算/合同审核。'}


def move(c, s, jid, anchor, before, reason, project=None, expected_revision=None):
    j = c.scoped(s, jid, project)
    target = c.scoped(s, anchor, j['project_id'])
    if not isinstance(reason,str) or not reason.strip() or len(reason)>1024:
        raise ValueError('调整队位必须提供1..1024字符的--reason')
    if expected_revision is not None and s['revision'] != expected_revision:
        raise ValueError('队列版本已变化，请重新查询')
    if jid == anchor: raise ValueError('不能以自身为锚点')
    if pending(s): raise ValueError('替换进行中禁止调整队位')
    for job in (j,target):
        if job['job_id'] in s['active_runs'] or job['status'] in c.TERMINAL:
            raise ValueError('只能调整非活动、非终态的排队实验')
    waiting = [x for x in sorted(s['jobs'],key=lambda x:(x.get('queue_position',x['queue_seq']),x['queue_seq']))
               if x['project_id']==j['project_id'] and x['status'] not in c.TERMINAL and x['job_id'] not in s['active_runs']]
    slots = sorted(x.get('queue_position',x['queue_seq']) for x in waiting)
    waiting.remove(j);index=waiting.index(target)+(0 if before else 1);waiting.insert(index,j)
    # All edits commit together. No alteration of registration IDs or project round.
    for job,position in zip(waiting,slots): job['queue_position']=position
    s['last_queue_change']={'job_id':jid,'anchor':anchor,'before':before,'reason':reason.strip(),'at':c.stamp()}
    return queue_rows(c,s,j['project_id'])

def install(c):
    if getattr(c, '_repair_installed', False):
        return
    old_cli, old_finalize, old_heads, old_eligible = c.cli, c.finalize_available, c.pending_heads, c.eligible

    def heads(s):
        ordered = dict(s)
        ordered['jobs'] = sorted(s['jobs'], key=lambda j: (j.get('queue_position', j['queue_seq']), j['queue_seq']))
        return old_heads(ordered)

    def finalize(idle=None):
        with c.Store().tx() as s:
            marked = s.get('daemon', {}).get('repair_revision') == 'ugs-repair-v1'
        if not marked:
            with c.Store().tx('repair_runtime_ready') as s:
                s['daemon']['repair_revision'] = 'ugs-repair-v1'
        old_finalize(idle)  # Only the existing guardian/GPU checks can free active_runs.
        with c.Store().tx() as s:
            work = [r['target_job_id'] for r in pending(s)
                    if r['status'] != 'BLOCKED' and r['target_job_id'] not in s['active_runs'] and not s.get('recovery')]
        for jid in work:
            with c.Store().tx('replacement_progress') as s:
                advance(c, s, jid)

    def cli():
        args = sys.argv[1:]
        repair = len(args) >= 2 and args[0] == 'experiment' and args[1] in ('replace', 'replacement', 'update')
        queue = len(args) >= 2 and args[0] == 'queue' and args[1] in ('list', 'move')
        resume = args[:2] == ['scheduler', 'resume']
        if not repair and not resume and not queue:
            return old_cli()
        c.node_check()
        if pwd.getpwuid(os.getuid()).pw_name != 'kzhang19':
            raise ValueError('wrong account')
        parser = argparse.ArgumentParser()
        parser.add_argument('object'); parser.add_argument('action'); parser.add_argument('target', nargs='?')
        parser.add_argument('--file'); parser.add_argument('--project'); parser.add_argument('--expected-sha')
        parser.add_argument('--cancel', action='store_true')
        parser.add_argument('--dry-run', action='store_true')
        anchors=parser.add_mutually_exclusive_group()
        anchors.add_argument('--before'); anchors.add_argument('--after')
        parser.add_argument('--reason'); parser.add_argument('--expected-revision',type=int)
        a = parser.parse_args(args)
        if not queue and any((a.before,a.after,a.reason,a.expected_revision is not None)):
            raise ValueError('队列参数仅用于queue move')
        if queue:
            if a.file or a.expected_sha or a.cancel: raise ValueError('queue不接受实验清单参数')
            if a.action=='list' and any((a.target,a.before,a.after,a.reason,a.expected_revision is not None,a.dry_run)):
                raise ValueError('queue list只接受--project')
            if a.action=='move' and (not a.target or not (a.before or a.after)):
                raise ValueError('queue move需要JOB和--before或--after')
            event = 'queue_move' if a.action=='move' and not a.dry_run else None
            with c.Store().tx(event) as state:
                c.require_v3(state)
                s=copy.deepcopy(state) if a.dry_run else state
                if a.action=='list': out=queue_rows(c,s,a.project)
                else:
                    if event and (s.get('daemon',{}).get('repair_revision')!='ugs-repair-v1' or not c.live(s['daemon'].get('identity'))):
                        raise ValueError('修复运行时未启用或不存活')
                    out=move(c,s,a.target,a.before or a.after,bool(a.before),a.reason,a.project,a.expected_revision)
                    out['dry_run']=a.dry_run
            print(json.dumps(out,ensure_ascii=False));return
        if resume:
            if len(args) != 2: raise ValueError('resume不接受额外参数')
            with c.Store().tx('scheduler_resume') as s:
                c.require_v3(s)
                if pending(s): raise ValueError('替换尚未完成；查看experiment replacement JOB，或--cancel撤销请求')
                s['policy']['scheduler_paused'] = False
                out = {'scheduler_paused': False}
        else:
            if not a.target: raise ValueError('必须指定job-id')
            if a.action == 'replacement':
                if a.file or a.expected_sha: raise ValueError('replacement只支持查询或--cancel')
            elif not a.file or a.cancel:
                raise ValueError('update/replace需要--file，不接受--cancel')
            m = json.loads(Path(a.file).read_text()) if a.file else None
            if a.dry_run and a.action == 'replacement': raise ValueError('replacement查询/取消不接受--dry-run')
            event = None if a.dry_run or (a.action == 'replacement' and not a.cancel) else 'experiment_' + a.action
            with c.Store().tx(event) as state:
                s = copy.deepcopy(state) if a.dry_run else state
                c.require_v3(s)
                if event and (s.get('daemon', {}).get('repair_revision') != 'ugs-repair-v1' or not c.live(s['daemon'].get('identity'))):
                    raise ValueError('修复运行时未启用或不存活，拒绝登记替换/更新')
                if a.action == 'replace': out = begin(c, s, a.target, m, a.project, a.expected_sha)
                elif a.action == 'update': out = update(c, s, a.target, m, a.project, a.expected_sha)
                elif a.cancel: out = abort(c, s, a.target, a.project)
                else:
                    c.scoped(s, a.target, a.project)
                    out = view(s.get('replacements', {}).get(a.target, {'status': 'NONE'}))
            if a.dry_run: out = {**out, 'dry_run':True, 'note':'仅模拟当前状态，不预留队位/资源，不停止进程；正式执行会重新校验'}
            if a.action == 'replace' and not a.dry_run:
                c.stop_owned(a.target)  # Intent is already durable; daemon also retries cleanup.
        print(json.dumps(out, ensure_ascii=False))

    c.pending_heads = heads
    c.eligible = lambda s: not pending(s) and old_eligible(s)
    c.finalize_available = finalize
    c.cli = cli
    c._repair_installed = True
