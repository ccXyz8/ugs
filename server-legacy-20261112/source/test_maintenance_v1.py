import unittest,importlib.util,sys,json,uuid,time,os,copy,gzip,threading
from pathlib import Path
ROOT=Path("/public/home/kzhang19/kezhang/cc/global-gpu-scheduler")
sys.path.insert(0,str(ROOT/"source"))
import ugs_maintenance_v1 as m
core=m.core
fixture=ROOT/"acceptance"/("maintenance-"+uuid.uuid4().hex);fixture.mkdir()
class Tests(unittest.TestCase):
 def setUp(self):
  self.root=fixture/self._testMethodName;self.root.mkdir();m.ROOT=self.root
  self.pol=dict(m.DEFAULT);self.pol.update(journal_trigger_mib=2,journal_target_mib=1)
  (self.root/"retention-policy.json").write_text(json.dumps(self.pol))
 def bigjournal(self):
  with core.Store(self.root).tx("seed") as s:
   s["jobs"]=[{"job_id":"job-000005","queue_seq":5,"run_id":"same","manifest_sha256":"same","status":"RUNNING","manifest":{"x":"unchanged"}}]
   s["active_runs"]={"job-000005":{"guardian":{"pid":22,"start":"33"},"gpus":["g6","g7"],"stage_nonce":"same"}}
   s["padding"]="x"*110000
  for n in range(28):
   with core.Store(self.root).tx("edit") as s:s["counter"]=n
  return core.Store(self.root).read()
 def test_checkpoint_preserves_all_state_and_legacy_readers(self):
  before=self.bigjournal();res=m.compact_journal(self.pol,time.time(),False)
  after=core.Store(self.root).read()
  trimmed=copy.deepcopy(after);trimmed["revision"]=before["revision"];trimmed.pop("journal_epoch")
  self.assertEqual(before,trimmed)
  self.assertLess(res["after_bytes"],res["before_bytes"])
  self.assertEqual(json.loads(gzip.decompress(Path(res["archive"]).read_bytes()).splitlines()[-1])["state"],before)
  versions=list((ROOT/"source").glob("ugs_exec_v2*.py"))+[ROOT/"source/ugs_exec_v3.py"]
  for index,p in enumerate(versions):
   spec=importlib.util.spec_from_file_location("legacy"+str(index),p);legacy=importlib.util.module_from_spec(spec);sys.modules[spec.name]=legacy;spec.loader.exec_module(legacy)
   self.assertEqual(legacy.Store(self.root).read(),after)
  with core.Store(self.root).tx("post_checkpoint") as s:s["counter"]=100
  self.assertEqual(core.Store(self.root).read()["counter"],100)
 def test_compaction_dryrun_no_change(self):
  self.bigjournal();p=self.root/"events.jsonl";before=p.read_bytes()
  self.assertIsNotNone(m.compact_journal(self.pol,time.time(),True))
  self.assertEqual(p.read_bytes(),before);self.assertFalse((self.root/"maintenance-archives").exists())
 def test_corrupt_journal_not_repaired_or_deleted(self):
  self.bigjournal();p=self.root/"events.jsonl";p.write_bytes(p.read_bytes()+b'bad')
  before=p.read_bytes()
  with self.assertRaises(ValueError):m.compact_journal(self.pol,time.time(),False)
  self.assertEqual(p.read_bytes(),before)
 def test_writer_serializes_with_compaction(self):
  self.bigjournal()
  def writer():
   for n in range(10):
    with core.Store(self.root).tx("concurrent") as s:s["written"]=s.get("written",0)+1
  thread=threading.Thread(target=writer);thread.start()
  m.compact_journal(self.pol,time.time(),False);thread.join()
  self.assertEqual(core.Store(self.root).read()["written"],10)
 def test_resource_age_size_and_partial_tail(self):
  p=self.root/"resources.jsonl";recent=core.stamp()
  old=json.dumps({"at":"2020-01-01T00:00:00Z","value":"old"}).encode()+b"\n"
  new=json.dumps({"at":recent,"value":"new"}).encode()+b"\n";p.write_bytes(old+new)
  self.assertEqual(m.trim_jsonl(p,7,1024,time.time()),new)
  self.assertEqual(m.trim_jsonl(p,100000,len(new),time.time()),new)
  p.write_bytes(old+b'{"at":')
  with self.assertRaises(ValueError):m.trim_jsonl(p,7,1024,time.time())
 def test_scoped_paths_symlinks_and_hardlinks(self):
  outside=fixture/"outside";outside.write_text("protected")
  p=self.root/"resources.jsonl";p.symlink_to(outside)
  with self.assertRaises(ValueError):m.safe(p)
  with self.assertRaises(ValueError):m.safe(outside)
  q=self.root/"service.log";os.link(outside,q)
  with self.assertRaises(ValueError):m.safe(q)
  self.assertEqual(outside.read_text(),"protected")
 def test_archive_age_size_cleanup_and_scope(self):
  d=m.archive_dir()
  old=d/"journal-old.jsonl.gz";old.write_bytes(b"old");os.utime(old,(0,0))
  current=d/"journal-current.jsonl.gz";current.write_bytes(b"current")
  protected=d/"evidence.json";protected.write_text("keep")
  self.assertEqual(m.archive_removals(self.pol,time.time()),[old])
  m.run()
  self.assertFalse(old.exists());self.assertTrue(current.exists());self.assertTrue(protected.exists())
 def test_service_copytruncate_preserves_inode(self):
  p=self.root/"service.log";p.write_bytes(b"old\n"*(5*m.MIB))
  inode=p.stat().st_ino
  m.run();self.assertEqual(p.stat().st_ino,inode);self.assertLessEqual(p.stat().st_size,8*m.MIB)
 def test_noop_keeps_pinned_files_and_current_state(self):
  for name in ["protocol-v2.md","retention-evidence.json"]: (self.root/name).write_text("protected")
  with core.Store(self.root).tx("seed") as s:s["jobs"]=[{"job_id":"job-000001"}]
  before=(self.root/"events.jsonl").read_bytes();m.run()
  self.assertEqual((self.root/"events.jsonl").read_bytes(),before)
  self.assertEqual((self.root/"protocol-v2.md").read_text(),"protected")
 def test_invalid_policy_rejected(self):
  p=self.root/"retention-policy.json"
  bad=dict(self.pol);bad["resource_max_mib"]=0;p.write_text(json.dumps(bad))
  with self.assertRaises(ValueError):m.policy()
suite=unittest.defaultTestLoader.loadTestsFromTestCase(Tests)
result=unittest.TextTestRunner(verbosity=2).run(suite)
m.ROOT=ROOT
e={"at":core.stamp(),"tests":result.testsRun,"failures":len(result.failures),"errors":len(result.errors),
   "passed":result.wasSuccessful(),"production_logs_changed":False,"cuda_used":False,"fixture":str(fixture),
   "source_sha256":core.hash_file(ROOT/"source/ugs_maintenance_v1.py")}
(fixture/"result.json").write_text(json.dumps(e,indent=2)+"\n")
print(json.dumps(e));sys.exit(0 if result.wasSuccessful() else 1)
