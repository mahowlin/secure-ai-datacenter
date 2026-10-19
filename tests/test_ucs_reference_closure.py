"""Check canonical UCS render joins against the selected Compute defaults."""
import importlib.util
import os
from pathlib import Path
import sys
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolver import resolve
spec = importlib.util.spec_from_file_location("ucs_reference_render", ROOT / "scripts/render-ucs.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)

class UcsReferenceClosure(unittest.TestCase):
    def test_generated_policies_and_active_hardware_profiles_join(self):
        path = os.environ.get("COMPUTE_ROOT")
        if not path:
            self.skipTest("COMPUTE_ROOT must name the selected Compute source candidate")
        compute = Path(path)
        platform = resolve(ROOT)
        files = renderer.render_ucs(platform.clusters, platform.ucs_defaults, root=ROOT,
                                    template_root_name=platform.defaults.identity["templateRoots"]["clusterInstall"])
        documents = [yaml.safe_load(content) for content in files.values()]
        documents.extend(yaml.safe_load(p.read_text()) for p in (compute / "ucs/defaults").rglob("*.ezi.yaml")
                         if "inactive" not in p.parts)
        organizations = {}
        for doc in documents:
            for organization, content in (doc or {}).items():
                if not isinstance(content, dict):
                    continue
                destination = organizations.setdefault(organization, {})
                for section in ("policies", "pools", "profiles"):
                    for kind, objects in content.get(section, {}).items():
                        destination.setdefault((section, kind), []).extend(objects)
        for cluster in platform.clusters:
            if not cluster.ucs_host_networking:
                continue
            model = organizations[platform.ucs_defaults.org]
            policies = {kind: {item["name"]: item for item in entries if "name" in item}
                        for (section, kind), entries in model.items() if section == "policies"}
            pools = {item["name"] for (section, _), entries in model.items() if section == "pools"
                     for item in entries if "name" in item}
            profiles = {item["name"]: item for item in model[("profiles", "server")]}
            for node in cluster.nodes:
                profile = profiles[node.ucs_profile]
                self.assertEqual(profile["targets"][0]["serial_number"], node.serial)
                name = profile["lan_connectivity_policy"]
                candidates = [item for item in model[("policies", "lan_connectivity")] if item["name"] == name]
                self.assertEqual(len(candidates), 1, name)
                for vnic in candidates[0]["vnics"]:
                    self.assertTrue(set(vnic["mac_address_pools"]) <= pools)
                    for field, kind in [("ethernet_adapter_policy", "ethernet_adapter"), ("ethernet_qos_policy", "ethernet_qos"), ("ethernet_network_control_policy", "ethernet_network_control")]:
                        self.assertIn(vnic[field], policies[kind])
                    for group in vnic["ethernet_network_group_policies"]:
                        self.assertIn(group, policies["ethernet_network_group"])
            for domain in model[("profiles", "domain")]:
                self.assertTrue(set(domain["vlan_policies"]) <= set(policies["vlan"]))
            for vlan_policy in policies["vlan"].values():
                for vlan in vlan_policy["vlans"]:
                    self.assertIn(vlan["multicast_policy"], policies["multicast"])

if __name__ == "__main__":
    unittest.main()
