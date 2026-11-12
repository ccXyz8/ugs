import argparse,json,sys
from ugs_exec_v3 import verify_launch
p=argparse.ArgumentParser()
p.add_argument('--preload',action='store_true')
a=p.parse_args()
try:
 code,result=verify_launch(preload=a.preload)
 print(json.dumps(result,sort_keys=True))
 sys.exit(code)
except Exception as e:
 print(json.dumps({'allowed':False,'error':type(e).__name__,'message':str(e)}))
 sys.exit(2)
