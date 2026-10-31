"""Shared manifest identity validation without changing frozen stage contracts."""
import ugs_exec_v3_1 as core


def validate_submission_version(manifest):
    if not isinstance(manifest, dict):
        raise ValueError("Experiment manifest must be a JSON object")
    value = manifest.get("submission_version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError('submission_version must be a non-empty string; use "1", not 1')


def install():
    if getattr(core, "_ugs_manifest_validation_v1", False):
        return
    original_validate = core.job_validate
    original_key = core.key_for

    def job_validate(manifest, project, from_index=0):
        validate_submission_version(manifest)
        return original_validate(manifest, project, from_index)

    def key_for(manifest):
        # submit/update look up idempotency before full manifest validation.
        validate_submission_version(manifest)
        return original_key(manifest)

    core.job_validate = job_validate
    core.key_for = key_for
    core._ugs_manifest_validation_v1 = True
