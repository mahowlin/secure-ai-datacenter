"""Declared base inputs use selected identities without private source fallback."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolver import resolve
from gitops_renderer.hcp_base import render_hcp_base


class DeclaredTimescapeBase(unittest.TestCase):
    def test_selected_auth_contract_is_a_declared_deterministic_input(self):
        platform = resolve(ROOT)
        one = render_hcp_base(platform.defaults)
        self.assertEqual(one, render_hcp_base(platform.defaults))
        app = yaml.safe_load(next(iter(one.values())))
        values = yaml.safe_load(app["spec"]["source"]["helm"]["values"])
        server = values["ingester"]["server"]
        self.assertEqual(server["authMethod"], "basic-auth")
        self.assertIs(server["grpc"]["enabled"], False)
        self.assertNotIn("enabled", server["http"]["basicAuth"])

    def test_changed_public_identity_and_registry_drive_output(self):
        defaults = copy.deepcopy(resolve(ROOT).defaults)
        defaults.identity["sourceRepositories"]["gitops"] = "fixture-owner/fixture-source"
        defaults.identity["generatedRepos"]["gitops"] = "fixture-gitops"
        defaults.registry_url = "registry.fixture.invalid:5000"
        text = next(iter(render_hcp_base(defaults).values()))
        self.assertIn("https://github.com/fixture-owner/fixture-gitops.git", text)
        self.assertIn("registry.fixture.invalid:5000", text)
        self.assertNotIn("__IMAGE_REGISTRY__", text)
        self.assertNotIn("registry.example.com:5000", text)

    def test_missing_or_embedded_source_identity_fails(self):
        defaults = copy.deepcopy(resolve(ROOT).defaults)
        with tempfile.TemporaryDirectory() as temporary:
            defaults.input_root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "directory is required"):
                render_hcp_base(defaults)
            base = defaults.input_root / "data/gitops/hcp-base/fixture"
            base.mkdir(parents=True)
            (base / "application.yaml").write_text("kind: Application\nspec:\n  source:\n    repoURL: https://fixed.invalid/repo\n")
            with self.assertRaisesRegex(ValueError, "embed"):
                render_hcp_base(defaults)


if __name__ == "__main__":
    unittest.main()
