from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from platform_contract import resolve_profile_intent
import resolver as legacy_resolver
from gitops_renderer import render_gitops


class ProfileResolverTests(unittest.TestCase):
    def test_current_tree_uses_strict_legacy_adapter(self) -> None:
        intent = resolve_profile_intent(REPO_ROOT)
        self.assertTrue(intent.legacy_adapter_used)
        codes = {item.code for item in intent.diagnostics}
        self.assertIn("legacy.combined-release-ref", codes)
        self.assertIn("legacy.features-map", codes)
        self.assertIn("legacy.worker-profile-explicit", codes)
        self.assertNotIn("legacy.worker-profile-derived", codes)
        hcp_management = intent.cluster("example-management")
        self.assertIsNotNone(hcp_management)
        self.assertEqual(hcp_management.role.value, "hcp-management")  # type: ignore[union-attr]
        self.assertFalse(hcp_management.deployment_authorized)  # type: ignore[union-attr]
        self.assertEqual(hcp_management.renderer_authorizations, ())  # type: ignore[union-attr]
        profiles = {
            item.name: (item.worker_profile.value, item.worker_profile.origin.field)
            for item in hcp_management.hosted_clusters  # type: ignore[union-attr]
        }
        self.assertEqual(profiles, {
            "example-multus-a": ("multus-l2-dhcp", "spec.workerNetworkProfileRef"),
            "example-shared": ("multus-l2-dhcp", "spec.workerNetworkProfileRef"),
        })

    def test_renderer_consumes_profile_capabilities_and_fails_closed_without_them(self) -> None:
        with_profile = legacy_resolver.resolve(REPO_ROOT)
        with patch.object(legacy_resolver, "resolve_profile_intent", return_value=None):
            without_profile = legacy_resolver.resolve(REPO_ROOT)

        self.assertIsNotNone(with_profile.defaults)
        self.assertIsNotNone(without_profile.defaults)
        self.assertEqual(legacy_resolver.validate(with_profile), [])
        self.assertTrue(any(
            "no resolved capability expansion" in error
            for error in legacy_resolver.validate(without_profile)
        ))
        current_files = render_gitops(
            with_profile.clusters[0],
            with_profile.fabric.fabric_asn,
            with_profile.defaults,  # type: ignore[arg-type]
        )
        self.assertTrue(current_files)


if __name__ == "__main__":
    unittest.main()
