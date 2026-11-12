import hashlib,tempfile,types,unittest
from pathlib import Path
import native_scan as n
import hash_scope
class Tests(unittest.TestCase):
 def fixture(self):
  rows=[];lines=[];names={}
  for i in range(8):
   bus='00000000:%02X:00.0'%(0x80+i);uid=str(i);names[uid]='NVIDIA A100'
   rows.append(','.join([str(i),bus,'driver','4','620','40960','0','0','Disabled','Disabled','Default','No','No','0']))
   lines.extend(['GPU '+bus,'    Processes : None','    Remapped Rows','        Pending : No','        Remapping Failure Occurred : No'])
  return '\n'.join(rows),'\n'.join(lines),names
 def test_index_and_empty_processes(self):
  cards=n.parse(*self.fixture());self.assertEqual([c['index'] for c in cards],list(range(8)));self.assertTrue(all(c['health_ok'] for c in cards))
 def test_graphics_process_preserved(self):
  a,b,c=self.fixture();b=b.replace('    Processes : None','    Processes\n        Process ID : 123\n            Type : G',1)
  self.assertEqual(n.parse(a,b,c)[0]['processes'],[{'pid':'123','type':'G'}])
 def test_unknown_missing_duplicate_rejected(self):
  a,b,c=self.fixture()
  for text in (b.replace('Processes : None','Processes : N/A',1),b.replace('Pending : No','Pending : N/A',1),b+b,b.replace('    Processes : None','',1)):
   with self.assertRaises(ValueError):n.parse(a,text,c)
  with self.assertRaises(ValueError):n.parse(a.replace(',4,',',N/A,',1),b,c)
 def test_remapping_blocks(self):
  a,b,c=self.fixture();self.assertFalse(n.parse(a,b.replace('Pending : No','Pending : Yes',1),c)[0]['health_ok'])
 def test_hash_scope(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'x';path.write_text('a');calls=[]
   def digest(p):calls.append(1);return hashlib.sha256(Path(p).read_bytes()).hexdigest()
   core=types.SimpleNamespace(hash_file=digest);hash_scope.install(core)
   with core.hash_scope():
    a=core.hash_file(path);self.assertEqual(a,core.hash_file(path));self.assertEqual(core.hash_scope_stats,{"hashed":1,"reused":1})
    path.write_text('b');self.assertNotEqual(a,core.hash_file(path));self.assertEqual(core.hash_scope_stats["hashed"],2)
   core.hash_file(path);self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
