"""Pinned current-state and legacy-window verifier; frozen previous releases remain unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
PINS = {
    HERE/'ugs_exec_v3_1.py': '565a6d75cf1c83e15427dda9b8a31ac657c4e874d9d54cdca43a5589473cc394',
    HERE/'memory_runtime.py': '7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78',
    HERE/'preload.py': '6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f',
    HERE/'idle_runtime_v2.py': '54fded7f96ee08ae3844a737b927e2aa51b3d103994413feddfc125e54629ebd',
    HERE/'native_scan.py': 'b577770686e470f844fa79bd8bba5173db3c28bd04eabe7c30810776614de563',
    HERE/'hash_scope.py': 'fb4bf20effc5b70b5d3b6aca9a51fda3e7903237a1beb161ab4ea3559d57980c',
    HERE/'index_runtime.py': 'fcde10530e121327e91c628d96b880de9ddba9a814f8217649bf1d528e6fb79a',
}
for path, sha in PINS.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise RuntimeError('Verifier dependency SHA mismatch: '+str(path))
sys.path.insert(0,str(HERE))
import ugs_exec_v3_1 as core
import memory_runtime
memory_runtime.install()
import idle_runtime_v2
idle_runtime_v2.install(core)
import index_runtime
index_runtime.install(core)
from preload import verify as original_verify
def verify(core):
    with core.hash_scope(): return original_verify(core)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preload', action='store_true')
    args = parser.parse_args()
    try:
        code, result = verify(core) if args.preload else core.verify_launch(False)
    except Exception as exc:
        code, result = 2, {'allowed':False,'error':type(exc).__name__,'message':str(exc)}
    if not result.get('allowed') and ('telemetry' in result.get('message','').lower() or 'GPU_TELEMETRY_UNAVAILABLE' in result.get('message','')):
        result.update(failure_category='GPU_TELEMETRY_UNAVAILABLE',worker_started=False,automatic_retry=False)
    print(json.dumps(result,sort_keys=True))
    sys.exit(code)
