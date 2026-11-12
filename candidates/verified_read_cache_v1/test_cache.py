import json,tempfile,types,unittest
from pathlib import Path
import verified_read_cache as cache
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'journal';self.path.write_text('{"value":1}\n')
  class Store:
   calls=0
   def __init__(s):s.journal=self.path
   def read(s):
    Store.calls+=1
    if not s.journal.exists():return {'value':0}
    raw=s.journal.read_bytes()
    if not raw.endswith(b'\n'):raise ValueError('incomplete')
    return json.loads(raw)
  self.cls=Store;cache.install(types.SimpleNamespace(Store=Store));self.store=Store()
 def test_same_bytes_reuse_verified_value(self):
  self.assertEqual(self.store.read(),self.store.read());self.assertEqual(self.cls.calls,1)
 def test_return_value_is_not_mutable_cache(self):
  self.store.read()['value']=9;self.assertEqual(self.store.read()['value'],1)
 def test_same_size_change_revalidated(self):
  self.store.read();self.path.write_text('{"value":2}\n');self.assertEqual(self.store.read()['value'],2);self.assertEqual(self.cls.calls,2)
 def test_corruption_rejected_after_cache(self):
  self.store.read();self.path.write_text('{"value":1}')
  with self.assertRaises(ValueError):self.store.read()
 def test_deletion_invalidates(self):
  self.store.read();self.path.unlink();self.assertEqual(self.store.read()['value'],0)
if __name__=='__main__':unittest.main()
