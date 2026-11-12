"""New submissions bind the accepted memory verifier; historical jobs are untouched."""
import copy,json
from pathlib import Path

ROOT = Path('/public/home/kzhang19/kezhang/cc/global-gpu-scheduler/candidates/memory_release/9ffee907f032')
HELPER_SHA = '7b9e57be91c37434586dcaf9fbbd7da1cb03a3d5aa45cdfdf78ac16a772a5b78'
VERIFY_SHA = '4f665def9e7500104735f40d6e8f8383c028e18902cbad2ccfefaf63d49b41e6'
PRELOAD_SHA = '6ee2d1354e9af32a430ce1d18b5ddef8b5a1344216608d0797e33f4a8c36212f'


def install(preflight):
    if getattr(preflight,'_memory_spec_installed',False):return
    original = preflight.validate_preflight
    preflight.VERIFIER = str(ROOT/'verify_launch_memory.py')
    preflight.DEPENDENCY = str(ROOT/'preload.py')
    preflight.VERIFIER_SHA = VERIFY_SHA
    preflight.DEPENDENCY_SHA = PRELOAD_SHA
    preflight.VERIFIER_SPEC = {**preflight.VERIFIER_SPEC,
        'path':preflight.VERIFIER,'sha256':VERIFY_SHA,
        'dependency_path':preflight.DEPENDENCY,'dependency_sha256':PRELOAD_SHA}
    def validate(manifest,project,from_index=0):
        original(manifest,project,from_index)
        if manifest.get('project_id') not in preflight.PROJECTS:return
        for stage in manifest['stages'][from_index:]:
            spec=manifest['admission']['stages'][stage['stage_id']]
            report=json.loads(Path(spec['cpu']['path']).read_text())
            path=str(ROOT/'memory_runtime.py')
            if report.get('source_sha256',{}).get(path)!=HELPER_SHA or preflight.core.hash_file(path)!=HELPER_SHA:
                raise ValueError('CPU evidence must bind accepted memory_runtime SHA')
    preflight.validate_preflight=validate
    preflight._memory_spec_installed=True
