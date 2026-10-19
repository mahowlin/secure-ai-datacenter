"""The login consumer preserves retained HC ownership and unrelated bytes."""
import copy
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/stage_hcp_phase0.py'
sys.path.insert(0, str(SCRIPT.parent))
spec = importlib.util.spec_from_file_location('login_stage', SCRIPT)
stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stage)


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False))


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class LoginConsumerTests(unittest.TestCase):
    def fixture(self, temporary, names=('alpha', 'beta', 'gamma'), secret='custom-login'):
        root, source, target = [Path(temporary) / p for p in ('inputs', 'render', 'target')]
        management = 'other-management'
        subjects = dict(zip(('worker-a', 'worker-b', 'worker-c'), names))
        subjects['stagedOnly'] = ['retained-other']
        directory = root / 'data/clusters' / management
        put(directory / 'cluster.yaml', {'kind': 'OCPCluster', 'metadata': {'name': management},
            'spec': {'day2GitOps': {'hostedClusterLifecycle': {'deploymentSubjects': subjects}}}})
        policy = {'hostedTargets': 'deployment-subjects', 'secretName': secret,
                  'secretKey': 'htpasswd', 'providerName': 'custom-provider',
                  'mappingMethod': 'claim', 'type': 'HTPasswd'}
        put(directory / 'login.yaml', {'kind': 'ConsoleLogin', 'spec': policy})
        base = Path('apps/hcp/management/hosted-cluster-lifecycle/overlays') / management
        put(target / base / 'kustomization.yaml', {'resources': ['retained-other']})
        put(target / base / 'phase0/kustomization.yaml', {'resources': list(names)})
        app = stage.manual_app('existing-owner', str(base / 'phase0'),
                               {'server': 'https://kubernetes.default.svc'},
                               'held', 'https://example.invalid/repo', 'main', 'gitops')
        put(target / 'clusters/_staged' / f'{management}-services-phase0' /
            f'{management}-hosted-clusters.yaml', app)
        oauth = {'identityProviders': [{'name': policy['providerName'], 'mappingMethod': 'claim',
            'type': 'HTPasswd', 'htpasswd': {'fileData': {'name': secret}}}]}
        for name in names:
            metadata = {'name': name, 'namespace': 'clusters-' + name}
            hc = {'apiVersion': 'hypershift.openshift.io/v1beta1', 'kind': 'HostedCluster',
                  'metadata': metadata, 'spec': {'configuration': {'operatorhub': {'disableAllDefaultSources': True}},
                  'release': {'image': 'retained-exact-image'},
                  'additionalTrustBundle': {'name': 'retained-trust'}, 'services': [{'service': 'APIServer'}]}}
            prepared = copy.deepcopy(hc)
            prepared['spec']['configuration']['oauth'] = copy.deepcopy(oauth)
            # These unrelated rendered fields must never replace retained target fields.
            prepared['spec']['release']['image'] = 'new-image-not-released'
            put(source / base / name / 'hostedcluster.yaml', prepared)
            put(source / base / name / 'kustomization.yaml', {'resources': ['hostedcluster.yaml', 'console-login-sealedsecret.yaml']})
            sealed = {'apiVersion': 'bitnami.com/v1alpha1', 'kind': 'SealedSecret',
                'metadata': {'name': secret, 'namespace': metadata['namespace']},
                'spec': {'encryptedData': {'htpasswd': 'AgFixtureOnly'}}}
            put(source / base / name / 'console-login-sealedsecret.yaml', sealed)
            path = target / base / 'phase0' / name
            put(path / 'hostedcluster.yaml', hc)
            # Preserve a retained comment too, not just parsed unrelated fields.
            (path / 'hostedcluster.yaml').write_text('# retained consumer\n' + (path / 'hostedcluster.yaml').read_text())
            put(path / 'kustomization.yaml', {'resources': ['registry-trust.yaml', 'hostedcluster.yaml', 'nodepool-workers.yaml']})
            put(path / 'nodepool-workers.yaml', {'spec': {'replicas': 5}})
            put(path / 'registry-trust.yaml', {'data': {'ca-bundle.crt': 'PUBLIC-TRUST-FIXTURE'}})
        return root, source, target, management, base, names

    def test_renamed_inputs_preserve_all_unrelated_resources_and_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, target, management, base, names = self.fixture(temp, ('one-new', 'two-new', 'three-new'), 'renamed-secret')
            before = snapshot(target)
            stage.stage_console_login(root, source, target, management)
            after = snapshot(target)
            changed = {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}
            expected = {str(base / 'phase0' / name / file) for name in names for file in
                        ('hostedcluster.yaml', 'kustomization.yaml', 'console-login-sealedsecret.yaml')}
            self.assertEqual(changed, expected)
            for name in names:
                path = str(base / 'phase0' / name / 'hostedcluster.yaml')
                old, new = yaml.safe_load(before[path]), yaml.safe_load(after[path])
                oauth = new['spec']['configuration'].pop('oauth')
                self.assertEqual(old, new)
                self.assertEqual(oauth['identityProviders'][0]['htpasswd']['fileData']['name'], 'renamed-secret')
                added = yaml.safe_dump({'oauth': oauth}, sort_keys=False)
                added = ''.join(' ' * 4 + line + '\n' for line in added.splitlines())
                self.assertEqual(after[path].decode().replace(added, '', 1), before[path].decode())
                sealed = base / name / 'console-login-sealedsecret.yaml'
                self.assertEqual((source / sealed).read_bytes(), (target / base / 'phase0' / name / sealed.name).read_bytes())
            stage.stage_console_login(root, source, target, management)
            self.assertEqual(snapshot(target), after)

    def test_mismatched_last_subject_rejects_without_partial_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, target, management, base, names = self.fixture(temp)
            path = source / base / names[-1] / 'console-login-sealedsecret.yaml'
            secret = yaml.safe_load(path.read_text()); secret['metadata']['namespace'] = 'wrong-namespace';put(path, secret)
            before = snapshot(target)
            with self.assertRaisesRegex(ValueError, 'sealed reference differs'):
                stage.stage_console_login(root, source, target, management)
            self.assertEqual(snapshot(target), before)

    def test_existing_other_provider_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root, source, target, management, base, names = self.fixture(temp)
            path = target / base / 'phase0' / names[0] / 'hostedcluster.yaml'
            hc = yaml.safe_load(path.read_text());hc['spec']['configuration']['oauth'] = {'identityProviders': [{'name': 'other-provider'}]};put(path, hc)
            before = snapshot(target)
            with self.assertRaisesRegex(ValueError, 'provider replacement'):
                stage.stage_console_login(root, source, target, management)
            self.assertEqual(snapshot(target), before)

    def test_automated_owner_or_wrong_stage_is_rejected(self):
        for modification in ('automated', 'wrong-source'):
            with self.subTest(modification=modification), tempfile.TemporaryDirectory() as temp:
                root, source, target, management, base, names = self.fixture(temp)
                path = target / 'clusters/_staged' / f'{management}-services-phase0' / f'{management}-hosted-clusters.yaml'
                app = yaml.safe_load(path.read_text())
                if modification == 'automated':app['spec']['syncPolicy']['automated'] = {'prune': True}
                else:app['spec']['source']['path'] = 'other-owner'
                put(path, app);before = snapshot(target)
                with self.assertRaisesRegex(ValueError, 'must remain held'):
                    stage.stage_console_login(root, source, target, management)
                self.assertEqual(snapshot(target), before)


if __name__ == '__main__':
    unittest.main()
