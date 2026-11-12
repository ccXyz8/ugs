"""Accept explicit old/new stage verifier contracts without overwriting old PINs."""
import copy
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def install(preflight):
    if getattr(preflight, '_idle_preflight_v2', False): return
    original = preflight.validate_preflight
    fields = ('VERIFIER', 'VERIFIER_SHA', 'VERIFIER_SPEC')
    old = {k: copy.deepcopy(getattr(preflight, k)) for k in fields}
    path = str(HERE / 'verify_launch_idle_v2.py')
    new = {'VERIFIER': path, 'VERIFIER_SHA': preflight.core.hash_file(path)}
    new['VERIFIER_SPEC'] = {**old['VERIFIER_SPEC'], 'path': path, 'sha256': new['VERIFIER_SHA']}
    def validate(manifest, project, from_index=0):
        if manifest.get('project_id') not in preflight.PROJECTS: return original(manifest, project, from_index)
        for stage in manifest['stages'][from_index:]:
            pin = manifest['admission']['stages'][stage['stage_id']]['cpu']
            report = json.loads(Path(pin['path']).read_text())
            spec = report.get('preflight', {}).get('verifier')
            selected = new if spec == new['VERIFIER_SPEC'] else old if spec == old['VERIFIER_SPEC'] else None
            if selected is None: raise ValueError('Unknown verifier specification; use accepted idle v1 or v2 PINs')
            if selected is new:
                helper = str(HERE / 'idle_runtime_v2.py')
                if report.get('source_sha256', {}).get(helper) != preflight.core.hash_file(helper):
                    raise ValueError('CPU admission must bind accepted idle_runtime_v2 SHA')
            # Preserve all existing CPU evidence, source/hash, memory-helper and
            # old-helper checks. New reports retain the immutable old helper PIN.
            one = {**manifest, 'stages': [stage]}
            saved = {k: getattr(preflight, k) for k in fields}
            try:
                for k, value in selected.items(): setattr(preflight, k, value)
                original(one, project, 0)
            finally:
                for k, value in saved.items(): setattr(preflight, k, value)
    preflight.validate_preflight = validate
    preflight._idle_preflight_v2 = True
