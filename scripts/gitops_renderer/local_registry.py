"""Render the management-side distribution registry from canonical data."""

from resolver import render_template
from .selected_inputs import template_root

from .common import *
from .registry_exposure import registry_exposure


def _template_root() -> str:
    return f"{template_root('gitops')}/apps/hcp/management/local-registry"


def _render_local_registry(cluster: ResolvedOCPCluster,
                           defaults: ResolvedDefaults) -> dict[str, str]:
    registry = cluster.local_registry
    if not registry:
        return {}
    exposure = registry_exposure(cluster)
    secrets = registry["runtimeSecrets"]
    resources = registry["resources"]
    context = {
        "namespace": registry["namespace"],
        "external_pull": exposure,
        "workload_name": registry["workloadName"],
        "listeners": registry["listeners"],
        "pull_service": registry["pullService"],
        "sync_service": registry["syncService"],
        "pvc_name": registry["pvc"]["name"],
        "pvc_size": registry["pvc"]["size"],
        "pvc_class": registry["pvc"]["storageClass"],
        "distribution_image": registry["images"]["distribution"],
        "proxy_image": registry["images"]["accessProxy"],
        "pod_uid": registry["podUid"],
        "http_secret": secrets["http"],
        "pull_auth_secret": secrets["pullAuth"],
        "sync_auth_secret": secrets["syncAuth"],
        "pull_tls_secret": secrets["pullTls"],
        "sync_tls_secret": secrets["syncTls"],
        "distribution_requests": resources["distribution"]["requests"],
        "distribution_limits": resources["distribution"]["limits"],
        "proxy_requests": resources["accessProxy"]["requests"],
        "proxy_limits": resources["accessProxy"]["limits"],
        "empty_dir": registry["emptyDir"],
        "oc_mirror_trial": registry.get("ocMirror") or {},
    }
    base = f"apps/hcp/management/local-registry/overlays/{cluster.name}"
    names = [
        "persistentvolumeclaim",
        "serviceaccount",
        "configmap",
        "service-pull",
        "service-sync",
        "deployment",
        "networkpolicy",
        "kustomization",
    ]
    files = {
        f"{base}/{name}.yaml": HEADER + "---\n" + render_template(f"{_template_root()}/{name}.yaml.j2", context)
        for name in names
    }

    if exposure:
        labels = exposure["serviceLabels"]
        selector = {"matchLabels": {**labels,
                    "io.kubernetes.service.namespace": registry["namespace"],
                    "io.kubernetes.service.name": exposure["name"]}}
        service = {"apiVersion": "v1", "kind": "Service",
                   "metadata": {"name": exposure["name"], "namespace": registry["namespace"],
                                "labels": labels,
                                "annotations": {"lbipam.cilium.io/ips": exposure["vip"]}},
                   "spec": {"type": "LoadBalancer", "allocateLoadBalancerNodePorts": False,
                            "externalTrafficPolicy": exposure["externalTrafficPolicy"],
                            "selector": {"app": registry["workloadName"]},
                            "ports": [{"name": "https", "protocol": "TCP",
                                       "port": exposure["servicePort"], "targetPort": "external-https"}]}}
        pool = {"apiVersion": "cilium.io/v2", "kind": "CiliumLoadBalancerIPPool",
                "metadata": {"name": exposure["name"]},
                "spec": {"blocks": [{"cidr": exposure["vip"] + "/32"}], "serviceSelector": selector}}
        advertisement = {"apiVersion": "isovalent.com/v1", "kind": "IsovalentBGPAdvertisement",
                         "metadata": {"name": exposure["name"], "labels": exposure["advertisementLabels"]},
                         "spec": {"advertisements": [{"advertisementType": "Service",
                                    "service": {"addresses": ["LoadBalancerIP"]}, "selector": selector}]}}
        contract = {"dns": exposure["records"], "tls": {"secret": exposure["tlsSecret"],
                    "sans": exposure["sans"], "runtimeOwner": exposure["runtimeOwner"]},
                    "pullClientSecret": exposure["pullClientSecret"], "consumer": exposure["consumer"],
                    "allocationEvidence": exposure["allocationEvidence"],
                    "networkRef": exposure["networkRef"], "poolRef": exposure["poolRef"]}
        handoff = {"apiVersion": "v1", "kind": "ConfigMap",
                   "metadata": {"name": exposure["name"] + "-runtime-contract", "namespace": registry["namespace"]},
                   "data": {"contract.yaml": yaml.safe_dump(contract, sort_keys=False)}}
        for name, doc in (("service-external", service), ("external-lb-pool", pool),
                          ("external-bgp-advertisement", advertisement), ("external-runtime-contract", handoff)):
            files[f"{base}/{name}.yaml"] = HEADER + _k8s_yaml(doc)
    return files
