"""New admission binds the shared idle-window implementation and verifier."""
from pathlib import Path
import json

HERE = Path(__file__).resolve().parent


def install(preflight):
    if getattr(preflight, '_idle_window_spec', False):return
    old_validate = preflight.validate_preflight
    verifier = HERE/'verify_launch_idle.py'
    helper = HERE/'idle_runtime.py'
    preflight.VERIFIER = str(verifier)
    preflight.VERIFIER_SHA = preflight.core.hash_file(str(verifier))
    preflight.VERIFIER_SPEC = {**preflight.VERIFIER_SPEC,
        'path':str(verifier), 'sha256':preflight.VERIFIER_SHA}
    def validate(manifest, project, from_index=0):
        old_validate(manifest,project,from_index)
        if manifest.get('project_id') not in preflight.PROJECTS:return
        for stage in manifest['stages'][from_index:]:
            spec=manifest['admission']['stages'][stage['stage_id']]
            report=json.loads(Path(spec['cpu']['path']).read_text())
            if report.get('source_sha256',{}).get(str(helper)) != preflight.core.hash_file(str(helper)):
                raise ValueError('CPU admission must bind accepted idle_runtime SHA')
    preflight.validate_preflight = validate
    preflight._idle_window_spec = True
