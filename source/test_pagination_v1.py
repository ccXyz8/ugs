"""CPU-only pagination and public CLI tests; no server, state or GPU access."""
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
import ugs_pagination_v1 as p


def rows(n):
    return [{'job_id': 'job-%06d' % i, 'queue_seq': n-i, 'status': 'QUEUED'} for i in range(1, n+1)]


class PaginationTests(unittest.TestCase):
    def test_default_and_explicit_first_identical(self):
        for command in [('experiment','list'),('scheduler','status'),('scheduler','get')]:
            self.assertEqual(p.parse_page(list(command)), (list(command),1))
            self.assertEqual(p.parse_page(list(command)+['--page','1']), (list(command),1))
    def test_newest_first_and_boundary(self):
        for n in (0,1,14,15,16,29,30,31):
            original=rows(n); frozen=copy.deepcopy(original);seen=[]
            for page in range(1,max(1,(n+14)//15)+1):
                result,info=p.paginate(original,['experiment','list'],page)
                self.assertLessEqual(len(result),15);seen+=result
                self.assertEqual(info['total_jobs'],n)
            self.assertEqual([x['job_id'] for x in seen],[x['job_id'] for x in reversed(original)])
            self.assertEqual(original,frozen)
    def test_numeric_ids_not_queue_priority(self):
        data=[{'job_id':'job-9','queue_seq':99},{'job_id':'job-10','queue_seq':1}]
        self.assertEqual(p.paginate(data,['experiment','list'],1)[0][0]['job_id'],'job-10')
    def test_status_active_and_counts_not_paginated(self):
        original={'jobs':rows(31),'active':rows(17),'active_count':17,'max_experiment':20}
        frozen=copy.deepcopy(original)
        result,info=p.paginate(original,['scheduler','status'],2)
        self.assertEqual(len(result['jobs']),15)
        self.assertEqual(result['jobs'][0]['job_id'],'job-000016')
        self.assertEqual(result['active'],original['active']);self.assertEqual(result['active_count'],17)
        self.assertEqual(original,frozen)
    def test_page_after_filter(self):
        data=rows(40)[::2];result,info=p.paginate(data,['experiment','list','--project','A'],2)
        self.assertEqual(info['total_jobs'],20);self.assertEqual(len(result),5)
    def test_out_of_range_empty(self):
        result,info=p.paginate(rows(30),['experiment','list'],3)
        self.assertEqual(result,[]);self.assertEqual(info['total_pages'],2)
    def test_invalid_pages(self):
        for value in ('0','-1','1.5','abc','','+1',' 2'):
            with self.subTest(value=value), self.assertRaises(ValueError):p.parse_page(['experiment','list','--page',value])
        for tail in (['--page'],['--page','1','--page=2']):
            with self.assertRaises(ValueError):p.parse_page(['experiment','list']+tail)
    def test_equals_and_other_flags_preserved(self):
        self.assertEqual(p.parse_page(['experiment','list','--project','A','--page=2','--status','FAILED']),(['experiment','list','--project','A','--status','FAILED'],2))
    def test_other_commands_unchanged_or_reject_flag(self):
        self.assertEqual(p.parse_page(['experiment','show','job-000001']),(['experiment','show','job-000001'],None))
        with self.assertRaises(ValueError):p.parse_page(['experiment','stop','--all','--page','1'])
    def test_public_cli_wiring_json_and_table(self):
        core=types.ModuleType('ugs_exec_v3_1');calls=[]
        def fake_cli():
            calls.append(sys.argv[1:]);print(json.dumps({'jobs':rows(31),'active':[],'version':'fixture'} if sys.argv[1]=='scheduler' else rows(31)))
        core.cli=fake_cli
        mocks={'ugs_read_view_v1':types.SimpleNamespace(session=lambda *a,**k:contextlib.nullcontext()),'ugs_exec_v3_1':core,'ugs_control_v2':types.SimpleNamespace(install=lambda:None),
            'ugs_preflight_v1':types.SimpleNamespace(install=lambda:None,enrich_output=lambda data,args:data),
            'ugs_maintenance_v1_1':types.SimpleNamespace()}
        spec=importlib.util.spec_from_file_location('test_public_cli',Path(__file__).with_name('ugs_cli_control_v2.py'))
        with patch.dict(sys.modules,mocks):
            cli=importlib.util.module_from_spec(spec);spec.loader.exec_module(cli)
        cli._allocations={}
        for command in (['experiment','list'],['scheduler','status']):
            for extra in ([],['--page','2'],['--page=3']):
                with patch.object(sys,'argv',['ugs']+command+extra+['--json']),contextlib.redirect_stdout(io.StringIO()) as out:
                    self.assertEqual(cli.main(),0)
                data=json.loads(out.getvalue());selected=data if isinstance(data,list) else data['jobs']
                self.assertEqual(len(selected),1 if extra==['--page=3'] else 15)
                self.assertNotIn('--page',calls[-1]);self.assertFalse(any(x.startswith('--page=') for x in calls[-1]))
            with patch.object(sys,'argv',['ugs']+command),contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(cli.main(),0)
            self.assertIn('第 1 页 / 共 3 页',out.getvalue());self.assertIn('job-000031',out.getvalue());self.assertNotIn('job-000016',out.getvalue())
        count=len(calls)
        with patch.object(sys,'argv',['ugs','experiment','list','--page','0']),contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(),2)
        self.assertEqual(len(calls),count)

if __name__=='__main__':unittest.main()
