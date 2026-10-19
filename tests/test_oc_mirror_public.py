"""Public operator packaging joins and isolation, without registry/API access."""
from pathlib import Path
import copy
import json
import shutil
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from resolver import resolve
from gitops_renderer import render_gitops
from gitops_renderer.oc_mirror_trial import render_oc_mirror_trial
from export_hcp_oc_mirror_producer import producer_manifest

class OperatorPackaging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = resolve(ROOT)
        cls.original = next(c for c in cls.platform.clusters if c.name == 'example-management')
        cls.defaults = cls.platform.defaults

    def setUp(self):
        self.cluster = copy.deepcopy(self.original)
        self.policy = self.cluster.local_registry['ocMirror']

    def render(self):
        return render_oc_mirror_trial(self.cluster, self.defaults)

    def docs(self):
        return {Path(path).name: yaml.safe_load(text) for path,text in self.render().items()}

    def test_olm_owner_and_runtime_references(self):
        docs = self.docs()
        self.assertEqual(len(docs), 8)  # Seven objects plus Kustomization.
        self.assertNotIn('ca-configmap.yaml', docs)
        self.assertEqual(docs['subscription.yaml']['spec']['installPlanApproval'], 'Manual')
        self.assertEqual(docs['subscription.yaml']['spec']['startingCSV'], 'oc-mirror.v0.1.7')
        self.assertEqual(docs['operatorgroup.yaml']['spec']['targetNamespaces'], ['oc-mirror-system'])
        target = docs['mirrortarget.yaml']['spec']
        self.assertFalse(target['insecure'])
        self.assertEqual(target['authSecret'], self.policy['authSecret'])
        self.assertEqual(target['caBundle']['configMapName'], self.policy['caConfigMap'])
        self.assertEqual(target['expose'], {'type':'Service'})
        self.assertNotIn('BEGIN CERTIFICATE', json.dumps(docs))
        self.assertFalse(any(doc.get('kind') in ('Secret','Deployment','CustomResourceDefinition') for doc in docs.values()))

    def test_enabled_full_composition_manual_child_and_registry_ingress(self):
        files = render_gitops(self.cluster, self.platform.fabric.fabric_asn, self.defaults)
        app = yaml.safe_load(files['clusters/example-management/hcp-oc-mirror-trial.yaml'])
        self.assertNotIn('automated', app['spec']['syncPolicy'])
        self.assertEqual(app['spec']['destination']['namespace'], 'oc-mirror-system')
        network = yaml.safe_load(files['apps/hcp/management/local-registry/overlays/example-management/networkpolicy.yaml'])
        rule = network['spec']['ingress'][-1]['from'][0]
        self.assertEqual(rule['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'],'oc-mirror-system')
        self.assertEqual(rule['podSelector']['matchExpressions'][0]['values'], ['oc-mirror-worker','oc-mirror-manager'])

    def test_disabled_feature_has_no_operator_or_extra_ingress(self):
        self.policy['publish'] = False
        files = render_gitops(self.cluster, self.platform.fabric.fabric_asn, self.defaults)
        self.assertFalse(any('oc-mirror-trial' in p for p in files))
        network = yaml.safe_load(files['apps/hcp/management/local-registry/overlays/example-management/networkpolicy.yaml'])
        self.assertNotIn('oc-mirror-system', json.dumps(network))

    def test_same_inputs_deterministic(self):
        self.assertEqual(self.render(), self.render())

    def test_changed_cluster_registry_and_target_propagate(self):
        original = 'registry.example.com:5000'
        alternate = 'mirror.alternate.example:5443'
        self.cluster.name = 'different-management'
        self.cluster.local_registry = json.loads(json.dumps(self.cluster.local_registry).replace(original, alternate))
        self.policy = self.cluster.local_registry['ocMirror']
        self.cluster.oc_mirror_inventory = json.loads(json.dumps(self.cluster.oc_mirror_inventory).replace(original, alternate))
        self.policy['imageSet'] = 'different-images'
        self.policy['mirrorTarget'] = 'different-target'
        files = self.render()
        self.assertTrue(all('different-management' in p for p in files))
        text = json.dumps(files)
        self.assertNotIn(original, text)
        self.assertIn(alternate, text)
        docs = {Path(p).name:yaml.safe_load(t) for p,t in files.items()}
        self.assertEqual(docs['mirrortarget.yaml']['spec']['imageSets'], ['different-images'])

    def test_published_namespace_mismatch_rejected(self):
        self.policy['namespace'] = 'arbitrary-namespace'
        with self.assertRaisesRegex(ValueError, 'namespace binding'):
            self.render()

    def test_missing_plugin_or_catalog_pin_rejected(self):
        self.policy['release']['bootstrapImages'].pop(3)
        with self.assertRaisesRegex(ValueError, 'plugin image missing'):
            self.render()
        self.setUp()
        self.policy['installation']['catalogImage'] = 'wrong.example/catalog@sha256:' + '0'*64
        with self.assertRaisesRegex(ValueError, 'catalog image differs'):
            self.render()

    def test_target_cannot_overwrite_registry_production_namespace(self):
        self.policy['targetPrefix'] = 'hcp-registry-sync.hcp-image-cache.svc:5000/hcp-image-cache/production'
        with self.assertRaisesRegex(ValueError, 'separate sync repository'):
            self.render()

    def test_producer_export_matches_bootstrap_and_tamper_fails(self):
        contract = producer_manifest(ROOT, self.original.name)
        self.assertEqual(contract['images'], self.policy['release']['bootstrapImages'])
        self.assertEqual(len(contract['images']), 10)
        self.assertEqual(contract['trust'], {'mode':'system'})
        with tempfile.TemporaryDirectory() as tmp:
            clone = Path(tmp)/'inputs'
            shutil.copytree(ROOT, clone, ignore=shutil.ignore_patterns('.git','generated','__pycache__'))
            csv = clone/self.policy['installation']['csvPath']
            csv.write_text(csv.read_text()+'\n# tamper\n')
            with self.assertRaisesRegex(ValueError, 'declared digest'):
                producer_manifest(clone, self.original.name)

if __name__ == '__main__': unittest.main()
