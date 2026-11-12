#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0, '/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/dispatch/4e57ccf76fc1')
import ugs_dispatch_entry as previous
sys.path.insert(0, str(Path(__file__).resolve().parent))
import idle_runtime_v2
import idle_preflight_v2
import dispatch_cadence
core, cli = previous.core, previous.cli
idle_runtime_v2.install(core)
idle_preflight_v2.install(cli.preflight)
core.SELF = Path(__file__).resolve()
core.serve = lambda: dispatch_cadence.serve(core)
original_enrich = cli.preflight.enrich_output
def enrich(data, args):
    data = original_enrich(data, args)
    if args[:2] not in (['experiment', 'show'], ['experiment', 'list'], ['scheduler', 'status'], ['scheduler', 'get'], ['queue', 'list']): return data
    with core.Store().tx() as state:
        jobs = {j['job_id']: j for j in state['jobs']}
        policy = state['policy']
    if args[:2] == ['experiment', 'show']: rows = [data]
    elif args[:2] == ['experiment', 'list']: rows = data
    elif args[:2] in (['scheduler', 'status'], ['scheduler', 'get']): rows = data.get('jobs', [])
    elif args[:2] == ['queue', 'list']: rows = data.get('queue', [])
    else: return data
    for row in rows:
        j = jobs.get(row.get('job_id'))
        if not j or j['status'] in core.TERMINAL: continue
        row['gpu_admission_policy'] = p = core.stage_idle_policy(j, policy)
        p['active_stage_preserved'] = j['job_id'] in state['active_runs']
        if not p['compatible'] and j['job_id'] not in state['active_runs']:
            row.update(failure_category='DISPATCH_BLOCKED', failure_reason='VERIFIER_MIGRATION_REQUIRED: frozen verifier needs CPU-accepted update',
                       failure_message='旧verifier与即时准入/30秒监测策略不兼容；先完成CPU验收，再公开update未开始阶段。')
            if 'blocking_controls' in row: row['blocking_controls'].append('VERIFIER_MIGRATION_REQUIRED')
    return data
cli.preflight.enrich_output = enrich
def help_text(value):
    if isinstance(value, str):
        return value.replace('两分钟', '当前状态').replace('2分钟', '当前状态').replace('120秒', '0秒（不累计等待）')
    if isinstance(value, list): return [help_text(x) for x in value]
    if isinstance(value, dict): return {k: help_text(v) for k, v in value.items()}
    return value
cli.help_zh.DATA = help_text(cli.help_zh.DATA)
cli.help_zh.NOTES = [x for x in cli.help_zh.NOTES if not x.startswith('GPU共同连续空闲120秒')]
cli.help_zh.NOTES.insert(0, '即时准入：每30秒监测，取消连续空闲等待和累计采样点要求；最新有效快照超过35秒失效。整组资源满足后派发，启动两次复查相隔2秒保持；旧verifier未迁移阶段明确阻塞，活动阶段不热改。以scheduler get的实际policy为准。')
if __name__ == '__main__': raise SystemExit(cli.main())
