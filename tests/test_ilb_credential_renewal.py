"""Focused, offline checks for the staged example-tenant-a ILB credential renewer."""

import base64
import importlib.util
import json
from pathlib import Path
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "data/ilb/example-tenant-poc/rotate_remote_credential.py"
spec = importlib.util.spec_from_file_location("ilb_credential_renewal", SOURCE)
renewal = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renewal)


def token(expiry):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": expiry}).encode()).decode().rstrip("=")
    return f"header.{payload}.signature"


class RenewalTests(unittest.TestCase):
    NOW = 1_800_000_000

    def test_scoped_server_uses_host_network_resolvable_hcp_route(self):
        self.assertEqual(renewal.HOSTED_SERVER, "https://api.example-tenant-a.example.com:443")

    def fake_oc(self, expiry=None, fail_step=None):
        calls = []
        ca = base64.b64encode(b"test-ca").decode()
        config = {"current-context": "admin", "contexts": [{"name": "admin",
                  "context": {"cluster": "example-tenant-a"}}], "clusters": [{"name": "example-tenant-a",
                  "cluster": {"certificate-authority-data": ca}}]}
        existing = {"apiVersion": "v1", "kind": "Secret", "metadata": {
            "namespace": renewal.TARGET_NAMESPACE, "name": renewal.TARGET_SECRET,
            "resourceVersion": "17"}, "data": {"other": "cHJlc2VydmU="}}

        def oc(step, args, input_bytes=None):
            calls.append((step, args, input_bytes))
            if step == fail_step:
                raise renewal.RenewalError("simulated failure")
            if step == "read named HCP admin Secret":
                return json.dumps({"data": {"kubeconfig":
                    base64.b64encode(b"admin-kubeconfig").decode()}}).encode()
            if step == "parse HCP admin kubeconfig":
                return json.dumps(config).encode()
            if step == "hosted TokenRequest":
                return token(expiry or self.NOW + 3600).encode()
            if step == "verify scoped API identity and TLS":
                return renewal.EXPECTED_IDENTITY.encode()
            if step == "verify hosted fixture access":
                return b"service/echo"
            if step == "read named target Secret":
                return json.dumps(existing).encode()
            if step == "replace named target Secret":
                return b""
            raise AssertionError(step)

        return oc, calls

    def test_rotation_updates_only_scoped_credential_and_preserves_secret(self):
        oc, calls = self.fake_oc()
        self.assertEqual(renewal.rotate(oc, self.NOW), "2027-01-15T09:00:00Z")
        replacement = next(json.loads(data) for step, _, data in calls
                           if step == "replace named target Secret")
        self.assertEqual(replacement["metadata"]["resourceVersion"], "17")
        self.assertEqual(replacement["data"]["other"], "cHJlc2VydmU=")
        scoped = json.loads(base64.b64decode(replacement["data"]["kubeconfig"]))
        self.assertEqual(scoped["clusters"][0]["cluster"]["server"], renewal.HOSTED_SERVER)
        self.assertIn("certificate-authority-data", scoped["clusters"][0]["cluster"])
        self.assertEqual(set(scoped["users"][0]["user"]), {"token"})
        self.assertNotIn("client-certificate-data", str(scoped))
        self.assertIn("token-expires-at", str(replacement["metadata"]["annotations"]))

    def test_short_token_fails_without_replacing_secret(self):
        oc, calls = self.fake_oc(expiry=self.NOW + 1200)
        with self.assertRaises(renewal.RenewalError):
            renewal.rotate(oc, self.NOW)
        self.assertFalse(any(step == "replace named target Secret" for step, _, _ in calls))

    def test_token_request_failure_preserves_secret(self):
        oc, calls = self.fake_oc(fail_step="hosted TokenRequest")
        with self.assertRaises(renewal.RenewalError):
            renewal.rotate(oc, self.NOW)
        self.assertFalse(any(step == "replace named target Secret" for step, _, _ in calls))

    def test_admin_ca_must_be_verified(self):
        with self.assertRaises(renewal.RenewalError):
            renewal.hosted_ca({"current-context": "admin", "contexts": [{"name": "admin",
                "context": {"cluster": "example-tenant-a"}}], "clusters": [{"name": "example-tenant-a",
                "cluster": {"insecure-skip-tls-verify": True,
                            "certificate-authority-data": "Y2E="}}]})


if __name__ == "__main__":
    unittest.main()
