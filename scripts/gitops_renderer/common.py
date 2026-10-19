"""Shared constants and helpers for GitOps rendering."""

from .selected_inputs import api_label

from pathlib import Path

import yaml

from resolver import (
    generated_header,
    slugify,
    ResolvedOCPCluster,
    ResolvedHCPCluster,
    ResolvedNetwork,
    ResolvedDefaults,
    resolved_management_cilium_config_spec,
    resolved_clife_image_content_sources,
)


HEADER = generated_header("render-gitops.py")


def worker_ipam_value(defaults, key: str) -> str:
    """Return one required worker IPAM string from the selected defaults."""
    worker = getattr(defaults, "gitops_worker_ipam", None) or {}
    value = worker.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"spec.gitops.workerIpam.{key} is required")
    return value

__all__ = [
    "Path", "yaml", "slugify", "ResolvedOCPCluster", "ResolvedHCPCluster",
    "ResolvedNetwork", "ResolvedDefaults", "HEADER", "_annotation", "_k8s_yaml",
    "resolved_management_cilium_config_spec",
    "resolved_clife_image_content_sources",
    "_bgp_prefix_match", "_kustomization", "_application_summary", "_namespace",
    "_operatorgroup", "_tenant_bond_interface", "_hcp_service_hostname",
    "_control_plane_ingress_service_labels",
    "worker_ipam_value",
]


HCP_SERVICE_HOSTNAME_PREFIXES = {
    "APIServer": "api",
    "Ignition": "ignition",
    "OAuthServer": "oauth",
    "Konnectivity": "konnectivity",
}

def _annotation(sync_wave: int) -> dict:
    return {"argocd.argoproj.io/sync-wave": str(sync_wave)}


def _k8s_yaml(obj: dict) -> str:
    return yaml.dump(obj, default_flow_style=False, sort_keys=False, explicit_start=True)


def _bgp_prefix_match(dest: dict) -> dict:
    match = {"prefix": dest["cidr"]}
    if "minLen" in dest:
        match["minLen"] = dest["minLen"]
    if "maxLen" in dest:
        match["maxLen"] = dest["maxLen"]
    return match


def _kustomization(resources: list[str], patches: list[dict] | None = None) -> str:
    kustomization = {
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    }
    if patches:
        kustomization["patches"] = patches
    return HEADER + _k8s_yaml(kustomization)


def _application_summary(files: dict[str, str]) -> list[tuple[str, str, str, str]]:
    """Return (wave, name, path, file) for generated Argo CD Applications."""
    rows = []
    for fpath, content in sorted(files.items()):
        if fpath.startswith("charts/"):
            continue
        for obj in yaml.safe_load_all(content):
            if not isinstance(obj, dict) or obj.get("kind") != "Application":
                continue
            meta = obj.get("metadata", {})
            source = obj.get("spec", {}).get("source", {})
            wave = meta.get("annotations", {}).get("argocd.argoproj.io/sync-wave", "0")
            rows.append((str(wave), meta.get("name", ""), source.get("path") or source.get("chart", ""), fpath))
    return rows


def _namespace(defaults: ResolvedDefaults, name: str, labels: dict | None = None) -> dict:
    """Create a Namespace resource with standard annotations."""
    meta: dict = {
        "name": name,
        "annotations": _annotation(defaults.sync_wave("namespace")),
    }
    if labels:
        meta["labels"] = labels
    return {"apiVersion": "v1", "kind": "Namespace", "metadata": meta}


def _operatorgroup(defaults: ResolvedDefaults,
                   name: str,
                   namespace: str,
                   target_namespaces: list[str] | None = None) -> dict:
    """Create an OperatorGroup resource."""
    og: dict = {
        "apiVersion": "operators.coreos.com/v1",
        "kind": "OperatorGroup",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": _annotation(defaults.sync_wave("operatorGroup")),
        },
        "spec": {},
    }
    if target_namespaces:
        og["spec"]["targetNamespaces"] = target_namespaces
        og["spec"]["upgradeStrategy"] = "Default"
    return og


def _tenant_bond_interface(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> str:
    if cluster.parent_networking and cluster.parent_networking.tenant_bond:
        return cluster.parent_networking.tenant_bond
    fallback = defaults.gitops_network.get("tenantTransportBondFallback")
    if not fallback:
        raise ValueError("PlatformDefaults spec.gitops.network.tenantTransportBondFallback is required")
    return str(fallback)


def _hcp_service_hostname(cluster: ResolvedOCPCluster, hcp: ResolvedHCPCluster, service: str) -> str:
    prefix = HCP_SERVICE_HOSTNAME_PREFIXES[service]
    return f"{prefix}.{hcp.name}.{cluster.base_domain}"


def _control_plane_ingress_service_labels(hcp: ResolvedHCPCluster) -> dict[str, str]:
    return {api_label("control-plane-ingress"): hcp.name}
