#!/usr/bin/env python3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/runtime/931207b36c12')
import ugs_idle_v2_entry as previous
import output_reuse
import reuse_dispatch
core,cli=previous.core,previous.cli
output_reuse.install(core,cli)
core.SELF=Path(__file__).resolve()
core.serve=lambda:reuse_dispatch.serve(core)
if __name__=='__main__':
    if sys.argv[1:2]==['internal']:raise SystemExit(cli.main())
    with core.hash_scope():raise SystemExit(cli.main())
