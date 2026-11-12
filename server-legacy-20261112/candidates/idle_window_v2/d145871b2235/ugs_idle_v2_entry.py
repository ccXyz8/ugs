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
                       failure_message='旧verifier与60秒/15秒策略不兼容；先完成CPU验收，再公开update未开始阶段。')
            if 'blocking_controls' in row: row['blocking_controls'].append('VERIFIER_MIGRATION_REQUIRED')
    return data
cli.preflight.enrich_output = enrich
cli.help_zh.NOTES.insert(0, '60秒准入：每15秒采样、至少5点、间隔超过20秒失效；启动两次复查相隔2秒。以scheduler get的实际policy为准；旧verifier未迁移阶段明确阻塞，活动阶段不热改。')
if __name__ == '__main__': raise SystemExit(cli.main())
