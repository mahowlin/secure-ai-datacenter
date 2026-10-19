"""Declared identity accepts an example registry and domain and rejects a missing block."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from site_identity import identity_errors, load_identity, registry_url


class SiteIdentityTests(unittest.TestCase):
    def test_missing_identity_fails(self) -> None:
        self.assertIn("spec.identity is required", identity_errors(None))
        self.assertTrue(any("apiGroup" in message for message in identity_errors({})))

    def test_example_registry_and_domain(self) -> None:
        source = (REPO_ROOT / "data" / "defaults" / "example.yaml").read_text()
        source = source.replace("registry.example.com:5000", "registry.example.com:5000")
        source = source.replace("saif.example.com", "saif.example.com")
        source = source.replace("baseDomain: example.com", "baseDomain: example.com")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            defaults = root / "data" / "defaults"
            defaults.mkdir(parents=True)
            (defaults / "example.yaml").write_text(source)
            identity = load_identity(root)
            self.assertEqual(identity["apiGroup"], "saif.example.com")
            self.assertEqual(identity["baseDomain"], "example.com")
            self.assertEqual(identity["chartRepository"], "registry.example.com:5000/charts/clife-install")
            self.assertEqual(registry_url(root), "registry.example.com:5000")


if __name__ == "__main__":
    unittest.main()
