"""Unused peering declarations must not create dangling node override references."""
from pathlib import Path
import sys
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from resolver import resolve
from gitops_renderer.management import _render_prerequisites_bgp, _render_prerequisites_kustomizations
from gitops_renderer.selected_inputs import selected

class BGPResources(unittest.TestCase):
    def test_node_override_references_follow_consumed_networks(self):
        p=resolve(ROOT)
        c=next(c for c in p.clusters if c.name=='example-management')
        with selected(p.defaults):
            files={**_render_prerequisites_bgp(c,p.fabric.fabric_asn,p.defaults),**_render_prerequisites_kustomizations(c,p.defaults)}
        k=f'apps/hcp/management/parent-network/overlays/{c.name}/cilium-bgp/kustomization.yaml'
        resources=yaml.safe_load(files[k])['resources']
        for name in resources:
            if name.startswith('node-config-override-'):
                self.assertIn(str(Path(k).parent/name),files)
        # The fixture has one unused nodeConfigOverride declaration; it is not selected.
        self.assertFalse(any(n.startswith('node-config-override-') for n in resources))
