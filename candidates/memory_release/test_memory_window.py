"""Synthetic eight-card histories through the real installed idle-window predicate."""
import copy,sys,unittest
from unittest.mock import patch
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/source')
sys.path.insert(0,'/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
import memory_runtime as m
c=m.core;m.install()

class Window(unittest.TestCase):
    def setUp(self):
        self.b={'memory_mib':4,'memory_ceiling_mib':7,'driver_version':'fixture','memory_reserved_mib':620,'memory_total_mib':40960}
        self.ids=['fixture-'+str(i) for i in range(8)]
        self.policy={'baseline':{g:dict(self.b) for g in self.ids},'gpu_inventory':self.ids}
        self.daemon={'identity':{'pid':123,'start':'fixture'},'monotonic':0}
    def records(self,used=7):
        cards=[{'uuid':g,'name':'A100','memory_mib':used,'memory_reserved_mib':620,'memory_total_mib':40960,'driver_version':'fixture','health_ok':True,'ecc_uncorrectable':0,'processes':[],'util':0,'memory_util':0} for g in self.ids]
        return [{'boot':c.BOOT,'daemon_pid':123,'daemon_start':'fixture','version':c.VERSION,'monotonic':t,'started_monotonic':t-.1,'valid':True,'policy_sha256':c.observation_policy(self.policy),'cards':copy.deepcopy(cards)} for t in range(30,331,30)]
    def eligible(self,rows,now=330):
        with patch.object(c,'baseline_valid',lambda b:True):return c.idle_state(rows,self.policy,now,self.daemon)[0]
    def test_full_window_required_and_process_resets(self):
        rows=self.records();self.assertTrue(all(x['eligible'] for x in self.eligible(rows).values()))
        self.assertFalse(any(x['eligible'] for x in self.eligible(rows[1:]).values()))
        rows[-1]['cards'][0]['processes']=[{'pid':456}]
        self.assertFalse(self.eligible(rows)[self.ids[0]]['eligible'])
    def test_latch_invalidates_7mb_and_old4mb_history(self):
        seven=self.records();four=self.records(4)
        m.latch_fallback(self.policy,[{'uuid':self.ids[0],'memory_mib':8}],{'at':'fixture'})
        self.assertFalse(any(x['eligible'] for x in self.eligible(seven).values()))
        self.assertFalse(any(x['eligible'] for x in self.eligible(four).values()))
        self.assertTrue(all(x['eligible'] for x in self.eligible(self.records(4)).values()))
        self.assertFalse(any(x['eligible'] for x in self.eligible(self.records(7)).values()))

if __name__=='__main__':unittest.main()
