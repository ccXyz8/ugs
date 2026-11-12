"""Pinned 120/300-second verifier; frozen previous releases remain unchanged."""
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
    HERE/'idle_runtime.py': '9c1a23f38eeab2536f1612106903efbfcaebabdf0b77921f91594e9cc81f2f04',
}
for path, sha in PINS.items():
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise RuntimeError('Verifier dependency SHA mismatch: '+str(path))
sys.path.insert(0,str(SOURCE))
sys.path.insert(0,str(MEMORY))
import ugs_exec_v3_1 as core
import memory_runtime
memory_runtime.install()
import idle_runtime
idle_runtime.install(core)
from preload import verify

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preload', action='store_true')
    args = parser.parse_args()
    try:
        code, result = verify(core) if args.preload else core.verify_launch(False)
    except Exception as exc:
        code, result = 2, {'allowed':False,'error':type(exc).__name__,'message':str(exc)}
    print(json.dumps(result,sort_keys=True))
    sys.exit(code)
