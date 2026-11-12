#!/usr/bin/env python3
"""Public entry: repair + memory policy + shared 120-second idle window."""
import sys
from pathlib import Path
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_entry/b40388bc2608')
import ugs_memory_entry as previous
sys.path.insert(0,str(Path(__file__).resolve().parent))
import idle_runtime
import idle_preflight
core = previous.core
idle_runtime.install(core)
idle_preflight.install(previous.repair_entry.cli.preflight)
core.SELF = Path(__file__).resolve()
help_zh = previous.repair_entry.cli.help_zh
# Translate only live help text; no frozen source files or historical evidence change.
def update_text(value):
    if isinstance(value,str):
        return value.replace('五分钟','两分钟').replace('5分钟','2分钟').replace('300秒','120秒').replace('11点','5点')
    if isinstance(value,list):return [update_text(x) for x in value]
    if isinstance(value,dict):return {k:update_text(v) for k,v in value.items()}
    return value
help_zh.DATA = update_text(help_zh.DATA)
help_zh.NOTES = update_text(help_zh.NOTES)
help_zh.NOTES.insert(0,'GPU共同连续空闲120秒且至少5点；每30秒观察，间隔超过35秒失效，启动两次复查相隔2秒。旧PIN未迁移阶段仍按300秒/11点，避免提前派发后被旧验证器拒绝。')
if __name__ == '__main__':raise SystemExit(previous.repair_entry.cli.main())
