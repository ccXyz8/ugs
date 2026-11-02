import contextlib,copy,json,os,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
import ugs_read_view_v1 as v


def initial():return {'revision':0}
def line(n,**extra):
 s={'revision':n,**extra};return v.canonical({'state':s,'sha256':v.digest(v.canonical(s))})+b'\n'
class Store:
 def __init__(self,root):
  self.root=Path(root);self.lock=self.root/'state.lock';self.journal=self.root/'events.jsonl'

class Tests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.store=Store(self.temp.name)
  self.raw=b''.join(line(i,jobs=[i]) for i in range(1,101));self.store.journal.write_bytes(self.raw)
 def snap(self,cache=True):return v.snapshot(self.store,initial,cache)
 def test_cold_and_warm_equal_full_replay(self):
  cold=self.snap();warm=self.snap();self.assertEqual(cold,warm);self.assertEqual(warm,self.snap(False))
 def test_warm_decodes_only_last_record_but_hashes_prefix(self):
  self.snap()
  with patch.object(v,'record',wraps=v.record) as records,patch.object(v,'digest',wraps=v.digest) as hashes:
   self.snap();self.assertEqual(records.call_count,1)
   self.assertTrue(any(a.args[0]==self.raw for a in hashes.call_args_list))
 def test_new_suffix_checked(self):
  self.snap();self.store.journal.write_bytes(self.raw+line(101,jobs=['new']))
  with patch.object(v,'record',wraps=v.record) as records:
   self.assertEqual(self.snap()['revision'],101);self.assertEqual(records.call_count,2)
 def test_older_prefix_corruption_rejected(self):
  self.snap();self.store.journal.write_bytes(self.raw.replace(b'"jobs":[1]',b'"jobs":[9]',1))
  with self.assertRaises(ValueError):self.snap()
 def test_bad_suffix_hash_rejected(self):
  self.snap();self.store.journal.write_bytes(self.raw+line(101).replace(b'"revision":101',b'"revision":102'))
  with self.assertRaises(ValueError):self.snap()
 def test_revision_gap_rejected(self):
  self.snap();self.store.journal.write_bytes(self.raw+line(102))
  with self.assertRaises(ValueError):self.snap()
 def test_partial_tail_rejected(self):
  self.snap();self.store.journal.write_bytes(self.raw+b'{"state":')
  with self.assertRaises(ValueError):self.snap()
 def test_truncation_and_rotation_full_verify(self):
  self.snap();self.store.journal.write_bytes(line(1,new_epoch=True));self.assertEqual(self.snap(),{'revision':1,'new_epoch':True})
  self.store.journal.write_bytes(line(1)+line(3))
  with self.assertRaises(ValueError):self.snap()
 def test_same_size_valid_rewrite_refreshes(self):
  self.snap();changed=self.raw.replace(line(1,jobs=[1]),line(1,jobs=[2]));self.assertEqual(len(changed),len(self.raw));self.store.journal.write_bytes(changed)
  with patch.object(v,'record',wraps=v.record) as calls:self.snap();self.assertEqual(calls.call_count,100)
 def test_cache_corruption_falls_back(self):
  self.snap();p=self.store.root/v.CACHE_NAME;p.write_text('broken')
  with patch.object(v,'record',wraps=v.record) as calls:self.snap();self.assertEqual(calls.call_count,100)
 def test_cache_bad_metadata_checksum_falls_back(self):
  self.snap();p=self.store.root/v.CACHE_NAME;d=json.loads(p.read_text());d['proof']['tail_start']=1;p.write_text(json.dumps(d))
  with patch.object(v,'record',wraps=v.record) as calls:self.snap();self.assertEqual(calls.call_count,100)
 def test_cache_symlink_ignored_and_target_untouched(self):
  target=self.store.root/'other';target.write_text('preserve');(self.store.root/v.CACHE_NAME).symlink_to(target)
  self.snap();self.assertEqual(target.read_text(),'preserve')
 def test_cache_world_readable_ignored(self):
  self.snap();os.chmod(self.store.root/v.CACHE_NAME,0o644)
  with patch.object(v,'record',wraps=v.record) as calls:self.snap();self.assertEqual(calls.call_count,100)
 def test_cache_write_failure_query_still_valid(self):
  with patch.object(v.os,'replace',side_effect=OSError('disk full')):self.assertEqual(self.snap()['revision'],100)
  self.assertEqual(list(self.store.root.glob('.read-view-*')),[])
 def test_cache_is_bounded_metadata_no_jobs(self):
  self.snap();p=self.store.root/v.CACHE_NAME;self.assertLess(p.stat().st_size,4096);self.assertNotIn('jobs',p.read_text());self.assertEqual(p.stat().st_mode & 0o777,0o600)
 def test_missing_or_empty_journal_not_stale(self):
  self.snap();self.store.journal.unlink();self.assertEqual(self.snap(),initial());self.store.journal.write_bytes(b'');self.assertEqual(self.snap(),initial())
 def test_session_one_snapshot_and_no_state_mutation(self):
  root=self.store.root
  class DefaultStore(Store):
   def __init__(self,where=root):super().__init__(where)
  core=types.SimpleNamespace(Store=DefaultStore,initial=initial)
  with patch.object(v,'snapshot',wraps=v.snapshot) as reads:
   with v.session(core,['experiment','list']):
    for i in range(3):
     with core.Store().tx() as s:self.assertEqual(s['jobs'],[100]);s['jobs'].append('local')
    self.assertEqual(reads.call_count,1)
    with self.assertRaises(ValueError):
     with core.Store().tx('write'):pass
  self.assertIs(core.Store,DefaultStore);self.assertEqual(self.store.journal.read_bytes(),self.raw)
 def test_mutation_and_gpu_commands_use_original_store(self):
  core=types.SimpleNamespace(Store=Store,initial=initial)
  for args in [['experiment','submit'],['resource','status'],['internal','serve'],['scheduler','pause']]:
   with v.session(core,args):self.assertIs(core.Store,Store)
 def test_shared_lock_released_before_validation(self):
  original=v.replay
  def check(*args,**kwargs):
   with self.store.lock.open('a+b') as lock:
    v.fcntl.flock(lock,v.fcntl.LOCK_EX|v.fcntl.LOCK_NB)
   return original(*args,**kwargs)
  with patch.object(v,'replay',side_effect=check):self.snap()
 def test_failure_does_not_refresh_cache(self):
  self.snap();p=self.store.root/v.CACHE_NAME;old=p.read_bytes();self.store.journal.write_bytes(self.raw+b'bad')
  with self.assertRaises(ValueError):self.snap()
  self.assertEqual(p.read_bytes(),old)

if __name__=='__main__':unittest.main()
