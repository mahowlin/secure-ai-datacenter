"""Prove the effective selected lifecycle replica owner used in the walkthrough."""
from pathlib import Path
import sys
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolver import resolve, validate
from gitops_renderer.hosted_clusters import _render_hosted_clusters
from gitops_renderer.selected_inputs import selected


class WorkerReplicaPrecedence(unittest.TestCase):
    def test_parent_workers_override_wins_until_changed_explicitly(self):
        platform = resolve(ROOT)
        cluster = next(item for item in platform.clusters if item.hosted_clusters)
        tenant = cluster.hosted_clusters[0]
        override = cluster.hosted_cluster_lifecycle["workerReplicasByCluster"][tenant.name]
        tenant.replicas = 3
        relative = f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{cluster.name}/{tenant.name}/nodepool-workers.yaml"
        with selected(platform.defaults):
            files = _render_hosted_clusters(cluster, platform.defaults)
        self.assertEqual(yaml.safe_load(files[relative])["spec"]["replicas"], override)
        cluster.hosted_cluster_lifecycle["workerReplicasByCluster"][tenant.name] = 3
        self.assertTrue(any("rollout policy" in error for error in validate(platform)))
        tenant.worker_replica_checkpoints = (0, 1, 3, 5)
        self.assertEqual(validate(platform), [])
        with selected(platform.defaults):
            files = _render_hosted_clusters(cluster, platform.defaults)
        self.assertEqual(yaml.safe_load(files[relative])["spec"]["replicas"], 3)


if __name__ == "__main__":
    unittest.main()
