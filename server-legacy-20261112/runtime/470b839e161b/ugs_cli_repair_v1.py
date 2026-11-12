#!/usr/bin/env python3
"""Candidate entry point: must be used for both CLI and coordinator after acceptance."""
from pathlib import Path
import sys

import ugs_cli_control_v2 as cli
import ugs_repair_v1 as repair
repair.install(cli.core)
cli.core.SELF = Path(__file__).resolve()
rows = cli.help_zh.DATA['catalog']['experiment']
for row in rows:
    if row[0] == 'update':
        row[1] = 'J --file FILE [--expected-sha SHA] [--project P]'
        row[2] = '自动修正从未启动实验或尚未开始的后续阶段；保留job-id和队位，不修改运行/完成阶段。'
rows.extend([
 ['replace', 'J --file FILE [--expected-sha SHA] [--project P]', '管理操作：预检后原子暂停全局派发并终止目标，完整收口后新job继承项目内队位。', ['--file','--project','--expected-sha'], '其他运行阶段继续；保持暂停，完成后显式scheduler resume。新产物目录必需；相同请求幂等。'],
 ['replacement', 'J [--cancel] [--project P]', '查询替换进度；--cancel撤销未完成替换，不复活旧job、不解除全局暂停。', ['--project'], 'BLOCKED时核查原因，可取消请求后重新replace。完成后管理新job-id。']])
cli.help_zh.TITLES['queue']='实验队列'
cli.help_zh.DATA['catalog']['queue']=[
 ['list','[--project P]','按实际项目内派发顺序显示非终态实验及控制阻塞原因；不替代GPU审核。',['--project'],'跨项目按轮转，不提供虚假的全局启动序号。'],
 ['move','J (--before OTHER | --after OTHER) --reason TEXT [--project P] [--expected-revision N] [--dry-run]',
  '显式调整本项目非活动实验队位；不抢占运行实验，不改变其他项目轮转。',
  ['--before','--after','--reason','--project','--expected-revision','--dry-run'],'默认FIFO不变，只有用户明确要求时才调整。替换进行中拒绝移动。']]
cli.help_zh.DATA['params'].update({
 '--dry-run':'update、replace、queue move仅预览，不写状态、不停止进程、不预留资源；正式执行重新校验。',
 '--cancel':'仅experiment replacement：撤销未完成请求，不复活旧job、不解除暂停。',
 '--before':'同项目的非活动job-id，移动到其前面。', '--after':'同项目的非活动job-id，移动到其后面。',
 '--expected-revision':'queue list返回的状态revision；变化时拒绝移动。',
 '--reason':'队位调整原因，1..1024字符，写入审计。'})
for row in rows:
    if row[0] in ('update','replace'):
        row[1]+=' [--dry-run]';row[3]=list(row[3])+['--dry-run']
    if row[0]=='replacement':row[3]=list(row[3])+['--cancel']
original_render=cli.render

def render(data,args):
    if args[:2] in (['queue','list'],['queue','move']):
        cli.table(['JOB','PROJECT','STATUS','POSITION','ACTIVE','BLOCKERS'],
                  [[x['job_id'],x['project_id'],x['status'],x['project_position'],x['active'],x['blocking_controls']] for x in data['queue']])
        print('revision='+str(data['revision'])+'; '+data['note'])
        if data.get('dry_run'):print('仅预览，未修改队列')
    else:original_render(data,args)
cli.render=render
if __name__ == '__main__':
    raise SystemExit(cli.main())
