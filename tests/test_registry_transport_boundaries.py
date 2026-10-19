# Copyright (c) 2026 Cisco and/or its affiliates.
# Licensed under the Cisco Sample Code License, Version 1.1. See LICENSE.
"""Registry trust/auth and exact manual ILB command regressions; offline only."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = load('verify-portworx-csi-tag')
TRANSPORT = load('transport-generated-artifacts')
DISKS = load('enrich-boot-disk')
from gitops_renderer.ilb_install import _t2_script


class RegistryTransportBoundaries(unittest.TestCase):
    def test_registry_uses_private_auth_and_normal_tls_and_keeps_errors_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            auth = root / 'auth.json'
            auth.write_text('{"auths":{}}')
            auth.chmod(0o600)
            certs = root / 'certs'
            certs.mkdir()
            response = subprocess.CompletedProcess(['synthetic-skopeo'], 0, b'manifest', b'')
            with patch.object(VERIFY.subprocess, 'run', return_value=response) as command:
                self.assertEqual(VERIFY.fetch_manifest('registry.example.test/team/image:v1', auth, certs), b'manifest')
                args = command.call_args.args[0]
                self.assertIn('--tls-verify=true', args)
                self.assertEqual(args[args.index('--authfile') + 1], str(auth))
                self.assertEqual(args[args.index('--cert-dir') + 1], str(certs))
            auth.chmod(0o644)
            with patch.object(VERIFY.subprocess, 'run') as command, self.assertRaises(ValueError):
                VERIFY.fetch_manifest('registry.example.test/team/image:v1', auth, certs)
            command.assert_not_called()
            with patch.object(VERIFY.subprocess, 'run', return_value=subprocess.CompletedProcess(
                    ['synthetic-skopeo'], 1, b'', b'private server response')):
                with self.assertRaisesRegex(ValueError, '^authenticated TLS registry manifest lookup failed$'):
                    VERIFY.fetch_manifest('registry.example.test/team/image:v1', None, None)

    def test_git_token_is_only_process_environment_not_origin_or_argv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            name = next(iter(TRANSPORT.TARGET_REPOS))
            subdir = TRANSPORT.REPO_SOURCE_SUBDIRS.get(name, 'data')
            (root / 'source' / subdir).mkdir(parents=True)
            calls = []
            def run(args, cwd=None, env=None):
                calls.append((args, env))
                return ''
            with patch.object(TRANSPORT, 'run', side_effect=run), patch.object(
                    TRANSPORT, 'reconcile_generated_files', return_value=['synthetic.yaml']), patch.object(
                    TRANSPORT, 'validate_openshift_drift'):
                TRANSPORT.sync_repo(name, root / 'source', root / 'manifest', 'review',
                                    root / 'work', 'synthetic-token', 'review only', None)
            clone, env = calls[0]
            self.assertNotIn('synthetic-token', ' '.join(clone))
            self.assertTrue(any(arg.startswith('https://github.com/') for arg in clone))
            self.assertTrue(any(key.startswith('GIT_CONFIG_VALUE_') and 'AUTHORIZATION: basic ' in value
                                for key, value in env.items()))

    def test_rendered_t2_label_arguments_are_accepted_by_actual_local_oc(self):
        script = _t2_script(['router-a', 'router-b'], 'backend', 2)
        line = next(line for line in script.splitlines() if line.startswith('oc label node'))
        self.assertNotIn('=t1-', line)
        args = line.split()[4:]
        node = '{"apiVersion":"v1","kind":"Node","metadata":{"name":"backend","labels":{"service.cilium.io/node":"t1-t2","node-role.kubernetes.io/ingress":"true"}}}'
        result = subprocess.run(['oc', 'label', '--local', '-f', '-', *args, '-o', 'json'],
                                input=node, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('service.cilium.io/node', __import__('json').loads(result.stdout)['metadata'].get('labels', {}))

    def test_optional_disk_enrichment_uses_host_identity_not_placeholder_order(self):
        document = {'hosts': [
            {'hostname': 'b.example.test', 'rootDeviceHints': {'model': DISKS.PLACEHOLDER}},
            {'hostname': 'a.example.test', 'rootDeviceHints': {'model': DISKS.PLACEHOLDER}}]}
        with patch.object(DISKS, 'determine_boot_model', side_effect=lambda tool, serial: serial + '_observed'):
            result = DISKS.enrich(document, {'a': 'A', 'b': 'B'}, 'synthetic-isctl')
        self.assertEqual(result['hosts'][0]['rootDeviceHints']['model'], 'B_observed')
        with self.assertRaisesRegex(ValueError, 'hostname/serial'):
            DISKS.enrich({'hosts': [{'hostname': 'missing', 'rootDeviceHints': {'model': DISKS.PLACEHOLDER}}]},
                         {'a': 'A'}, 'synthetic-isctl')

    def test_disk_count_never_invents_a_virtual_drive_or_model(self):
        with patch.object(DISKS, 'query', side_effect=[[], [{'Model': 'M.2'}, {'Model': 'M.2'}]]):
            with self.assertRaisesRegex(ValueError, 'cannot infer RAID'):
                DISKS.determine_boot_model('synthetic-isctl', 'A')
        with patch.object(DISKS, 'query', return_value=[{'Model': 'Observed VD Model'}]):
            self.assertEqual(DISKS.determine_boot_model('synthetic-isctl', 'A'), 'Observed_VD_Model')
        with patch.object(DISKS.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['synthetic'], 0, '[{"Dn":"OTHER/Storage/MSTOR-RAID","Model":"wrong"}]', '')):
            with self.assertRaisesRegex(ValueError, 'declared server'):
                DISKS.query('synthetic-isctl', 'virtualdrive', 'A')


if __name__ == '__main__':
    unittest.main()
