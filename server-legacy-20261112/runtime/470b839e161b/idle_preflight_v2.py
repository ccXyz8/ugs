"""Single current verifier contract; no historical verifier fallback."""
import json
from pathlib import Path
HERE = Path(__file__).resolve().parent

def install(preflight):
    original = preflight.validate_preflight
    preflight.VERIFIER = str(HERE/'verify_launch_idle_v2.py')
    preflight.DEPENDENCY = str(HERE/'preload.py')
    preflight.VERIFIER_SHA = preflight.core.hash_file(preflight.VERIFIER)
    preflight.DEPENDENCY_SHA = preflight.core.hash_file(preflight.DEPENDENCY)
    preflight.VERIFIER_SPEC = {**preflight.VERIFIER_SPEC,
        'path':preflight.VERIFIER,'sha256':preflight.VERIFIER_SHA,
        'dependency_path':preflight.DEPENDENCY,'dependency_sha256':preflight.DEPENDENCY_SHA}
    def validate(manifest, project, from_index=0):
        original(manifest, project, from_index)
        if manifest.get('project_id') not in preflight.PROJECTS: return
        for stage in manifest['stages'][from_index:]:
            report=json.loads(Path(manifest['admission']['stages'][stage['stage_id']]['cpu']['path']).read_text())
            for name in ('idle_runtime_v2.py','memory_runtime.py','native_scan.py','hash_scope.py','index_runtime.py'):
                path=str(HERE/name)
                if report.get('source_sha256',{}).get(path)!=preflight.core.hash_file(path):
                    raise ValueError('CPU evidence must bind current runtime helper: '+path)
    preflight.validate_preflight=validate
