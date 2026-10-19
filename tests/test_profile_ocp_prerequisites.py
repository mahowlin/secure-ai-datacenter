from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from resolver import resolve


def load_render_ocp_module():
    spec = importlib.util.spec_from_file_location("render_ocp", SCRIPTS / "render-ocp.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class OCPPrerequisiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.platform = resolve(REPO_ROOT)
        cls.renderer = load_render_ocp_module()

    def cluster(self, name: str):
        return next(cluster for cluster in self.platform.clusters if cluster.name == name)

    def test_compact_cilium_kpr_renders_two_sided_contract(self) -> None:
        cluster = self.cluster("example-management")
        files = self.renderer.render_ocp_install(cluster, self.platform.defaults, root=REPO_ROOT)

        cilium = yaml.safe_load(files["manifests/ciliumconfig.yaml"])
        network = yaml.safe_load(files["manifests/cluster-network-02-config-local.yml"])
        install = yaml.safe_load(files["install-config.yaml"])

        self.assertIs(cilium["spec"]["kubeProxyReplacement"], True)
        self.assertIs(cilium["spec"]["socketLB"]["hostNamespaceOnly"], True)
        self.assertEqual(
            cilium["spec"]["ipam"]["operator"]["clusterPoolIPv4MaskSize"],
            install["networking"]["clusterNetwork"][0]["hostPrefix"],
        )
        self.assertEqual(network["apiVersion"], "operator.openshift.io/v1")
        self.assertEqual(network["kind"], "Network")
        self.assertEqual(network["metadata"]["name"], "cluster")
        self.assertIs(network["spec"]["deployKubeProxy"], False)
        self.assertEqual(network["spec"]["networkType"], "Cilium")
        self.assertEqual(network["spec"]["clusterNetwork"], install["networking"]["clusterNetwork"])
        self.assertEqual(network["spec"]["serviceNetwork"], install["networking"]["serviceNetwork"])

    def test_sno_ovn_day1_does_not_render_cilium_kpr_manifests(self) -> None:
        cluster = self.cluster("example-sno")
        files = self.renderer.render_ocp_install(cluster, self.platform.defaults, root=REPO_ROOT)

        self.assertEqual(yaml.safe_load(files["install-config.yaml"])["networking"]["networkType"], "OVNKubernetes")
        self.assertNotIn("manifests/ciliumconfig.yaml", files)
        self.assertNotIn("manifests/cluster-network-02-config-local.yml", files)


if __name__ == "__main__":
    unittest.main()
