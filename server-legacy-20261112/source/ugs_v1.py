"""Legacy entry point; retain lifecycle and new-submission admission checks."""
import ugs_control_v2 as control
control.install()
import ugs_preflight_v1 as preflight
preflight.install()
if __name__ == "__main__":
    control.cli()
