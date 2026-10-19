"""Attestation reads named Git objects, never substitute worktree bytes."""
import hashlib
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import yaml
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
spec = importlib.util.spec_from_file_location(
    'exact_attestation',
    ROOT / 'scripts/scoped-publication-attestation.py')
attest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(attest)

class ExactCommitAttestation(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'target'
        self.target.mkdir()
        self.git('init', '-q')
        self.payload = self.target / 'payload.yaml'
        self.payload.write_bytes(b'old committed payload\n')
        self.base = self.commit('base')
        self.manifest = self.root / 'manifest.yaml'
        self.stages = self.root / 'stages.yaml'

    def git(self, *args):
        return subprocess.check_output(['git', '-C', str(self.target), *args], text=True).strip()

    def commit(self, message):
        self.git('add', '.')
        self.git(
            '-c',
            'user.name=Fixture',
            '-c',
            'user.email=fixture@example.invalid',
            'commit',
            '-qm',
            message)
        return self.git('rev-parse', 'HEAD')

    def configure(self, content, state='present'):
        self.manifest.write_text(
            yaml.safe_dump(
                {
                    'source': {
                        'commit': '1' * 40},
                    'targets': [
                        {
                            'repo': 'fixture',
                            'files': [
                                ] if state == 'absent' else [
                                {
                                    'path': 'payload.yaml',
                                    'sha256': hashlib.sha256(
                                        content).hexdigest(
                                        )}]}]}))
        self.stages.write_text(
            yaml.safe_dump(
                {
                    'spec': {
                        'targetRepo': 'fixture',
                        'targetBase': self.base,
                        'stages': [
                            {
                                'name': 'selected',
                                'allowlist': [
                                    {
                                        'path': 'payload.yaml',
                                        'state': state}]}]}}))

    def build(self, head='HEAD'):
        return attest.build_attestation(
            self.manifest,
            self.stages,
            'selected',
            self.target,
            target_head=head,
            target_mode='publication')

    def test_dirty_payload_cannot_be_attested_as_committed_head(self):
        content = b'only in worktree\n'
        self.payload.write_bytes(content)
        self.configure(content)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.build()

    def test_arbitrary_old_head_cannot_borrow_clean_new_checkout(self):
        content = b'new committed payload\n'
        self.payload.write_bytes(content)
        self.commit('new')
        self.configure(content)
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            self.build(self.base)

    def test_deleted_worktree_file_does_not_make_committed_file_absent(self):
        self.payload.unlink()
        self.configure(b'', state='absent')
        with self.assertRaisesRegex(ValueError, 'expected absent'):
            self.build(self.base)

    def test_exact_old_commit_can_be_verified_independent_of_worktree(self):
        self.configure(self.payload.read_bytes())
        self.payload.write_bytes(b'later content\n')
        self.commit('later')
        result = self.build(self.base)
        self.assertEqual(
            result[
                'scope'][
                'orderedAllowlist'][
                0][
                'sha256'],
            hashlib.sha256(
                b'old committed payload\n').hexdigest(
                ))

    def test_committed_symlink_is_not_an_attested_regular_file(self):
        self.payload.unlink()
        self.payload.symlink_to('outside')
        head = self.commit('symlink')
        self.configure(b'outside')
        with self.assertRaisesRegex(ValueError, 'not a committed regular file'):
            self.build(head)
if __name__ == '__main__':
    unittest.main()
