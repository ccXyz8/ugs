#!/usr/bin/env python3
"""Production entry combining accepted repair and shared memory policy."""
import sys
from pathlib import Path
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/repair_release/3061d40c3131')
sys.path.append('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source')
import ugs_cli_repair_v1 as repair_entry
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
import memory_runtime
memory_runtime.install()
import memory_preflight
memory_preflight.install(repair_entry.cli.preflight)
core=repair_entry.cli.core
core.SELF=Path(__file__).resolve()
repair_entry.cli.help_zh.NOTES.insert(0,'已审定4MiB基线允许4–7MiB；收口无进程>7MiB则全局锁定回精确4MiB并重建空闲窗口。新提交绑定memory verifier；进程/健康/五分钟及两秒复查不变。')
if __name__=='__main__':raise SystemExit(repair_entry.cli.main())
