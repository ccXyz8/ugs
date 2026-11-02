"""CPU-only isolated admission and failure evidence regression suite."""
import copy,json,sys,unittest
from pathlib import Path
import test_control_v2 as fixture
import ugs_preflight_v1 as pre
u=fixture.u
pre.install()

class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.root=fixture.reset();self.project=fixture.project(self.root,'ms-swift-main')
        self.manifest=fixture.manifest(self.project)
        self.stage=self.manifest['stages'][0]
        self.cpu=Path(self.manifest['admission']['stages']['one']['cpu']['path'])
        evidence=self.root/'ms-swift-main'/'actual-checks.json';evidence.write_text('{"passed":true,"fixture_only":true}')
        self.stage['failure_record']={'contract':'ugs-failure-v1','path':str(Path(self.stage['output_dir'])/'failure.json')}
        self.report=json.loads(self.cpu.read_text())
        self.report['preflight']={'contract':'ugs-preflight-v1','stage_id':'one','verifier':copy.deepcopy(pre.VERIFIER_SPEC),
           'failure_record':copy.deepcopy(self.stage['failure_record']),
           'checks':[{'name':n,'passed':True,'evidence':{'path':str(evidence),'sha256':u.hash_file(evidence)}} for n in sorted(pre.CHECKS)]}
        self.report['source_sha256'].update({pre.VERIFIER:pre.VERIFIER_SHA,pre.DEPENDENCY:pre.DEPENDENCY_SHA})
        self.save()
    def save(self):
        if 'preflight' in self.report:self.report['preflight']['stage_sha256']=u.digest(u.canonical(self.stage))
        self.cpu.write_text(json.dumps(self.report));self.manifest['admission']['stages']['one']['cpu']['sha256']=u.hash_file(self.cpu)
    def validate(self):return u.job_validate(self.manifest,self.project)
    def test_valid_and_idempotent(self):
        self.validate();a=fixture.submit(self.manifest);b=fixture.submit(self.manifest)
        self.assertEqual(a['job_id'],b['job_id']);self.assertTrue(b['duplicate'])
    def test_missing_contract_does_not_create_job(self):
        self.report.pop('preflight');self.save();before=u.Store().read()
        with self.assertRaisesRegex(ValueError,'ugs-preflight'):fixture.submit(self.manifest)
        self.assertEqual(before,u.Store().read())
    def test_all_seven_checks_required(self):
        self.report['preflight']['checks'].pop();self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_duplicate_check_rejected(self):
        self.report['preflight']['checks'].append(self.report['preflight']['checks'][0]);self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_wrong_stage_rejected(self):
        self.report['preflight']['stage_id']='other';self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_old_verifier_rejected(self):
        self.report['preflight']['verifier']['path']='old';self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_two_second_call_timeout_rejected(self):
        self.report['preflight']['verifier']['initial_timeout_seconds']=2;self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_missing_external_dependency_binding_rejected(self):
        del self.report['source_sha256'][pre.DEPENDENCY];self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_failed_check_rejected(self):
        self.report['preflight']['checks'][0]['passed']=False;self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_evidence_hash_mismatch_rejected(self):
        Path(self.report['preflight']['checks'][0]['evidence']['path']).write_text('{"passed":false}')
        with self.assertRaises(ValueError):self.validate()
    def test_failed_evidence_rejected_even_when_rehashed(self):
        p=Path(self.report['preflight']['checks'][0]['evidence']['path']);p.write_text('{"passed":false}')
        for x in self.report['preflight']['checks']:x['evidence']['sha256']=u.hash_file(p)
        self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_failure_path_cannot_escape_project_output(self):
        self.stage['failure_record']['path']=str(self.root/'outside.json')
        self.report['preflight']['failure_record']=copy.deepcopy(self.stage['failure_record']);self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_existing_failure_file_rejected(self):
        Path(self.stage['failure_record']['path']).write_text('{}')
        with self.assertRaises(ValueError):self.validate()
    def test_legacy_replay_preserved(self):
        # Existing same-content idempotency hits do not enqueue a new job or demand migration.
        old=copy.deepcopy(self.manifest);self.report.pop('preflight');self.save();old=copy.deepcopy(self.manifest)
        key=u.key_for(old)
        with u.Store().tx('fixture_old_job') as s:
            s['jobs'].append({'job_id':'old','idempotency_key':key,'manifest_sha256':u.digest(u.canonical(old)),'status':'QUEUED'})
        r=fixture.submit(old);self.assertTrue(r['duplicate']);self.assertEqual(r['job_id'],'old')
    def test_failure_path_cannot_overwrite_log_or_receipt(self):
        for key in ('log_path','result_path'):
            self.stage['failure_record']['path']=self.stage[key]
            self.report['preflight']['failure_record']=copy.deepcopy(self.stage['failure_record']);self.save()
            with self.assertRaisesRegex(ValueError,'overwrite'):self.validate()
    def test_final_argv_change_invalidates_evidence(self):
        self.stage['argv'].append('--unexpected-after-check')
        with self.assertRaisesRegex(ValueError,'stage SHA'):self.validate()
    def test_unstarted_suffix_preserves_frozen_prefix(self):
        prefix=copy.deepcopy(self.stage);prefix['stage_id']='frozen'
        prefix.pop('failure_record')
        self.manifest['stages'].insert(0,prefix)
        pre.validate_preflight(self.manifest,self.project,1)
    def test_symlink_escape_rejected(self):
        link=Path(self.stage['output_dir'])/'escape';link.symlink_to(self.root,target_is_directory=True)
        self.stage['failure_record']['path']=str(link/'failure.json')
        self.report['preflight']['failure_record']=copy.deepcopy(self.stage['failure_record']);self.save()
        with self.assertRaises(ValueError):self.validate()
    def test_running_view_unchanged(self):
        self.assertEqual(pre.failure_view({'status':'RUNNING'}),{})
    def make_failure(self):
        r=fixture.submit(self.manifest)
        with u.Store().tx('fixture_failure') as s:
            j=u.get_job(s,r['job_id']);j['status']='FAILED';j['terminal_reason']='adapter exited 1'
            proof=Path(self.stage['output_dir'])/'guardian.json';proof.write_text(json.dumps({'generation':'gen','stage_nonce':'nonce'}));j['guardian_result']=str(proof)
        j=copy.deepcopy(u.get_job(u.Store().read(),r['job_id']))
        record={'contract':'ugs-failure-v1','job_id':j['job_id'],'run_id':j['run_id'],'stage_id':'one','manifest_sha256':j['manifest_sha256'],
                'generation':'gen','stage_nonce':'nonce','automatic_retry':False,'phase':'preload','category':'PRELOAD_REJECTED',
                'reason':'PRELOAD_RECHECK_FAILED','message':'Final GPU recheck became stale','exit_code':2,
                'verifier':{'allowed':False,'message':'Final GPU recheck became stale'}}
        path=Path(self.stage['failure_record']['path']);path.write_text(json.dumps(record))
        return j,path,record
    def test_failure_detail_and_full_message_visible(self):
        j,p,r=self.make_failure();v=pre.failure_view(j)
        self.assertEqual(v['failure_record_status'],'VERIFIED');self.assertEqual(v['failure_message'],r['message'])
        self.assertEqual(v['failure_detail']['verifier'],r['verifier'])
    def test_wrong_generation_not_trusted(self):
        j,p,r=self.make_failure();r['generation']='other';p.write_text(json.dumps(r))
        self.assertEqual(pre.failure_view(j)['failure_record_status'],'UNVERIFIED')
    def test_bad_json_does_not_break_list(self):
        j,p,r=self.make_failure();p.write_text('broken')
        self.assertEqual(pre.failure_view(j)['failure_record_status'],'UNVERIFIED')
    def test_large_record_bounded(self):
        j,p,r=self.make_failure();p.write_text('x'*(pre.LIMIT+1))
        self.assertEqual(pre.failure_view(j)['failure_record_status'],'UNVERIFIED')
    def test_legacy_failure_explicitly_unknown(self):
        j,p,r=self.make_failure();del j['manifest']['stages'][0]['failure_record']
        self.assertEqual(pre.failure_view(j)['failure_record_status'],'LEGACY_NO_STRUCTURED_RECORD')
    def test_read_view_does_not_mutate_queue(self):
        j,p,r=self.make_failure();before=u.Store().read();pre.enrich_output(j,['experiment','show',j['job_id']]);self.assertEqual(before,u.Store().read())

if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(PreflightTests))
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'cuda_used':False,'production_state_changed':False,'source_sha256':u.hash_file(Path(pre.__file__))}
    u.fresh_json(fixture.FIX/'preflight-result.json',report)
    print(json.dumps({'evidence':str(fixture.FIX/'preflight-result.json'),**report}))
    sys.exit(0 if result.wasSuccessful() else 1)
