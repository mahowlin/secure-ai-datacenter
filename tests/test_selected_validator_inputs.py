"""The selected input root and interface derivation govern validation."""
import contextlib
import hashlib
import shutil
import tempfile
import importlib.util
from pathlib import Path
import sys
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolver import resolve
from gitops_renderer.applications import _render_cluster_entrypoints
spec = importlib.util.spec_from_file_location("portable_validator", ROOT / "scripts/validate-gitops-output.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

class SelectedCiliumInterface(unittest.TestCase):
    def test_declared_bond_rename_and_nonbond_interfaces_are_accepted(self):
        for management in ("bond9", "uplink9"):
            with self.subTest(management=management):
                platform = resolve(ROOT)
                cluster = next(c for c in platform.clusters if c.features.get("hostedControlPlanes"))
                cluster.management_interface.bond = management
                validator._PLATFORM_ROOT = ROOT
                validator._SELECTED_PLATFORM = platform
                if hasattr(validator, "_bind_identity"):
                    validator._bind_identity(ROOT)
                    validator._SELECTED_PLATFORM = platform
                if (ROOT / "scripts/gitops_renderer/selected_inputs.py").exists():
                    from gitops_renderer.selected_inputs import selected
                    context = selected(platform.defaults)
                else:
                    context = contextlib.nullcontext()
                with context:
                    files = _render_cluster_entrypoints(
                        cluster, platform.defaults.gitops_repo_url,
                        platform.defaults.gitops_target_revision, platform.defaults,
                    )
                relative = f"clusters/{cluster.name}/kustomization.yaml"
                document = yaml.safe_load(files[relative])
                self.assertEqual(validator.validate_hcp_cilium_devices(Path(relative), document, relative), [])
                patch = next(p for p in document["patches"] if p["target"].get("name") == "cilium-config")
                operations = yaml.safe_load(patch["patch"])
                for operation in operations:
                    value = operation.get("value")
                    if isinstance(value, dict) and value.get("name") == "l2.device":
                        value["value"] = "wrong-interface"
                patch["patch"] = yaml.safe_dump(operations)
                self.assertTrue(validator.validate_hcp_cilium_devices(Path(relative), document, relative))

    def test_external_root_controls_defaults_profiles_and_public_registry_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            selected_root = Path(temporary)
            shutil.copytree(ROOT / "data", selected_root / "data")
            shutil.copytree(ROOT / "schemas", selected_root / "schemas")
            shutil.copytree(ROOT / ".artifacts", selected_root / ".artifacts")
            shutil.copy2(ROOT / "platform-release.yaml", selected_root / "platform-release.yaml")
            defaults = next(p for p in (selected_root / "data/defaults").glob("*.yaml")
                            if yaml.safe_load(p.read_text()).get("kind") == "PlatformDefaults")
            document = yaml.safe_load(defaults.read_text())
            document["spec"]["gitops"]["workerDhcp"]["serverName"] = "selected-fixture-dhcp"
            document["spec"]["identity"]["publicImageRegistries"] = ["selected.example.invalid/"]
            defaults.write_text(yaml.safe_dump(document, sort_keys=False))
            validator._bind_identity(selected_root)
            profile = selected_root / "data/cilium-config-profiles/fixture.yaml"
            profile.parent.mkdir(exist_ok=True)
            profile.write_text(yaml.safe_dump({"kind": "CiliumConfigProfile", "metadata": {"name": "fixture"}, "spec": {"values": {"fixture": True}}}))
            self.assertEqual(validator._declared_text("spec", "gitops", "workerDhcp", "serverName", label="DHCP name"), "selected-fixture-dhcp")
            self.assertEqual(validator.public_image_refs({"image": "quay.io/test/image"}), [])
            self.assertTrue(validator.public_image_refs({"image": "selected.example.invalid/image"}))
            values, error = validator.load_cilium_config_profile("fixture", hashlib.sha256(profile.read_bytes()).hexdigest())
            self.assertIsNone(error)
            self.assertEqual(values, {"fixture": True})

if __name__ == "__main__":
    unittest.main()
