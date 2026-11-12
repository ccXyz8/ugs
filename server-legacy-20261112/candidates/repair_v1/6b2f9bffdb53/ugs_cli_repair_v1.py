#!/usr/bin/env python3
"""Candidate entry point: must be used for both CLI and coordinator after acceptance."""
from pathlib import Path
import sys
sys.path.append("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source")
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
if __name__ == '__main__':
    raise SystemExit(cli.main())
