"""Compatibility entry: all queue mutations use the accepted executable CLI."""
from ugs_exec_v2 import *
if __name__=="__main__":
    try: cli()
    except Exception as e:
        print(json.dumps({"error":type(e).__name__,"message":str(e)}),file=sys.stderr)
        sys.exit(2)
