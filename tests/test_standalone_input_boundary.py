"""Real input removal/negative cases for the generic reader path (no live effects)."""
import copy
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from resolver import resolve, validate


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec); spec.loader.exec_module(obj); return obj


class StandaloneInputBoundary(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / 'examples/standalone', self.root, dirs_exist_ok=True)
        for name in ('templates', 'schemas'):
            shutil.copytree(ROOT / name, self.root / name)

    def change(self, path, mutate):
        path = self.root / path; data = yaml.safe_load(path.read_text()); mutate(data)
        path.write_text(yaml.safe_dump(data, sort_keys=False))

    def test_one_cluster_no_hosted_conformance_day2_or_fabric(self):
        platform = resolve(self.root)
        self.assertEqual(validate(platform), [])
        self.assertEqual(len(platform.clusters), 1)
        self.assertNotIn('conformance', platform.defaults.identity)
        self.assertNotIn('ilb', platform.defaults.identity)
        self.assertFalse(platform.clusters[0].hosted_clusters)
        renderer = module('standalone_renderer', ROOT / 'scripts/render-ocp.py')
        first = renderer.render_ocp_install(platform.clusters[0], platform.defaults, root=self.root)
        second = renderer.render_ocp_install(platform.clusters[0], platform.defaults, root=self.root)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertEqual(yaml.safe_load(first['metadata.yaml'])['ocpVersion'], '4.19.21')

    def test_enabled_hosted_missing_configuration_fails(self):
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec']['features'].update(hostedControlPlanes=True, KubeVirt=True))
        try:
            platform = resolve(self.root)
        except ValueError:
            pass
        else:
            self.assertTrue(validate(platform))

    def test_enabled_ilb_requires_its_identity_and_configuration(self):
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec'].update(day2GitOps={'inClusterLoadBalancer': {'enabled': True}}))
        errors = validate(resolve(self.root))
        self.assertTrue(any('identity' in e and 'ilb' in e for e in errors), errors)

    def test_unsafe_hardware_and_allocation_still_fail(self):
        for field in ('duplicate-mac', 'api-node-collision', 'no-disk'):
            with self.subTest(field=field):
                platform = resolve(self.root); c = platform.clusters[0]
                if field == 'duplicate-mac': c.nodes[1].interfaces[0] = copy.deepcopy(c.nodes[0].interfaces[0])
                elif field == 'api-node-collision': c.api_vip = c.nodes[0].ip
                else: c.nodes[0].root_device_hints = {}
                self.assertTrue(validate(platform))

    def test_missing_policy_and_unreviewed_pair_fail(self):
        self.change('data/defaults/example.yaml', lambda d: d['spec'].pop('day1Compatibility'))
        self.assertTrue(any('day1Compatibility' in e for e in validate(resolve(self.root))))

    def test_wrong_release_pair_fails_without_changing_code(self):
        self.change('platform-release.yaml', lambda d: d['releases']['saif-ocp-4.19-cilium-1.19.7-stable']['openshift'].update(version='4.21.0'))
        self.assertTrue(any('day1Compatibility' in e for e in validate(resolve(self.root))))

    def test_delivery_input_is_required_without_policy_fallback(self):
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec']['installConfig']['openshift'].pop('imageDelivery'))
        with self.assertRaisesRegex(ValueError,'requires explicit mode and scopes'):
            resolve(self.root)

    def test_registry_trust_input_has_no_implicit_ca_policy(self):
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec']['installConfig']['openshift'].pop('registryTrustPolicy'))
        with self.assertRaisesRegex(ValueError, 'registryTrustPolicy requires an explicit'):
            resolve(self.root)

    def test_delivery_selected_scopes(self):
        for mode, scopes, valid in [('connected', [], True), ('connected', ['ocp-install-core'], False),
                                    ('mirrored', ['ocp-install-core'], False),
                                    ('mirrored', ['ocp-install-core','cilium-day1'], True),
                                    ('mirrored', ['ocp-install-core','cilium-day1','invented'],False)]:
            with self.subTest(mode=mode, scopes=scopes):
                platform=resolve(self.root); c=platform.clusters[0]
                c.image_delivery_mode=mode; c.image_delivery_scopes=scopes
                self.assertEqual(not validate(platform), valid)

    def test_nic_mode_and_site_address_inputs_drive_output(self):
        self.change('data/defaults/example.yaml', lambda d: d['spec']['dns'].update(servers=['192.0.2.53']))
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec']['installConfig']['openshift'].update(baseDomain='changed.example.org'))
        platform=resolve(self.root); c=platform.clusters[0]
        c.management_interface.mode='active-backup';c.management_interface.members=['pnic0','pnic1'];c.management_interface.options={'miimon':'200','primary':'pnic0'}
        for n in c.nodes:
            for i,iface in enumerate(n.interfaces):iface['name']=f'pnic{i}'
        self.assertEqual(validate(platform), [])
        renderer=module('changed_renderer', ROOT/'scripts/render-ocp.py')
        files=renderer.render_ocp_install(c,platform.defaults,root=self.root)
        agent=yaml.safe_load(files['agent-config.yaml'])
        self.assertEqual(yaml.safe_load(files['install-config.yaml'])['baseDomain'], 'changed.example.org')
        self.assertIn('192.0.2.53', files['agent-config.yaml'])
        self.assertEqual(agent['hosts'][0]['networkConfig']['interfaces'][0]['link-aggregation']['port'],['pnic0','pnic1'])

    def test_only_selected_mirror_scopes_are_required(self):
        bundles=module('selected_bundles', ROOT/'scripts/validate-image-policy-bundles.py')
        c=resolve(self.root).clusters[0]
        self.assertEqual(bundles.required_scopes(c), {})
        c.image_delivery_mode='mirrored';c.image_delivery_scopes=['ocp-install-core','cilium-day1']
        self.assertEqual(bundles.required_scopes(c), {'ocpInstallCore':'ocp-install-core','ciliumDay1':'cilium-day1'})
        c.image_delivery_scopes.append('gitops-bootstrap')
        self.assertIn('gitopsBootstrap',bundles.required_scopes(c))

    def test_imports_selected_synthetic_receipt_payloads_and_rejects_corruption(self):
        import hashlib, json, subprocess
        self.change('data/clusters/example-third-party/cluster.yaml', lambda d: d['spec']['installConfig']['openshift'].update(imageDelivery={'mode':'mirrored','scopes':['ocp-install-core','cilium-day1']}))
        c=resolve(self.root).clusters[0]
        owner=self.root/'synthetic-airgap';owner.mkdir()
        for scope in ['ocp-install-core','cilium-day1']:
            base=owner/'mirror/bundles'/scope/'synthetic';(base/'manifests').mkdir(parents=True)
            source='quay.io/openshift-release-dev/ocp-release' if scope=='ocp-install-core' else 'containers.isovalent.com'
            doc={'kind':'ImageDigestMirrorSet','spec':{'imageDigestMirrors':[{'source':source,'mirrors':['registry.example.com:5000/synthetic']} ]}}
            payload=yaml.safe_dump(doc);(base/'manifests/idms.yaml').write_text(payload)
            digest=hashlib.sha256(b'idms.yaml'+b'\0'+payload.encode()+b'\0').hexdigest()
            images=[f'platform:stable-4.19:{c.ocp_version}:{c.ocp_version}'] if scope=='ocp-install-core' else [c.release.cilium_image,c.release.cilium_operator_image,c.release.clife_image]
            meta={'name':scope,'scope':scope,'version':'synthetic-only','specHash':'0'*64,'contentHash':digest}
            (base/'bundle.yaml').write_text(yaml.safe_dump({'metadata':meta,'spec':{'requiredImages':images,'registry':'registry.example.com:5000'}}))
            (base.parent/'current.json').write_text(json.dumps({**{k:meta[k] for k in ['scope','version','specHash','contentHash']},'path':str(base.relative_to(owner))}))
        command=[sys.executable,str(ROOT/'scripts/validate-image-policy-bundles.py'),'--root',str(self.root),'--cluster',c.name,'--sys-admin-root',str(owner),'--output',str(self.root/'contract.yaml'),'--ai-pod-output-root',str(self.root/'consumer/data')]
        result=subprocess.run(command,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        contract=yaml.safe_load((self.root/'contract.yaml').read_text())
        self.assertEqual(set(contract['spec']['bundles']),{'ocpInstallCore','ciliumDay1'})
        copied=self.root/'consumer/data/openshift'/c.name/'image-policy-bundles/cilium-day1/manifests/idms.yaml'
        self.assertTrue(copied.is_file())
        source=owner/'mirror/bundles/cilium-day1/synthetic/manifests/idms.yaml';source.write_text('kind: ConfigMap\n')
        result=subprocess.run(command,text=True,capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertIn('contentHash mismatch',result.stderr)

if __name__ == '__main__': unittest.main()
