"""Optional add-on capabilities stay absent until their switches and pins are declared."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gitops_renderer import render_gitops
from gitops_renderer.addons import addon_errors
from resolver import resolve, validate


class AddonCapabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = resolve(REPO_ROOT)

    def cluster(self, name):
        return copy.deepcopy(next(item for item in self.platform.clusters if item.name == name))

    def test_disabled_switches_add_no_files(self):
        for name in ("example-sno", "example-management"):
            cluster = self.cluster(name)
            self.assertTrue(cluster.gitops_addons)
            self.assertFalse(any(cluster.gitops_addons.values()))
            files = render_gitops(cluster, self.platform.fabric.fabric_asn, self.platform.defaults)
            self.assertFalse(any("/addons/" in path for path in files))

    def test_enabled_addons_render_declared_applications(self):
        cluster = self.cluster("example-sno")
        cluster.network_type = "Cilium"
        cluster.gitops_addons = {
            "nvidia": True,
            "splunk": True,
            "demoApps": True,
            "saifDemo": True,
        }
        serial = cluster.nodes[0].serial
        tag = "v0.6.18"
        cluster.gitops_addon_selections = {
            "nvidia": {"model": "deepseek-r1-14b"},
            "demoApps": {
                "repository": "ghcr.io/open-webui/open-webui",
                "tag": tag,
            },
            "splunk": {"bladeSerial": serial},
        }
        self.assertEqual(addon_errors(cluster, self.platform.defaults), [])
        files = render_gitops(cluster, self.platform.fabric.fabric_asn, self.platform.defaults)
        names = set()
        for path, text in files.items():
            if "/addons/" not in path or path.endswith("kustomization.yaml"):
                continue
            document = yaml.safe_load(text)
            names.add(document["metadata"]["name"])
            self.assertEqual(document["spec"]["source"]["repoURL"], self.platform.defaults.gitops_repo_url)
        self.assertEqual(names, {
            "gpu-operator",
            "nim-operator",
            "nim-llm-deepseek",
            "open-webui",
            "saif-agent",
            "splunk-otel",
            "intersight-otel",
        })
        model = yaml.safe_load(files["clusters/example-sno/addons/nim-llm-deepseek.yaml"])
        self.assertIn("1.1.0", yaml.safe_dump(model))
        webui = yaml.safe_load(files["clusters/example-sno/addons/open-webui.yaml"])
        self.assertIn(tag, yaml.safe_dump(webui))
        splunk = yaml.safe_load(files["clusters/example-sno/addons/splunk-otel.yaml"])
        self.assertIn(f"{cluster.name}.{cluster.base_domain}", yaml.safe_dump(splunk))
        intersight = yaml.safe_load(files["clusters/example-sno/addons/intersight-otel.yaml"])
        self.assertIn(serial, yaml.safe_dump(intersight))
        nim = yaml.safe_load(files["clusters/example-sno/addons/nim-operator.yaml"])
        self.assertEqual(nim["metadata"]["annotations"][
            self.platform.defaults.api_label("chart-version")], "3.0.2")

    def test_missing_pins_fail_closed(self):
        cluster = self.cluster("example-sno")
        cluster.gitops_addons = {"nvidia": True, "demoApps": True, "splunk": True}
        cluster.gitops_addon_selections = {"demoApps": {"repository": "example.invalid/open-webui", "tag": "main"}}
        errors = addon_errors(cluster, self.platform.defaults)
        self.assertTrue(any("one declared model" in item for item in errors))
        self.assertTrue(any("immutable pin" in item for item in errors))
        self.assertTrue(any("bladeSerial" in item for item in errors))
        with self.assertRaisesRegex(ValueError, "one declared model"):
            render_gitops(cluster, self.platform.fabric.fabric_asn, self.platform.defaults)

    def test_committed_inputs_still_validate(self):
        errors = validate(self.platform)
        self.assertFalse(any("addon" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
