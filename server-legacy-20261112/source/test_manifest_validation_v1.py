"""Isolated CPU regression tests; no production queue mutations or CUDA."""
import copy,json,sys,unittest
from pathlib import Path
import test_control_v2 as fixture
import ugs_manifest_validation_v1 as validation
validation.install()
u=fixture.u

class SubmissionVersionTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture.reset()
        self.project=fixture.project(self.root)
        self.manifest=fixture.manifest(self.project)

    def test_invalid_versions_rejected_by_validate_and_submit_without_writes(self):
        for value in [1,0,True,False,None,[],{},""," "]:
            for action in ['validate','submit']:
                with self.subTest(value=value,action=action):
                    manifest=copy.deepcopy(self.manifest)
                    manifest['submission_version']=value
                    path=self.root/'invalid.json';path.write_text(json.dumps(manifest))
                    before=u.Store().read()
                    with self.assertRaisesRegex(ValueError,'submission_version must be a non-empty string'):
                        fixture.runcli('experiment',action,'--file',str(path))
                    self.assertEqual(before,u.Store().read())

    def test_missing_version_rejected(self):
        del self.manifest['submission_version']
        with self.assertRaisesRegex(ValueError,'submission_version'):
            u.job_validate(self.manifest,self.project)
        with self.assertRaisesRegex(ValueError,'submission_version'):
            fixture.submit(self.manifest)

    def test_valid_string_preserves_key_hash_and_idempotency(self):
        before=copy.deepcopy(self.manifest)
        path=self.root/'valid.json';path.write_text(json.dumps(self.manifest))
        result=fixture.runcli('experiment','validate','--file',str(path))
        self.assertTrue(result['valid'])
        first=fixture.runcli('experiment','submit','--file',str(path))
        second=fixture.runcli('experiment','submit','--file',str(path))
        self.assertEqual(first['job_id'],second['job_id'])
        self.assertTrue(second['duplicate'])
        self.assertEqual(u.key_for(self.manifest),'A:one:1')
        self.assertEqual(self.manifest,before)
        self.assertEqual(result['manifest_sha256'],first['manifest_sha256'])
        self.assertEqual(len(u.Store().read()['jobs']),1)

    def test_invalid_update_keeps_original_job(self):
        first=fixture.submit(self.manifest)
        manifest=copy.deepcopy(self.manifest);manifest['submission_version']=2
        path=self.root/'update.json';path.write_text(json.dumps(manifest))
        before=u.Store().read()
        with self.assertRaisesRegex(ValueError,'submission_version'):
            fixture.runcli('experiment','update',first['job_id'],'--file',str(path),'--expected-sha',first['manifest_sha256'])
        self.assertEqual(before,u.Store().read())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(SubmissionVersionTests))
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'cuda_used':False,'production_state_changed':False,'source_sha256':u.hash_file(Path(validation.__file__))}
    u.fresh_json(fixture.FIX/'manifest-validation-result.json',report)
    print(json.dumps({'evidence':str(fixture.FIX/'manifest-validation-result.json'),**report}))
    sys.exit(0 if result.wasSuccessful() else 1)
