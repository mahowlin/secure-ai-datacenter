"""Offline manifest transport: full payload, stale-owner reconciliation and preservation."""
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'transport_inventory',
    ROOT / 'scripts/transport-generated-artifacts.py')
transport = importlib.util.module_from_spec(spec)
spec.loader.exec_module(transport)

def digest(content):
    return hashlib.sha256(content).hexdigest()

class TransportInventory(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / 'source'
        self.target = self.root / 'target'
        self.source.mkdir()
        self.target.mkdir()
        self.repo = transport.GITOPS_REPO if hasattr(
            transport,
            'GITOPS_REPO') else next(
            (
                k for k in transport.TARGET_REPOS if 'gitops' in k))
        self.manifest = self.root / 'manifest.yaml'

    def inventory(self, files):
        entries = []
        for relative, content in files.items():
            path = self.source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            entries.append({'path': relative, 'sha256': digest(content)})
        self.manifest.write_text(
            yaml.safe_dump(
                {
                    'targets': [
                        {
                            'repo': self.repo,
                            'targetRoot': '.',
                            'files': entries}]}))

    def sync(self):
        return transport.reconcile_generated_files(
            self.repo,
            self.source,
            self.manifest,
            self.target)

    def test_full_selected_manifest_reaches_fresh_target(self):
        document = yaml.safe_load((ROOT / 'generated/artifact-manifest.yaml').read_text())
        selected = next((t for t in document['targets'] if t['repo'] == self.repo))
        source = ROOT / selected['sourceRoot']
        for k in source.glob(
            'apps/hcp/management/hosted-cluster-lifecycle/overlays/*/*/kustomization.yaml'):
            if 'registry-ca-bundle.yaml' in k.read_text():
                path = self.target / k.parent.relative_to(source) / 'registry-ca-bundle.yaml'
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    '-----BEGIN CERTIFICATE-----\nSYNTHETIC\n-----END CERTIFICATE-----\n')
        paths = transport.reconcile_generated_files(
            self.repo,
            source,
            ROOT / 'generated/artifact-manifest.yaml',
            self.target)
        self.assertEqual(len(selected['files']), len(paths) - 1)
        for item in selected['files']:
            self.assertEqual(digest((self.target / item['path']).read_bytes()), item['sha256'])

    def test_rename_and_shared_change_remove_only_prior_generated_files(self):
        self.inventory({'apps/shared/config.yaml': b'old', 'clusters/old.yaml': b'retired'})
        self.sync()
        unrelated = self.target / 'apps/shared/site.yaml'
        unrelated.write_bytes(b'site-owned')
        secure = self.target / 'apps/hcp/management/hosted-cluster-lifecycle/overlays/demo/bootstrap/secure/private.yaml'
        secure.parent.mkdir(parents=True)
        secure.write_bytes(b'runtime-owned')
        self.inventory({'apps/shared/config.yaml': b'new', 'clusters/renamed.yaml': b'replacement'})
        self.sync()
        self.assertFalse((self.target / 'clusters/old.yaml').exists())
        self.assertEqual((self.target / 'apps/shared/config.yaml').read_bytes(), b'new')
        self.assertEqual(unrelated.read_bytes(), b'site-owned')
        self.assertEqual(secure.read_bytes(), b'runtime-owned')

    def test_changed_retired_owner_fails_before_any_payload_overwrite(self):
        self.inventory({'old.yaml': b'old', 'shared.yaml': b'before'})
        self.sync()
        (self.target / 'old.yaml').write_bytes(b'independent change')
        self.inventory({'shared.yaml': b'after'})
        with self.assertRaisesRegex(ValueError, 'retired'):
            self.sync()
        self.assertEqual((self.target / 'shared.yaml').read_bytes(), b'before')

    def test_corrupt_source_and_unclaimed_collision_fail(self):
        self.inventory({'payload.yaml': b'expected'})
        (self.source / 'payload.yaml').write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'source manifest mismatch'):
            self.sync()
        self.inventory({'payload.yaml': b'expected'})
        (self.target / 'payload.yaml').write_bytes(b'site-owned')
        with self.assertRaisesRegex(ValueError, 'unclaimed target collision'):
            self.sync()

    def test_sync_repo_never_commits_a_failed_inventory(self):
        self.inventory({'payload.yaml': b'expected'})
        calls = []

        def run(cmd, cwd=None, env=None):
            calls.append(cmd)
            if cmd[1] == 'clone':
                Path(cmd[-1]).mkdir(parents=True)
            return 'changed'
        with mock.patch.object(
            transport,
            'run',
            side_effect=run), mock.patch.object(
            transport,
            'reconcile_generated_files',
            side_effect=ValueError(
                'bad inventory')):
            with self.assertRaisesRegex(ValueError, 'bad inventory'):
                transport.sync_repo(
                    self.repo,
                    self.source,
                    self.manifest,
                    'unused',
                    self.root / 'clones',
                    'SYNTHETIC',
                    'test',
                    None)
        self.assertFalse(any((c[1] in ('commit', 'push') for c in calls)))
if __name__ == '__main__':
    unittest.main()
