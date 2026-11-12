#!/usr/bin/env python3
"""Public Chinese help for current-state admission; coordinator/PINs unchanged."""
import sys
sys.path.insert(0, '/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/idle_window_v2/37b9634c7b9d')
import ugs_idle_v2_entry as entry
h = entry.cli.help_zh
for row in h.DATA['catalog']['resource']:
    if row[0] == 'status':
        row[4] = 'QUALIFIED表示最新一次完整有效观察合格，不要求累计空闲时长或采样点；仍受容量、预约及最终复查限制。未知与异常不准入。'
    elif row[0] == 'release':
        row[2] = '解除指定GPU的明确预约；取得新的有效观察后即可参与分配，无连续空闲等待。'
notes = []
for line in h.NOTES:
    if line.startswith('资源档gpu_uuids'):
        line = '资源档gpu_uuids限制允许分配的GPU UUID；整组数量及当前资源满足即可派发，不占部分卡、不绕过最终复查。'
    elif line.startswith('已审定4MiB基线'):
        line = '已审定4MiB基线允许4–7MiB；收口无进程>7MiB则锁定回精确4MiB并使旧观察失效。进程、健康、initial/preload和两秒双复查保持。'
    elif line.startswith('两个接入项目的新提交'):
        line = '项目提交须通过真实CPU预检并绑定已验收即时准入verifier；旧PIN不覆盖，未开始阶段须用公开update迁移，否则明确阻塞。'
    notes.append(line)
h.NOTES = notes
if __name__ == '__main__':
    raise SystemExit(entry.cli.main())
