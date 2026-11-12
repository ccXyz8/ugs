#!/usr/bin/env python3
"""Public UGS entry with approved suffix resource updates and UUID constraints."""
import sys
from pathlib import Path
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/idle_window/ad910adbc734')
import ugs_idle_entry as previous
sys.path.insert(0,str(Path(__file__).resolve().parent))
import resource_override
core=previous.core
resource_override.install(core)
core.SELF=Path(__file__).resolve()
help_zh=previous.help_zh
help_zh.DATA['params']['--resource-authorization']='experiment update专用：引用中央已登记、绑定job/run/新旧SHA/未开始阶段的用户资源变更授权；必须同时--expected-sha。不停止运行阶段、不改变全局开关或队位。'
for row in help_zh.DATA['catalog']['experiment']:
    if row[0]=='update':
        row[1]+=' [--resource-authorization FILE]'
        row[3].append('--resource-authorization')
        row[2]+=' 明确授权的资源变更仅在无运行阶段时应用；普通update仍冻结资源语义。'
help_zh.NOTES.insert(0,'资源档gpu_uuids可限制允许的GPU UUID集合；目标卡不足或不满足120秒完整空闲时等待，不占卡、不绕过复查。')
if __name__=='__main__':raise SystemExit(previous.previous.repair_entry.cli.main())
