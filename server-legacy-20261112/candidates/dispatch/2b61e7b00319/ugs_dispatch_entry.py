#!/usr/bin/env python3
"""Accepted overlays plus fresh-snapshot dispatch; no frozen files changed."""
import sys
from pathlib import Path
sys.path.insert(0, '/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/resource_override/a31d2dcc3d5b')
import ugs_resource_entry as previous
sys.path.insert(0, str(Path(__file__).resolve().parent))
import dispatch_runtime
core = previous.core
cli = previous.previous.previous.repair_entry.cli
dispatch_runtime.install(core, cli)
core.SELF = Path(__file__).resolve()
cli.help_zh.NOTES.insert(0, '派发校验先于最终GPU复查；2秒快照有效期保持。启动前阻塞原因显示在experiment show/list，重复同因不重复写入完整状态。')
if __name__ == '__main__':
    raise SystemExit(cli.main())
