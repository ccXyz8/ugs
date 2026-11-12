"""Linux CPU-only acceptance: a fake driver DSO, never a real GPU library."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

@unittest.skipUnless(sys.platform.startswith('linux') and shutil.which('gcc') and shutil.which('ltrace'), 'requires Linux gcc/ltrace')
class NativeAcceptance(unittest.TestCase):
    def test_dynamic_symbol_and_real_worker_exit(self):
        with tempfile.TemporaryDirectory(prefix='ugs-native-cpu-') as directory:
            root = Path(directory).resolve()
            (root / 'stub.c').write_text('int cuInit(unsigned int n){return 0;}\n')
            library = root / 'libcuda.so.1'
            subprocess.run(['gcc', '-shared', '-fPIC', str(root/'stub.c'), '-o', str(library)], check=True)
            env = os.environ.copy()
            for key in ('UGS_JOB_ID','UGS_RUN_ID','UGS_STAGE_ID','UGS_GENERATION','UGS_STAGE_NONCE','UGS_MANIFEST_SHA256'):
                env[key] = 'CPU_ACCEPTANCE'
            runner = Path(__file__).with_name('ugs_diagnostic_runner.py')
            for code in (0, 7):
                with self.subTest(exit_code=code):
                    trace = root / ('trace%d.log' % code)
                    command = [sys.executable, '-B', str(runner), '--project-dir', str(root), '--trace-log', str(trace), '--events-file', str(root/'events.jsonl'), '--trace-tool', 'ltrace', '--phase', 'gpu-worker', '--', sys.executable, '-B', '-c', "import ctypes,sys;ctypes.CDLL(%r).cuInit(0);print('OUT_SENTINEL');print('ERR_SENTINEL',file=sys.stderr);sys.exit(%d)" % (str(library), code)]
                    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=30)
                    self.assertEqual(result.returncode, code, result.stderr)
                    data = trace.read_text()
                    self.assertIn('cuInit', data)
                    self.assertIn('ERR_SENTINEL', result.stderr)
                    self.assertIn('OUT_SENTINEL', result.stdout)
                    self.assertNotIn('ERR_SENTINEL', data)
                    rows = [json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
                    self.assertEqual(rows[-1]['exit_code'], code)
                    self.assertLessEqual(trace.stat().st_size, 4*1024*1024)

if __name__ == '__main__':
    unittest.main()
