import unittest
import verify_launch_v1 as v
class DenialTests(unittest.TestCase):
    def state(self):
        return {"revision": 1, "daemon": {"version": "ugs-cli-v1"},
                "policy": {"dispatch_enabled": False, "migration_cleared": False},
                "active": None}
    def test_no_env(self):
        self.assertFalse(v.diagnose(self.state(), {})["allowed"])
    def test_forged_env(self):
        self.assertFalse(v.diagnose(self.state(), {"UGS_MANAGED":"1",
            "UGS_RUN_ID":"fake", "UGS_STAGE_ID":"fake", "UGS_STAGE_NONCE":"fake"})["allowed"])
    def test_matching_identity_not_authorization(self):
        s=self.state()
        s["active"]={"run_id":"r","stage_id":"s","stage_nonce":"n"}
        d=v.diagnose(s, {"UGS_RUN_ID":"r","UGS_STAGE_ID":"s","UGS_STAGE_NONCE":"n"})
        self.assertTrue(d["identity_fields_match"])
        self.assertFalse(d["allowed"])
    def test_flags_not_authorization(self):
        s=self.state()
        s["policy"]={"dispatch_enabled":True,"migration_cleared":True}
        self.assertFalse(v.diagnose(s, {})["allowed"])
    def test_version_not_authorization(self):
        s=self.state();s["daemon"]["version"]="ugs-exec-v2"
        self.assertFalse(v.diagnose(s, {})["allowed"])
if __name__ == "__main__":
    unittest.main()
