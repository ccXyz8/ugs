"""Direct child of registered adapter; --preload rechecks immediately before CUDA."""
import argparse,json,sys
from ugs_exec_v2_1 import verify_launch
if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--preload",action="store_true");args=p.parse_args()
    try:code,result=verify_launch(preload=args.preload)
    except Exception as e:code,result=2,{"allowed":False,"reason":"VERIFICATION_ERROR","error":type(e).__name__}
    print(json.dumps(result,sort_keys=True))
    sys.exit(code)
