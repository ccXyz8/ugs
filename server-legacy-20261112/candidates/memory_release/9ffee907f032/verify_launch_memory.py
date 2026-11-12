"""Opt-in candidate verifier. Never replaces the frozen production entry."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source')
EXPECTED_CORE = 'bfca69d1afc35a40b0ef374ecb57dfc7459fb860e962a445378d6ac2cd361f45'
if hashlib.sha256((SOURCE/'ugs_exec_v3_1.py').read_bytes()).hexdigest() != EXPECTED_CORE:
    raise RuntimeError('Frozen core SHA mismatch')
sys.path.insert(0, str(SOURCE))
import ugs_exec_v3_1 as core
HERE=Path(__file__).resolve().parent
if hashlib.sha256((HERE/'memory_runtime.py').read_bytes()).hexdigest() != '7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78':
    raise RuntimeError('Memory policy SHA mismatch')
import memory_runtime
memory_runtime.install()
from preload import verify

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preload', action='store_true')
    args = parser.parse_args()
    try:
        code, result = verify(core) if args.preload else core.verify_launch(False)
    except Exception as exc:
        code, result = 2, {'allowed': False, 'error': type(exc).__name__, 'message': str(exc)}
    print(json.dumps(result, sort_keys=True))
    sys.exit(code)
