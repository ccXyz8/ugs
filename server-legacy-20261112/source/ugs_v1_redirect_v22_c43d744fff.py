"""Compatibility entry for the single accepted executor."""
from ugs_exec_v2_2 import *
if __name__=="__main__":
    try: cli()
    except Exception as e:
        print(json.dumps({"error":type(e).__name__,"message":str(e)}),file=sys.stderr)
        sys.exit(2)
