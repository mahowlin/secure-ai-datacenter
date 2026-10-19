"""Fixture-only external registry checks; no lab allocation is implied."""
import copy
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from resolver import resolve, ResolvedHCPManagementServicePool
from gitops_renderer import render_gitops
from gitops_renderer.local_registry import _render_local_registry
from gitops_renderer.hosted_clusters import _render_hosted_clusters
from gitops_renderer.registry_exposure import registry_exposure
from gitops_renderer.selected_inputs import selected
from gitops_renderer.tenant_observability import _vector_image


def fixture(platform):
    """Synthetic documentation address and names; never enabled real input data."""
    cluster = copy.deepcopy(next(c for c in platform.clusters if c.local_registry))
    cluster.local_registry.pop("cacheProduction", None)
    defaults = copy.deepcopy(platform.defaults)
    network = next(n for n in cluster.kube_api_networks if n.hcp_management_services)
    pool = ResolvedHCPManagementServicePool("fixture-services", "192.0.2.0/28", network.name)
    network.hcp_management_services.append(pool)
    cluster.local_registry["externalPull"] = {
        "enabled": True, "name": "fixture-registry", "networkRef": network.name,
        "poolRef": pool.name, "vip": "192.0.2.4", "allocationEvidence": "fixture-only: RFC5737",
        "hostnamePrefix": "fixture-registry", "servicePort": 443, "listenerPort": 8443,
        "externalTrafficPolicy": "Cluster", "tlsSecret": "fixture-registry-tls",
        "runtimeOwner": "fixture-sysadmin", "pullClientSecret": {"namespace": "fixture-cache", "name": "fixture-client"},
        "consumer": {"workerAttachmentKinds": ["multus-l2"], "deploymentSubjectRoles": ["multus"], "pullSecret": "fixture-pull-secret",
                     "trustBundle": "fixture-additive-ca", "exporterImagePull": "hosted-mirror"},
    }
    return cluster, defaults


class ExternalRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.platform = resolve(ROOT)

    def test_disabled_equals_absent(self):
        c = copy.deepcopy(next(c for c in self.platform.clusters if c.local_registry))
        c.local_registry["externalPull"] = {"enabled": False}
        with selected(self.platform.defaults):
            disabled = _render_local_registry(c, self.platform.defaults)
            c.local_registry.pop("externalPull")
            self.assertEqual(disabled, _render_local_registry(c, self.platform.defaults))
            self.assertIsNone(registry_exposure(c))

    def test_required_inputs_and_allocation_bounds(self):
        c, d = fixture(self.platform)
        for key in list(c.local_registry["externalPull"]):
            if key == "enabled":
                continue
            missing = copy.deepcopy(c)
            missing.local_registry["externalPull"].pop(key)
            with selected(d), self.assertRaisesRegex(ValueError, key):
                registry_exposure(missing)
        for vip in ("192.0.2.0", "192.0.2.15", "198.51.100.4"):
            c.local_registry["externalPull"]["vip"] = vip
            with selected(d), self.assertRaisesRegex(ValueError, "usable address"):
                registry_exposure(c)
        c, d = fixture(self.platform)
        c.local_registry["externalPull"]["tlsSecret"] = c.local_registry["runtimeSecrets"]["pullTls"]
        with selected(d), self.assertRaisesRegex(ValueError, "ServiceCA"):
            registry_exposure(c)

    def test_exact_service_pool_advertisement_tls_and_lifecycle(self):
        c, d = fixture(self.platform)
        files = render_gitops(c, self.platform.fabric.fabric_asn, d)
        self.assertEqual(files, render_gitops(c, self.platform.fabric.fabric_asn, d))
        base = f"apps/hcp/management/local-registry/overlays/{c.name}"
        docs = {name: yaml.safe_load(text) for name, text in files.items() if name.startswith(base + "/")}
        svc = docs[f"{base}/service-external.yaml"]
        pool = docs[f"{base}/external-lb-pool.yaml"]
        advert = docs[f"{base}/external-bgp-advertisement.yaml"]
        self.assertEqual(svc["spec"]["ports"][0]["port"], 443)
        self.assertFalse(svc["spec"]["allocateLoadBalancerNodePorts"])
        self.assertEqual(pool["spec"]["blocks"], [{"cidr": "192.0.2.4/32"}])
        selector = pool["spec"]["serviceSelector"]
        self.assertEqual(advert["spec"]["advertisements"][0]["selector"], selector)
        self.assertEqual(selector["matchLabels"]["io.kubernetes.service.namespace"], c.local_registry["namespace"])
        self.assertEqual(selector["matchLabels"]["io.kubernetes.service.name"], svc["metadata"]["name"])
        conf = docs[f"{base}/configmap.yaml"]["data"]["nginx.conf"]
        self.assertIn("listen 8443 ssl;", conf)
        self.assertIn("/run/pull-auth/htpasswd", conf)
        self.assertIn("/run/external-tls/tls.crt", conf)
        self.assertIn(f"listen {c.local_registry['listeners']['pull']} ssl;", conf)
        self.assertIn(f"listen {c.local_registry['listeners']['sync']} ssl;", conf)
        with selected(d):
            exposure = registry_exposure(c)
        handoff = yaml.safe_load(docs[f"{base}/external-runtime-contract.yaml"]["data"]["contract.yaml"])
        self.assertEqual(set(handoff["tls"]["sans"]), {r["name"] for r in handoff["dns"]})
        for h in c.hosted_clusters:
            hc = yaml.safe_load(files[f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{c.name}/{h.name}/hostedcluster.yaml"])
            if h.name in exposure["subjects"]:
                self.assertEqual(hc["spec"]["additionalTrustBundle"]["name"], "fixture-additive-ca")
                self.assertTrue(any(m.startswith(exposure["endpoint"] + "/")
                                   for item in hc["spec"]["imageContentSources"] for m in item["mirrors"]))
                app = yaml.safe_load(files[f"clusters/{c.name}/{h.name}-timescape-s3-exporter.yaml"])
                values = yaml.safe_load(app["spec"]["source"]["helm"]["values"])
                self.assertTrue(values["image"]["repository"].startswith(exposure["endpoint"] + "/"))
                vector = d.gitops_image_values("vector")
                self.assertTrue(any(item["source"] == vector["repository"] for item in hc["spec"]["imageContentSources"]))
        image = _vector_image(d, "hosted-mirror", exposure["endpoint"])
        self.assertTrue(image["repository"].startswith(exposure["endpoint"] + "/"))
        self.assertEqual(image["sha"], d.gitops_image_values("vector")["digest"])

    def test_changed_identity_and_new_consumer(self):
        c, d = fixture(self.platform)
        c.name = "alternate-management"
        c.base_domain = "alternate.example.test"
        c.local_registry["namespace"] = "alternate-cache"
        c.local_registry["workloadName"] = "alternate-distribution"
        d.identity["apiGroup"] = "alternate.example.test"
        e = c.local_registry["externalPull"]
        e.update(name="alternate-registry", hostnamePrefix="images", vip="192.0.2.5")
        for h in c.hosted_clusters:
            if h.worker_attachment_kind in e["consumer"]["workerAttachmentKinds"]:
                h.application_ingress.dns_zone = c.base_domain
                h.application_ingress.dns_authority = "dns.alternate.example.test"
        original = next(h for h in c.hosted_clusters if h.worker_attachment_kind in e["consumer"]["workerAttachmentKinds"])
        future = copy.deepcopy(original)
        future.name = "future-worker-cluster"
        c.hosted_clusters.append(future)
        c.hosted_cluster_lifecycle["deploymentSubjects"]["multus"] = future.name
        with selected(d):
            exposure = registry_exposure(c)
            files = _render_local_registry(c, d)
            hosted_files = _render_hosted_clusters(c, d)
        future_hc = yaml.safe_load(hosted_files[f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{c.name}/{future.name}/hostedcluster.yaml"])
        self.assertEqual(future_hc["spec"]["additionalTrustBundle"]["name"], e["consumer"]["trustBundle"])
        self.assertTrue(any(m.startswith(exposure["endpoint"] + "/") for item in future_hc["spec"]["imageContentSources"] for m in item["mirrors"]))
        self.assertIn("images.future-worker-cluster.alternate.example.test", exposure["sans"])
        self.assertEqual(exposure["serviceLabels"], {"alternate.example.test/registry-exposure": "alternate-registry"})
        docs = [yaml.safe_load(t) for t in files.values()]
        svc = next(x for x in docs if x["kind"] == "Service" and x["spec"]["type"] == "LoadBalancer")
        self.assertEqual(svc["spec"]["selector"], {"app": "alternate-distribution"})
        self.assertEqual(svc["metadata"]["namespace"], "alternate-cache")
        self.assertEqual(svc["metadata"]["annotations"]["lbipam.cilium.io/ips"], "192.0.2.5")

    def test_vector_rejects_undeclared_endpoint(self):
        d = copy.deepcopy(self.platform.defaults)
        # A selected copy returns deliberately inconsistent image inputs.
        bad = dict(d.gitops_image_values("vector"))
        bad["hostedPullRepository"] = "unselected.example.test:443/repository"
        proxy = SimpleNamespace(gitops_image_values=lambda name: bad, registry_url=d.registry_url)
        with self.assertRaisesRegex(ValueError, "declared hostedPullEndpoint"):
            _vector_image(proxy, "hosted-mirror")

if __name__ == "__main__":
    unittest.main()
