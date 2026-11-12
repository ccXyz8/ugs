"""Pinned current-state and legacy-window verifier; frozen previous releases remain unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source')
MEMORY = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
HERE = Path(__file__).resolve().parent
PINS = {
    SOURCE/'ugs_exec_v3_1.py': 'bfca69d1afc35a40b0ef374ecb57dfc7459fb860e962a445378d6ac2cd361f45',
    MEMORY/'memory_runtime.py': '7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78',
    MEMORY/'preload.py': '6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f',
    HERE/'idle_runtime_v2.py': '3846068a23ae5779079192d3bd6771d75550bc1343d1e0f8d90a62860c6bcd90',
    HERE/'native_scan.py': 'b577770686e470f844fa79bd8bba5173db3c28bd04eabe7c30810776614de563',
    HERE/'hash_scope.py': '0b248b8909a76cdb77cfe8c2f86a6f5ef1d271ff92705dd40a9b1a66012b0022',
    HERE/'index_runtime.py': 'fcde10530e121327e91c628d96b880de9ddba9a814f8217649bf1d528e6fb79a',
}
for path, sha in PINS.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise RuntimeError('Verifier dependency SHA mismatch: '+str(path))
sys.path.insert(0,str(SOURCE))
sys.path.insert(0,str(MEMORY))
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
