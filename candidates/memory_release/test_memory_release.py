import ast
import contextlib
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
import sys
sys.path.insert(0,"/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source")
import memory_runtime as runtime
from memory_runtime import memory_matches, fallback_candidates, latch_fallback

ROOT=Path(__file__).resolve().parents[1]
def function(path,name,env):
    node=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.FunctionDef) and n.name==name)
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(path),'exec'),env)
    return env[name]

class MemoryPolicy(unittest.TestCase):
    def setUp(self):
        self.b={'memory_mib':4,'memory_ceiling_mib':7,'driver_version':'driver','memory_reserved_mib':620,'memory_total_mib':40960}
        self.c={'uuid':'gpu','name':'A100','memory_mib':4,'driver_version':'driver','memory_reserved_mib':620,'memory_total_mib':40960,'health_ok':True,'ecc_uncorrectable':0,'processes':[],'util':0,'memory_util':0}
        self.reason=function(Path(runtime.__file__),'card_reason',{'memory_matches':memory_matches})
    def test_integer_range_and_legacy(self):
        for n in [4,5,6,7]:
            with self.subTest(n=n):self.assertIsNone(self.reason(dict(self.c,memory_mib=n),self.b,True))
        for n in [None,True,'7',7.0,-1,0,3,8,100]:
            with self.subTest(n=n):self.assertEqual(self.reason(dict(self.c,memory_mib=n),self.b,True),'MEMORY_NOT_BASELINE')
        b=dict(self.b);b.pop('memory_ceiling_mib')
        self.assertFalse(memory_matches(dict(self.c,memory_mib=7),b))
    def test_other_guards_remain(self):
        for changes in [{'processes':[{'pid':9}]},{'processes':None},{'health_ok':False},{'ecc_uncorrectable':1},{'util':1},{'memory_util':1},{'driver_version':'changed'},{'memory_reserved_mib':621},{'memory_total_mib':40959},{'name':'V100'}]:
            with self.subTest(changes=changes):self.assertIsNotNone(self.reason(dict(self.c,memory_mib=7,**changes),self.b,True))
        self.assertEqual(self.reason(self.c,self.b,False),'BASELINE_UNREVIEWED')
    def test_invalid_allowance_fails_closed(self):
        for n in [None,True,'7',7.0,3,8]:
            self.assertFalse(memory_matches(self.c,dict(self.b,memory_ceiling_mib=n)))
    def test_cleanup_uses_identical_range_and_retains_identity_checks(self):
        with tempfile.TemporaryDirectory() as d:
            proof=Path(d)/'proof.json';proof.write_text(json.dumps({'generation':'g','stage_nonce':'n','all_children_reaped':True,'remaining':[],'exits':[{'pid':42}]}))
            active={'generation':'g','stage_nonce':'n','guardian_result':str(proof),'gpus':['gpu']}
            state={'policy':{'baseline':{'gpu':self.b}},'daemon':{}}
            core=types.SimpleNamespace(Store=lambda:types.SimpleNamespace(tx=lambda *args:contextlib.nullcontext(state)),observe=lambda *a:{'cards':[self.c]},baseline_valid=lambda b:True,memory_matches=memory_matches,stamp=lambda:"test-time")
            check=function(Path(runtime.__file__),'gpu_contexts_gone',{'core':core,'Path':Path,'json':json,'copy':copy,'fallback_candidates':fallback_candidates,'latch_fallback':latch_fallback})
            for n in [4,5,6,7,8]:
                self.c['memory_mib']=n;self.assertEqual(check(active),n<=7)
            self.c.update(memory_mib=4,processes=[{'pid':42}]);self.assertFalse(check(active))
            with self.assertRaises(ValueError):check(dict(active,generation='wrong'))

    def test_global_fallback_latches_and_changes_observation_signature(self):
        import hashlib
        policy={'baseline':{'gpu':dict(self.b),'other':dict(self.b)},'gpu_inventory':['gpu','other']}
        signature=function(Path(runtime.core.__file__),'observation_policy',{
            'digest':lambda b:hashlib.sha256(b).hexdigest(),
            'canonical':lambda x:json.dumps(x,sort_keys=True,separators=(',',':')).encode()})
        before=signature(policy)
        cards=[dict(self.c,memory_mib=8)]
        triggers=fallback_candidates(policy,cards,['gpu'])
        self.assertTrue(latch_fallback(policy,triggers,{'job_id':'test-job','at':'test-time'}))
        self.assertNotEqual(signature(policy),before)
        for b in policy['baseline'].values():
            self.assertTrue(memory_matches(dict(self.c,memory_mib=4),b))
            self.assertFalse(memory_matches(dict(self.c,memory_mib=7),b))
            b['memory_ceiling_mib']=7
            self.assertFalse(memory_matches(dict(self.c,memory_mib=7),b))
        self.assertEqual(policy['memory_allowance_fallback']['triggers'],[{'uuid':'gpu','memory_mib':8}])
        self.assertFalse(fallback_candidates(policy,cards,['gpu']))
    def test_external_process_unknown_and_nonallocated_do_not_trip(self):
        policy={'baseline':{'gpu':self.b}}
        for changes in [{'memory_mib':7},{'memory_mib':None},{'memory_mib':'8'},
                        {'memory_mib':8,'processes':[{'pid':99}]},
                        {'memory_mib':8,'processes':None}]:
            self.assertFalse(fallback_candidates(policy,[dict(self.c,**changes)],['gpu']))
        self.assertFalse(fallback_candidates(policy,[dict(self.c,memory_mib=8)],['other']))

if __name__=='__main__':unittest.main()
