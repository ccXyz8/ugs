"""Memory allowance candidate verifier. Production activation requires release migration."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

SOURCE = Path(__file__).resolve().parent / 'source'
EXPECTED_CORE = 'fdd7052c6559afc4413428e2c01c3240752507d5ea12680fb52c32868baa4698'
if hashlib.sha256((SOURCE/'ugs_exec_v3_1.py').read_bytes()).hexdigest() != EXPECTED_CORE:
    raise RuntimeError('Frozen core SHA mismatch')
sys.path.insert(0, str(SOURCE))
import ugs_exec_v3_1 as core
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
