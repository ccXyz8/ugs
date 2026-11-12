import copy,unittest
from unittest.mock import patch
import ugs_idle_v2_entry as e
c=e.core
class Index(unittest.TestCase):
 def setUp(self):
  self.profile=dict(profile_id='fixture',gpu_count=2,gpu_model='A100',precision='bf16',execution='ddp',world_size=2,host_start_mib=1,host_running_mib=1,disk_min_gib=1,acceptance_ref='fixture',gpu_indices=[2,6])
  self.job={'job_id':'j','project_id':'p','stage_index':0,'manifest':{'stages':[{'stage_id':'train','resource_profiles':[self.profile]}]}}
  self.state={'policy':{'gpu_identity':'index','gpu_inventory':[str(i) for i in range(8)],'idle_seconds':0,'scan_seconds':30,'max_sample_gap_seconds':35},'projects':{'p':{}},'active_runs':{},'gpu_reservations':{}}
 def test_index_validation(self):
  c.profile_validate(self.profile)
  for value in ([True,2],[2,2],['2','6'],[2,9],[2]):
   with self.assertRaises(ValueError):c.profile_validate(dict(self.profile,gpu_indices=value))
  with self.assertRaises(ValueError):c.profile_validate(dict(self.profile,gpu_uuids=['GPU-x']))
 def test_selection_reserves_and_occupied(self):
  idle=type('Idle',(),{'candidates':lambda *a:['2','6','7']})()
  with patch.object(c,'stage_idle_policy',return_value={'compatible':True}),patch.object(c,'accepted_stage',return_value=True),patch.object(c,'budgets_ready',return_value=True):
   self.assertEqual(c.select_profile(self.job,self.state,idle)[1],['2','6'])
   self.state['gpu_reservations']={'6':{}};self.assertIsNone(c.select_profile(self.job,self.state,idle))
   self.state['gpu_reservations']={};self.state['active_runs']={'other':{'gpus':['6']}};self.assertIsNone(c.select_profile(self.job,self.state,idle))
 def test_uuid_target_rejected(self):
  self.assertEqual(c.resolve_gpu_targets('2,6',self.state['policy']),['2','6'])
  for x in ('2,2','GPU-x','8','-1'):
   with self.assertRaises(ValueError):c.resolve_gpu_targets(x,self.state['policy'])
 def test_mapping_change_blocks(self):
  self.assertEqual(c.card_reason({'pci_bus_id':'new'},{'pci_bus_id':'old'},True),'DEVICE_MAPPING_CHANGED')
 def test_old_contract_not_compatible(self):
  import idle_runtime_v2 as w
  with patch.object(w,'contract',return_value='v2'):self.assertFalse(c.stage_idle_policy(self.job,self.state['policy'])['compatible'])
 def test_waiting_filter_does_not_replace_launch_integrity(self):
  self.state['policy'].update(dispatch_enabled=True,migration_cleared=True,execution_mode='authorized_direct_ssh')
  self.assertTrue(c.waiting_eligible(self.state))
  with patch.object(c,'stage_idle_policy',return_value={'compatible':True}),patch.object(c,'eligible',return_value=False):
   self.assertFalse(c.launch_valid(self.state,self.job,'sha',['2','6']))
if __name__=='__main__':unittest.main()
