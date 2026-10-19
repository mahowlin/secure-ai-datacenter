"""Declared hardware changes drive the existing renderer; secrets remain runtime inputs."""
from __future__ import annotations
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from resolver import resolve, validate
from gitops_renderer.orchestrator import render_gitops
from platform_contract.models import SupportStatus, ValidationStatus

class DeclaredBaremetalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = resolve(ROOT)
        cls.cluster = next(c for c in cls.platform.clusters if c.name == 'example-third-party')
        module = importlib.util.spec_from_file_location('render_ocp_declared', ROOT / 'scripts/render-ocp.py')
        cls.renderer = importlib.util.module_from_spec(module)
        module.loader.exec_module(cls.renderer)

    def test_generic_compact_is_not_ucs_or_hcp_day2(self):
        self.assertEqual(validate(self.platform), [])
        self.assertIsNone(self.cluster.ucs_host_networking)
        self.assertEqual(self.cluster.hosted_clusters, [])
        self.assertEqual(render_gitops(self.cluster, self.platform.fabric.fabric_asn, self.platform.defaults), {})
        intent = next(c for c in self.platform.profile_intent.management_clusters if c.name == self.cluster.name)
        self.assertEqual(intent.role.value, 'standalone')
        self.assertEqual(intent.mode.value, 'compact')
        self.assertEqual(intent.releases.support, SupportStatus.UNKNOWN)
        self.assertEqual(intent.releases.validation, ValidationStatus.CANDIDATE)

    def test_declared_hardware_and_nonfirst_rendezvous(self):
        files = self.renderer.render_ocp_install(self.cluster, self.platform.defaults, root=ROOT)
        agent = yaml.safe_load(files['agent-config.yaml'])
        install = yaml.safe_load(files['install-config.yaml'])
        cilium = yaml.safe_load(files['manifests/ciliumconfig.yaml'])
        network = yaml.safe_load(files['manifests/cluster-network-02-config-local.yml'])
        self.assertEqual(agent['rendezvousIP'], self.cluster.nodes[1].ip)
        self.assertEqual(len(agent['hosts']), 3)
        self.assertNotIn('PLACEHOLDER', files['agent-config.yaml'])
        self.assertEqual(install['controlPlane']['replicas'], 3)
        self.assertEqual(install['compute'][0]['replicas'], 0)
        for node, host in zip(self.cluster.nodes, agent['hosts']):
            self.assertEqual(host['interfaces'], node.interfaces)
            self.assertEqual(host['rootDeviceHints'], node.root_device_hints)
            bond = host['networkConfig']['interfaces'][0]
            self.assertEqual(bond['link-aggregation']['mode'], '802.3ad')
            self.assertNotIn('primary', bond['link-aggregation']['options'])
        self.assertEqual(network['spec']['clusterNetwork'], install['networking']['clusterNetwork'])
        self.assertEqual(cilium['spec']['ipam']['operator']['clusterPoolIPv4PodCIDRList'], [n['cidr'] for n in install['networking']['clusterNetwork']])
        self.assertEqual(cilium['spec']['k8sServiceHost'], 'api-int.example-third-party.example.com')
        self.assertNotIn('enterprise', cilium['spec'])

    def test_changed_input_changes_output_without_renderer_edits(self):
        cluster = copy.deepcopy(self.cluster)
        cluster.nodes[0].interfaces[0]['macAddress'] = '02:00:5e:00:53:40'
        cluster.nodes[0].root_device_hints = {'deviceName': '/dev/disk/by-id/example-boot'}
        cluster.management_interface.mode = 'active-backup'
        cluster.management_interface.options = {'miimon': '200', 'primary': 'eno2'}
        files = self.renderer.render_ocp_install(cluster, self.platform.defaults, root=ROOT)
        host = yaml.safe_load(files['agent-config.yaml'])['hosts'][0]
        self.assertEqual(host['interfaces'][0]['macAddress'], '02:00:5e:00:53:40')
        self.assertEqual(host['rootDeviceHints']['deviceName'], '/dev/disk/by-id/example-boot')
        self.assertEqual(host['networkConfig']['interfaces'][0]['link-aggregation'], {'mode': 'active-backup', 'options': {'miimon': '200', 'primary': 'eno2'}, 'port': ['eno1', 'eno2']})

    def test_invalid_hardware_fails_instead_of_selecting_ucs_defaults(self):
        for change in ['mac', 'disk', 'mode', 'rendezvous']:
            with self.subTest(change=change):
                platform = copy.deepcopy(self.platform)
                cluster = next(c for c in platform.clusters if c.name == self.cluster.name)
                if change == 'mac': cluster.nodes[1].interfaces[0] = copy.deepcopy(cluster.nodes[0].interfaces[0])
                if change == 'disk': cluster.nodes[0].root_device_hints = {}
                if change == 'mode': cluster.management_interface.mode = ''
                if change == 'rendezvous': cluster.rendezvous_node = 'missing'
                self.assertTrue(validate(platform))

    def test_private_runtime_inputs_are_not_printable_or_committable(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            pull = d / 'pull.json'; pull.write_text(json.dumps({'auths': {'registry.example.com:5000': {'auth': 'synthetic-test-value'}}}))
            ssh = d / 'id.pub'; ssh.write_text('ssh-ed25519 synthetic-test-public-key')
            ca = d / 'ca.pem'; ca.write_text('-----BEGIN CERTIFICATE-----\nsynthetic-test-ca\n-----END CERTIFICATE-----\n')
            flags = ['--executable', '--cluster', self.cluster.name, '--pull-secret-file', str(pull), '--ssh-key-file', str(ssh), '--registry-ca-file', str(ca)]
            cmd = [sys.executable, str(ROOT / 'scripts/render-ocp.py')]
            for extra in [['--dry-run', '--output', str(d / 'out')], ['--output', str(ROOT / 'generated/private-test')]]:
                result = subprocess.run(cmd + flags + extra, capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('synthetic-test-value', result.stdout + result.stderr)
            result = subprocess.run(cmd + flags + ['--output', str(d / 'out')], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('synthetic-test-value', result.stdout + result.stderr)
            install = (d / 'out/install-config.yaml')
            config = yaml.safe_load(install.read_text())
            self.assertEqual(json.loads(config['pullSecret']), json.loads(pull.read_text()))
            self.assertNotIn('PLACEHOLDER', install.read_text())
            self.assertEqual(install.stat().st_mode & 0o777, 0o600)

    def test_public_trust_does_not_require_or_emit_additional_ca(self):
        with tempfile.TemporaryDirectory() as directory:
            d = Path(directory)
            input_root = d / "source"
            shutil.copytree(ROOT, input_root, ignore=shutil.ignore_patterns(".git", "generated", "__pycache__"))
            path = input_root / "data/clusters/example-third-party/cluster.yaml"
            declared = yaml.safe_load(path.read_text())
            declared["spec"]["installConfig"]["openshift"]["registryTrustPolicy"] = "public"
            path.write_text(yaml.safe_dump(declared, sort_keys=False))
            pull = d / "pull.json"
            pull.write_text(json.dumps({"auths": {"containers.isovalent.com": {"auth": "synthetic-public-trust-test"}}}))
            ssh = d / "id.pub"; ssh.write_text("ssh-ed25519 synthetic-test-public-key")
            result = subprocess.run([sys.executable, str(ROOT / "scripts/render-ocp.py"), "--root", str(input_root), "--cluster", "example-third-party", "--executable", "--output", str(d / "private-output"), "--pull-secret-file", str(pull), "--ssh-key-file", str(ssh)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = yaml.safe_load((d / "private-output/install-config.yaml").read_text())
            self.assertNotIn("additionalTrustBundle", config)
            self.assertNotIn("additionalTrustBundlePolicy", config)
            self.assertNotIn("synthetic-public-trust-test", result.stdout + result.stderr)

if __name__ == '__main__': unittest.main()
