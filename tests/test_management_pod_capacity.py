"""Management pod density stays input-driven and requires ordered manual activation."""

import copy
import sys
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gitops_renderer.applications import _render_cluster_entrypoints
from gitops_renderer.management import _render_prerequisites_operators
from gitops_renderer.selected_inputs import selected
from resolver import resolve, validate


class ManagementPodCapacityTests(unittest.TestCase):
    def setUp(self):
        self.platform = resolve(ROOT)
        self.cluster = next(cluster for cluster in self.platform.clusters
                            if cluster.hosted_cluster_lifecycle.get("managementPodCapacity"))
        self.capacity = self.cluster.hosted_cluster_lifecycle["managementPodCapacity"]

    def test_rendered_policy_and_capacity_are_detached_until_manual_sync(self):
        files = _render_prerequisites_operators(self.cluster, self.platform.defaults)
        base = f"apps/hcp/management/operators/overlays/{self.cluster.name}"
        parent = yaml.safe_load(files[f"{base}/kustomization.yaml"])
        self.assertNotIn("pod-density", parent["resources"])
        self.assertNotIn("pod-density-policy", parent["resources"])

        kubelet = yaml.safe_load(files[f"{base}/pod-density/kubeletconfig.yaml"])
        self.assertEqual(kubelet["metadata"]["annotations"], {
            "machineconfiguration.openshift.io/mc-name-suffix": "",
        })
        self.assertEqual(kubelet["spec"]["kubeletConfig"], {
            "maxPods": self.capacity["maxPods"],
            "podsPerCore": self.capacity["podsPerCore"],
        })
        self.assertEqual(kubelet["spec"]["machineConfigPoolSelector"]["matchLabels"], {
            f"pools.operator.machineconfiguration.openshift.io/{self.capacity['machineConfigPool']}": "",
        })
        policy = yaml.safe_load(files[f"{base}/pod-density-policy/machineconfiguration.yaml"])
        self.assertEqual(policy["metadata"]["name"], "cluster")
        self.assertEqual(policy["spec"], {"nodeDisruptionPolicy": {"files": [{
            "path": "/etc/kubernetes/kubelet.conf",
            "actions": [{"type": "Restart", "restart": {"serviceName": "kubelet.service"}}],
        }]}})
        self.assertEqual(policy["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
                         "ServerSideApply=true,Prune=false,Delete=false")

        with selected(self.platform.defaults):
            apps = _render_cluster_entrypoints(
                self.cluster, "https://example.invalid/gitops.git", "main", self.platform.defaults)
        for name in ("hcp-pod-density-policy", "hcp-pod-density"):
            app = yaml.safe_load(apps[f"clusters/{self.cluster.name}/{name}.yaml"])
            self.assertNotIn("automated", app["spec"]["syncPolicy"])
            self.assertEqual(app["spec"]["source"]["targetRevision"], "main")
            self.assertIn(f"{name}.yaml", yaml.safe_load(
                apps[f"clusters/{self.cluster.name}/kustomization.yaml"])["resources"])

    def test_changed_inputs_change_only_declared_capacity_and_policy(self):
        changed = copy.deepcopy(self.cluster)
        changed.hosted_cluster_lifecycle["managementPodCapacity"] = {
            "machineConfigPool": "worker", "maxPods": 420, "podsPerCore": 0,
            "restartPolicy": self.capacity["restartPolicy"],
        }
        files = _render_prerequisites_operators(changed, self.platform.defaults)
        base = f"apps/hcp/management/operators/overlays/{self.cluster.name}"
        kubelet = yaml.safe_load(files[f"{base}/pod-density/kubeletconfig.yaml"])
        self.assertEqual(kubelet["spec"]["kubeletConfig"]["maxPods"], 420)
        self.assertEqual(kubelet["spec"]["machineConfigPoolSelector"]["matchLabels"], {
            "pools.operator.machineconfiguration.openshift.io/worker": "",
        })
        policy = yaml.safe_load(files[f"{base}/pod-density-policy/machineconfiguration.yaml"])
        self.assertEqual(policy["spec"]["nodeDisruptionPolicy"]["files"][0]["path"],
                         self.capacity["restartPolicy"]["path"])

    def test_invalid_policy_or_limit_does_not_validate(self):
        for field, value in (("maxPods", True), ("restartPolicy", {"path": "/tmp/other"})):
            original = self.capacity[field]
            try:
                self.capacity[field] = value
                self.assertTrue(any("managementPodCapacity" in error
                                    for error in validate(self.platform)))
            finally:
                self.capacity[field] = original


if __name__ == "__main__":
    unittest.main()
