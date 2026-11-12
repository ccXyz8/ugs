import copy,unittest
from unittest.mock import patch
from test_idle_v2 import Window,c,w
class CurrentState(unittest.TestCase):
 rows=Window.rows
 details=Window.details
 def setUp(self):
  Window.setUp(self)
  self.policy.update(idle_seconds=0,scan_seconds=30,max_sample_gap_seconds=35)
 def test_one_sample_is_sufficient_without_wait(self):
  self.assertEqual(w.parameters(self.policy),(0,30,35,1))
  rows=self.rows((75,));details=self.details(rows)
  self.assertTrue(all(x['eligible'] and x['count']==1 and x['idle_seconds']==0 for x in details.values()))
  c.require_idle_window(rows,self.ids[:2],self.policy,75,self.daemon)
 def test_prior_busy_or_gap_does_not_require_cooldown(self):
  rows=self.rows((1,75));rows[0]['cards'][0]['processes']=[{'pid':123,'type':'C'}]
  self.assertTrue(all(x['eligible'] for x in self.details(rows).values()))
 def test_latest_busy_card_still_rejects_whole_selected_group(self):
  rows=self.rows((75,));rows[0]['cards'][0]['processes']=[{'pid':123,'type':'C'}]
  with self.assertRaises(ValueError):c.require_idle_window(rows,self.ids[:2],self.policy,75,self.daemon)
 def test_invalid_last_sample_cannot_reuse_prior_good_one(self):
  rows=self.rows((60,75));rows[-1]['valid']=False
  self.assertFalse(any(x['eligible'] for x in self.details(rows).values()))
 def test_latest_over5_seconds_still_rejected(self):
  rows=self.rows((75,));rows[-1]['started_monotonic']=69.9
  self.assertFalse(any(x['eligible'] for x in self.details(rows).values()))
 def test_stale_over35_rejected_and_no_observation_rejected(self):
  self.assertTrue(all(x['eligible'] for x in self.details(self.rows((75,)),110).values()))
  self.assertFalse(any(x['eligible'] for x in self.details(self.rows((75,)),110.01).values()))
  self.assertFalse(any(x['eligible'] for x in self.details([]).values()))
 def test_invalidation_requires_new_snapshot_but_no_timer(self):
  rows=self.rows((60,));rows.append({'boot':c.BOOT,'daemon_pid':123,'daemon_start':'fixture','version':c.VERSION,'monotonic':70,'invalidated_gpus':self.ids})
  self.assertFalse(any(x['eligible'] for x in self.details(rows).values()))
  rows.extend(self.rows((75,)))
  self.assertTrue(all(x['eligible'] for x in self.details(rows).values()))
 def test_old_verifier_blocked_v2_uses_zero(self):
  job={'stage_index':0,'manifest':{'stages':[{'stage_id':'x'}]}}
  for kind in ('v1','legacy300','unknown'):
   with patch.object(w,'contract',return_value=kind):
    self.assertFalse(w.stage_policy(c,job,self.policy)['compatible'])
  with patch.object(w,'contract',return_value='v2'):
   self.assertTrue(w.stage_policy(c,job,self.policy)['compatible'])
   self.assertEqual(w.stage_policy(c,job,self.policy)['idle_seconds'],0)
 def test_current_candidates_and_baseline_health_remain_strict(self):
  idle=c.Idle();idle.sync(self.rows((75,)),self.policy,self.daemon,75)
  self.assertEqual(len(idle.candidates(75)),8)
  for key,value in [('memory_mib',8),('processes',None),('health_ok',False),('util',1),('ecc_uncorrectable',1)]:
   rows=self.rows((75,));rows[-1]['cards'][0][key]=value
   self.assertFalse(self.details(rows)[self.ids[0]]['eligible'])
if __name__=='__main__':unittest.main()
