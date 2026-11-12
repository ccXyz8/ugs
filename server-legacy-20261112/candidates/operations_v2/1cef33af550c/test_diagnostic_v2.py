import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import ugs_diagnostic_runner_v2 as d

class V2(unittest.TestCase):
    def test_native_rejected_before_identity_paths_or_child(self):
        for tool in ('ltrace','strace','auto',''):
            with patch.object(d.subprocess,'Popen') as spawn:
                with self.assertRaisesRegex(d.DiagnosticError,'NATIVE_TRACE_DISABLED'):
                    d.run(['anything'],project_dir='/missing',trace_log='/missing/t',events_file='/missing/e',trace_tool=tool,phase_name='test')
                spawn.assert_not_called()
    def invoke(self,code):
        with tempfile.TemporaryDirectory() as raw:
            root=Path(raw).resolve();env=dict(os.environ)
            env.update({k:'cpu-fixture' for k in d.IDENTITY_ENV})
            argv=[sys.executable,'-B',d.__file__,'--project-dir',str(root),'--trace-log',str(root/'trace'),
                  '--events-file',str(root/'events'),'--trace-tool','none','--phase','test','--',sys.executable,'-c',code]
            result=subprocess.run(argv,env=env,capture_output=True,text=True,timeout=15)
            self.assertEqual((root/'trace').stat().st_size,0)
            rows=[json.loads(x) for x in (root/'events').read_text().splitlines()]
            return result,rows
    def test_success_and_error_transparent(self):
        for rc in (0,7):
            result,rows=self.invoke("import sys;print('out');print('err',file=sys.stderr);sys.exit("+str(rc)+")")
            self.assertEqual(result.returncode,rc);self.assertEqual(result.stdout.strip(),'out');self.assertEqual(result.stderr.strip(),'err')
            self.assertEqual(rows[-1]['exit_code'],rc);self.assertTrue(rows[-1]['worker_exit_verified'])
    def test_signal_preserved(self):
        result,rows=self.invoke('import os,signal;os.kill(os.getpid(),signal.SIGTERM)')
        self.assertEqual(result.returncode,-15);self.assertEqual(rows[-1]['exit_code'],-15)
    def test_direct_worker_parent_no_tracer(self):
        result,rows=self.invoke('import os;print(os.getppid())')
        self.assertEqual(int(result.stdout.strip()),rows[0]['pid'])

if __name__=='__main__':unittest.main()
