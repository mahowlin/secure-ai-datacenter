"""Canonical Subscription selection and a connected site without a local registry."""
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import yaml
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from resolver import resolve,validate

class GitOpsBootstrapInputTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name)
        shutil.copytree(ROOT/'examples/standalone',self.root,dirs_exist_ok=True)
        for name in ('templates','schemas'):shutil.copytree(ROOT/name,self.root/name)
        spec=importlib.util.spec_from_file_location('bootstrap_input_renderer',ROOT/'scripts/render-ocp.py')
        self.renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.renderer)
    def change(self,relative,mutate):
        path=self.root/relative;document=yaml.safe_load(path.read_text());mutate(document)
        path.write_text(yaml.safe_dump(document,sort_keys=False))
    def rendered(self):
        platform=resolve(self.root);self.assertEqual(validate(platform),[])
        first=self.renderer.render_ocp_install(platform.clusters[0],platform.defaults,root=self.root)
        self.assertEqual(first,self.renderer.render_ocp_install(platform.clusters[0],platform.defaults,root=self.root))
        return first
    def test_declared_subscription_fields_and_refs_drive_metadata(self):
        self.change('data/defaults/example.yaml',lambda d:d['spec']['gitops']['catalogSourceAliases'].update(redhat='changed-catalog'))
        self.change('data/defaults/example.yaml',lambda d:d['spec']['gitops']['namespaces'].update(marketplace='changed-marketplace',operators='changed-operators'))
        self.change('data/defaults/example.yaml',lambda d:d['spec']['gitops']['operatorSubscriptions']['openshift-gitops-operator'].update(channel='changed-channel',installPlanApproval='Manual'))
        selected=yaml.safe_load(self.rendered()['metadata.yaml'])['platformServices']['gitops']['operatorSubscription']
        self.assertEqual({k:selected[k] for k in ('source','sourceNamespace','namespace','channel','installPlanApproval')},
                         {'source':'changed-catalog','sourceNamespace':'changed-marketplace','namespace':'changed-operators','channel':'changed-channel','installPlanApproval':'Manual'})
    def test_connected_no_local_registry_or_additional_ca_renders(self):
        def remove_local(d):
            d['spec'].pop('registry');d['spec'].pop('imageDigestSources')
            d['spec']['releaseImageRepository']='quay.io/openshift-release-dev/ocp-release'
        self.change('data/defaults/example.yaml',remove_local)
        self.change('data/clusters/example-third-party/cluster.yaml',lambda d:d['spec']['installConfig']['openshift'].update(registryTrustPolicy='public'))
        rendered=self.rendered();metadata=yaml.safe_load(rendered['metadata.yaml']);install=yaml.safe_load(rendered['install-config.yaml'])
        self.assertEqual(metadata['imageDelivery']['registry'],'')
        self.assertNotIn('registry',metadata['platformServices']);self.assertNotIn('registryTrustPolicy',metadata['platformServices'])
        self.assertNotIn('additionalTrustBundle',install);self.assertNotIn('imageDigestSources',install)
        self.assertEqual(metadata['platformServices']['gitops']['operatorSubscription']['source'],'redhat-operators')
    def test_missing_selection_and_conflicting_reference_fail(self):
        defaults=self.root/'data/defaults/example.yaml';original=defaults.read_text()
        for mutation,expected in [(lambda s:s.pop('channel'),'channel'),
                                  (lambda s:s.pop('sourceNamespaceRef'),'sourceNamespace'),
                                  (lambda s:s.update(installPlanApproval='invented'),'installPlanApproval'),
                                  (lambda s:s.update(source='conflicting-catalog'),'both source and sourceRef')]:
            with self.subTest(expected=expected):
                defaults.write_text(original)
                self.change('data/defaults/example.yaml',lambda d:mutation(d['spec']['gitops']['operatorSubscriptions']['openshift-gitops-operator']))
                self.assertTrue(any(expected in error for error in validate(resolve(self.root))))
    def test_mirrored_mode_still_requires_actual_registry(self):
        self.change('data/defaults/example.yaml',lambda d:d['spec'].pop('registry'))
        self.change('data/clusters/example-third-party/cluster.yaml',lambda d:d['spec']['installConfig']['openshift'].update(imageDelivery={'mode':'mirrored','scopes':['ocp-install-core','cilium-day1','gitops-bootstrap']}))
        self.assertTrue(any('requires spec.registry.url' in error for error in validate(resolve(self.root))))
if __name__=='__main__':unittest.main()
