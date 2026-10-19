"""The temporary compact recovery hold affects only the two approved VMs."""

import copy
import sys
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from gitops_renderer import render_gitops
from resolver import resolve, validate


class MaintenanceVmHoldTests(unittest.TestCase):
    def test_only_approved_vm_run_strategies_change(self):
        platform = resolve(ROOT)
        cluster = next(c for c in platform.clusters if c.name == "example-management")
        self.assertEqual(validate(platform), [])
        held = render_gitops(cluster, platform.fabric.fabric_asn, platform.defaults)
        unheld_cluster = copy.deepcopy(cluster)
        unheld_cluster.maintenance_vm_holds = []
        unheld = render_gitops(unheld_cluster, platform.fabric.fabric_asn, platform.defaults)

        self.assertEqual(cluster.maintenance_vm_holds, [])
        self.assertEqual(held, unheld)
        self.assertFalse(any("workload-mutator" in path for path in held))


if __name__ == "__main__":
    unittest.main()
