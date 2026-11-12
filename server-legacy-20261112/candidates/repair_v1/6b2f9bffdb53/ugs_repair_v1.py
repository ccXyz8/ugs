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
        resume = args[:2] == ['scheduler', 'resume']
        if not repair and not resume:
            return old_cli()
        c.node_check()
        if pwd.getpwuid(os.getuid()).pw_name != 'kzhang19':
            raise ValueError('wrong account')
        parser = argparse.ArgumentParser()
        parser.add_argument('object'); parser.add_argument('action'); parser.add_argument('target', nargs='?')
        parser.add_argument('--file'); parser.add_argument('--project'); parser.add_argument('--expected-sha')
        parser.add_argument('--cancel', action='store_true')
        a = parser.parse_args(args)
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
            event = None if a.action == 'replacement' and not a.cancel else 'experiment_' + a.action
            with c.Store().tx(event) as s:
                c.require_v3(s)
                if event and (s.get('daemon', {}).get('repair_revision') != 'ugs-repair-v1' or not c.live(s['daemon'].get('identity'))):
                    raise ValueError('修复运行时未启用或不存活，拒绝登记替换/更新')
                if a.action == 'replace': out = begin(c, s, a.target, m, a.project, a.expected_sha)
                elif a.action == 'update': out = update(c, s, a.target, m, a.project, a.expected_sha)
                elif a.cancel: out = abort(c, s, a.target, a.project)
                else:
                    c.scoped(s, a.target, a.project)
                    out = view(s.get('replacements', {}).get(a.target, {'status': 'NONE'}))
            if a.action == 'replace':
                c.stop_owned(a.target)  # Intent is already durable; daemon also retries cleanup.
        print(json.dumps(out, ensure_ascii=False))

    c.pending_heads = heads
    c.eligible = lambda s: not pending(s) and old_eligible(s)
    c.finalize_available = finalize
    c.cli = cli
    c._repair_installed = True
