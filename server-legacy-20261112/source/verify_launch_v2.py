"""Central launch verifier. Invoke as a direct child of the registered adapter."""
import json,sys
from ugs_exec_v2 import verify_launch
if __name__=="__main__":
    try:code,result=verify_launch()
    except Exception as e:code,result=2,{"allowed":False,"reason":"VERIFICATION_ERROR","error":type(e).__name__}
    print(json.dumps(result,sort_keys=True))
    sys.exit(code)
