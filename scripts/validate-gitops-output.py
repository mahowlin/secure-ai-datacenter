#!/usr/bin/env python3
"""Validate generated GitOps output before transport/sync."""

from __future__ import annotations
from site_identity import gitops_worker_value, load_identity, platform_defaults_file, public_image_registries

import argparse
import hashlib
import ipaddress
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from resolver import resolve, legacy_management_cilium_devices


_PLATFORM_ROOT: Path | None = None
_SELECTED_PLATFORM = None


def selected_platform_root() -> Path:
    if _PLATFORM_ROOT is None:
        raise ValueError("GitOps validator requires the selected platform input root")
    return _PLATFORM_ROOT


def selected_cluster(rel: str):
    parts = Path(rel).parts
    if len(parts) < 3 or parts[0] != "clusters":
        raise ValueError(f"cannot identify selected management cluster from {rel}")
    cluster = next((item for item in _SELECTED_PLATFORM.clusters if item.name == parts[1]), None)
    if cluster is None:
        raise ValueError(f"management cluster {parts[1]} is absent from selected inputs")
    return cluster


LOCAL_SERVER = "https://kubernetes.default.svc"
SUPPORTED_BGP_KINDS = {
    "IsovalentBFDProfile",
    "IsovalentBGPAdvertisement",
    "IsovalentBGPClusterConfig",
    "IsovalentBGPNodeConfigOverride",
    "IsovalentBGPPeerConfig",
    "IsovalentBGPPolicy",
    "CiliumLoadBalancerIPPool",
    "CiliumEgressGatewayPolicy",
}
BGP_PEER_SPEC_KEYS = {
    "transport",
    "timers",
    "bfdProfileRef",
    "gracefulRestart",
    "families",
    "importPolicyRef",
}
BFD_PROFILE_SPEC_KEYS = {
    "detectMultiplier",
    "receiveIntervalMilliseconds",
    "transmitIntervalMilliseconds",
}
DISALLOWED_SHELL_TOKENS = (" eval ", " curl ", " wget ", " rm -rf ", " kubectl ")
WATCHED_BGP_KEYS = {"sourceInterface", "localAddress", "bfdProfile", "bfdProfileRef"}
_API_GROUP: str | None = None


def _bind_identity(platform_root: Path) -> None:
    global _API_GROUP, _PLATFORM_ROOT, _SELECTED_PLATFORM
    _PLATFORM_ROOT = platform_root.resolve()
    _SELECTED_PLATFORM = resolve(_PLATFORM_ROOT)
    group = load_identity(platform_root).get("apiGroup")
    if not isinstance(group, str) or not group:
        raise ValueError("selected spec.identity.apiGroup is required")
    _API_GROUP = group


def api_label(suffix: str) -> str:
    if not _API_GROUP:
        raise ValueError("selected spec.identity.apiGroup is required")
    return f"{_API_GROUP}/{suffix}"


def control_plane_ingress_service_label() -> str:
    return api_label("control-plane-ingress")


HCP_ROUTER_INGRESS_CNP = "allow-hcp-router-ingress"
OLD_REQUEST_SERVING_CNP_PREFIX = "allow-remote-node-to-"
HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION = "hypershift.openshift.io/kubevirt-vm-jsonpatch"
HCP_ROUTE_HOSTNAME_PREFIXES = {
    "APIServer": "api",
    "Ignition": "ignition",
    "OAuthServer": "oauth",
    "Konnectivity": "konnectivity",
}
HCP_APISERVER_ROUTE_PORT = 443


def _declared_text(*path: str, label: str) -> str:
    value: Any = yaml.safe_load(platform_defaults_file(selected_platform_root()).read_text())
    for key in path:
        value = value.get(key) if isinstance(value, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is required")
    return value


def contains_literal(value: Any, literal: str) -> bool:
    if isinstance(value, dict):
        return any(contains_literal(key, literal) or contains_literal(item, literal) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_literal(item, literal) for item in value)
    return isinstance(value, str) and literal in value


def contains_watched_bgp_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key in WATCHED_BGP_KEYS or contains_watched_bgp_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(contains_watched_bgp_key(item) for item in value)
    return False


def public_image_refs(value: Any, path: str = "") -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key in {"image", "repository"} and isinstance(item, str):
                if item.startswith(public_image_registries(selected_platform_root())):
                    refs.append((child_path, item))
            refs.extend(public_image_refs(item, child_path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            refs.extend(public_image_refs(item, f"{path}[{index}]"))
    return refs


def load_yaml_documents(path: Path) -> list[Any]:
    try:
        return list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc


def yaml_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".yaml", ".yml"}:
            continue
        parts = path.relative_to(root).parts
        if parts and parts[0] == "charts" and "{{" in path.read_text():
            # Templated chart sources are validated after Helm rendering; plain
            # YAML carried by charts remains in this validation pass.
            continue
        files.append(path)
    return sorted(files)


def is_clife_package_source(source: dict[str, Any]) -> bool:
    return source.get("chart") == "charts/clife-install"


def application_helm_values(app: dict[str, Any]) -> dict[str, Any] | None:
    source = ((app.get("spec", {}) or {}).get("source", {}) or {})
    raw_values = ((source.get("helm", {}) or {}).get("values"))
    if not isinstance(raw_values, str):
        return None
    try:
        values = yaml.safe_load(raw_values)
    except yaml.YAMLError:
        return None
    return values if isinstance(values, dict) else None


def is_sha256(value: Any, prefix: bool = False) -> bool:
    if not isinstance(value, str):
        return False
    digest = value.removeprefix("sha256:") if prefix else value
    if prefix and not value.startswith("sha256:"):
        return False
    return len(digest) == 64 and all(character in "0123456789abcdef" for character in digest)


def load_cilium_config_profile(profile_ref: Any, profile_sha256: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Load a hash-bound SAIF profile used to validate rendered management values."""
    if not isinstance(profile_ref, str) or not profile_ref or "/" in profile_ref or ".." in profile_ref:
        return None, "rendered management Cilium values must name a valid CiliumConfig profile"
    if not is_sha256(profile_sha256):
        return None, "rendered management Cilium values must bind a CiliumConfig profile SHA256"
    path = selected_platform_root() / "data" / "cilium-config-profiles" / f"{profile_ref}.yaml"
    if not path.is_file():
        return None, f"rendered management CiliumConfig profile {profile_ref!r} was not found"
    source = path.read_bytes()
    if hashlib.sha256(source).hexdigest() != profile_sha256:
        return None, f"rendered management CiliumConfig profile {profile_ref!r} SHA256 does not match"
    profile = yaml.safe_load(source)
    if profile.get("kind") != "CiliumConfigProfile" or profile.get("metadata", {}).get("name") != profile_ref:
        return None, f"rendered management CiliumConfig profile {profile_ref!r} has invalid identity"
    values = profile.get("spec", {}).get("values")
    if not isinstance(values, dict):
        return None, f"rendered management CiliumConfig profile {profile_ref!r} has no values mapping"
    return values, None


def required_mapping_mismatches(actual: Any, required: Any, path: str = "spec") -> list[str]:
    """Return paths where a rendered mapping differs from its declarative profile."""
    if isinstance(required, dict):
        if not isinstance(actual, dict):
            return [path]
        mismatches: list[str] = []
        for key, value in required.items():
            if key not in actual:
                mismatches.append(f"{path}.{key}")
            else:
                mismatches.extend(required_mapping_mismatches(actual[key], value, f"{path}.{key}"))
        return mismatches
    return [] if actual == required else [path]


def expected_hcp_hostname(hcp_name: str, base_domain: str, service: str) -> str:
    return f"{HCP_ROUTE_HOSTNAME_PREFIXES[service]}.{hcp_name}.{base_domain}"


def application_role(app: dict[str, Any]) -> str:
    metadata = app.get("metadata", {}) or {}
    spec = app.get("spec", {}) or {}
    source = spec.get("source", {}) or {}
    destination = spec.get("destination", {}) or {}
    source_path = source.get("path") or ""
    source_chart = source.get("chart") or ""
    name = metadata.get("name", "")
    platform_idms_app = (
        name == "platform-idms"
        or name.endswith("-platform-idms")
        or "platform-idms" in source_path
        or "platform-idms" in source_chart
    )

    if destination.get("name"):
        if platform_idms_app:
            return "hosted-remote:platform-idms"
        if (
            "guest-cni" in source_path
            or (
                is_clife_package_source(source)
                and name == f"{destination.get('name')}-guest-cni"
            )
        ):
            return "hosted-remote:guest-cni"
        if "platform-catalogsources" in source_path:
            return "hosted-remote:platform-catalogsources"
        if "tenant-datapath" in source_path or "/tenants/" in source_path:
            return "hosted-remote:tenant-datapath"
        return "hosted-remote:other"

    if destination.get("server") == LOCAL_SERVER:
        if "/management/operators" in source_path:
            return "management-local:operators"
        if "/management/operands" in source_path:
            return "management-local:operands"
        if "/management/parent-network" in source_path:
            return "management-local:parent-network"
        if "/management/hosted-clusters" in source_path:
            return "management-local:hosted-cluster-lifecycle"
        if platform_idms_app:
            return "management-local:platform-idms"
        if "/tenants/" in source_path or "/tenant/" in source_path:
            return "invalid:tenant-payload-local"
        return "management-local:other"

    return "invalid:unknown-destination"


def validate_application(path: Path, app: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    metadata = app.get("metadata", {}) or {}
    spec = app.get("spec", {}) or {}
    source = spec.get("source", {}) or {}
    destination = spec.get("destination", {}) or {}
    name = metadata.get("name", "<unnamed>")
    source_path = source.get("path") or ""
    role = application_role(app)

    if destination.get("name") and destination.get("server"):
        errors.append(f"{rel}: Application {name} sets both destination.name and destination.server")
    if not destination.get("name") and not destination.get("server"):
        errors.append(f"{rel}: Application {name} must set destination.name or destination.server")

    if role.startswith("invalid:"):
        errors.append(f"{rel}: Application {name} has invalid destination class {role} for source {source_path!r}")

    if role == "hosted-remote:platform-idms":
        errors.append(
            f"{rel}: hosted platform-idms Application {name} is invalid for HCP tenants; "
            "use HostedCluster.spec.imageContentSources instead"
        )

    hosted_cluster = destination.get("name")
    if hosted_cluster and f"/tenants/{hosted_cluster}" not in source_path:
        if "guest-cni" in source_path or "tenant-datapath" in source_path:
            expected_overlay = f"/overlays/{hosted_cluster}"
            if expected_overlay not in source_path:
                errors.append(f"{rel}: hosted Application {name} destination.name={hosted_cluster!r} does not match source path {source_path!r}")

    if is_clife_package_source(source):
        if "automated" in ((spec.get("syncPolicy", {}) or {})):
            errors.append(f"{rel}: CLIFE package Application must require manual activation")
        annotations = metadata.get("annotations", {}) or {}
        package_digest = annotations.get(api_label("clife-package-digest"))
        receipt_digest = annotations.get(api_label("clife-receipt-sha256"))
        if not is_sha256(package_digest, prefix=True):
            errors.append(f"{rel}: CLIFE package Application must bind an immutable package digest")
        if not is_sha256(receipt_digest):
            errors.append(f"{rel}: CLIFE package Application must bind a receipt SHA256")
        values = application_helm_values(app)
        if values is None:
            errors.append(f"{rel}: CLIFE package Application must contain valid mapping Helm values")
        elif hosted_cluster:
            if role != "hosted-remote:guest-cni" or name != f"{hosted_cluster}-guest-cni":
                errors.append(
                    f"{rel}: hosted CLIFE package Application name {name!r} must match "
                    f"destination.name {hosted_cluster!r}"
                )
            if destination.get("namespace") != "cilium":
                errors.append(f"{rel}: hosted CLIFE package Application must target namespace 'cilium'")
            cilium_config = values.get("ciliumConfig", {}) or {}
            network_operator = values.get("networkOperator", {}) or {}
            if values.get("mode") != "hosted":
                errors.append(f"{rel}: hosted CLIFE package Application mode must be 'hosted'")
            if cilium_config.get("kubeProxyReplacement") is not True:
                errors.append(f"{rel}: hosted CiliumConfig must set kubeProxyReplacement=true")
            if ((cilium_config.get("gatewayAPI", {}) or {}).get("enabled")) is not False:
                errors.append(f"{rel}: hosted CiliumConfig must keep Gateway API disabled")
            if network_operator.get("deployKubeProxy") is not False:
                errors.append(f"{rel}: hosted Network operator must set deployKubeProxy=false")
            if ((network_operator.get("defaultNetwork", {}) or {}).get("type")) != "Cilium":
                errors.append(f"{rel}: hosted Network operator defaultNetwork.type must be 'Cilium'")
        else:
            if destination.get("server") != LOCAL_SERVER or destination.get("namespace") != "cilium":
                errors.append(f"{rel}: management CLIFE package Application must target local namespace 'cilium'")
            if not name.endswith("-hcp-clife"):
                errors.append(f"{rel}: management CLIFE package Application name must end with '-hcp-clife'")
            if values.get("mode") != "management":
                errors.append(f"{rel}: management CLIFE package Application mode must be 'management'")
            if values.get("ciliumConfig") != {} or values.get("networkOperator") != {}:
                errors.append(f"{rel}: management CLIFE package must not own CiliumConfig or Network operator values")

    if "/tenants/" in source_path and destination.get("server") == LOCAL_SERVER:
        errors.append(f"{rel}: tenant payload Application {name} must target hosted cluster with destination.name, not parent-local server")

    return errors


def application_source_identity(app: dict[str, Any]) -> dict[str, Any]:
    source = ((app.get("spec", {}) or {}).get("source", {}) or {})
    return {
        key: source.get(key)
        for key in ("repoURL", "chart", "path", "targetRevision")
        if source.get(key) is not None
    }


def application_sync_wave(app: dict[str, Any]) -> int | None:
    metadata = app.get("metadata", {}) or {}
    raw_wave = (metadata.get("annotations", {}) or {}).get("argocd.argoproj.io/sync-wave", "0")
    try:
        return int(str(raw_wave))
    except ValueError:
        return None


def validate_platform_idms_delivery(applications: list[tuple[str, dict[str, Any]]]) -> list[str]:
    errors: list[str] = []
    base_sources: list[tuple[str, dict[str, Any]]] = []

    for rel, app in applications:
        role = application_role(app)
        if role == "management-local:platform-idms":
            base_sources.append((rel, application_source_identity(app)))

    if not base_sources:
        errors.append("management-local platform-idms Application not found")
    else:
        base_source = base_sources[0][1]
        for rel, source in base_sources[1:]:
            if source != base_source:
                errors.append(f"{rel}: management-local platform-idms source differs from first platform-idms source")

    return errors


def validate_hosted_cluster_image_content_sources(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    if doc.get("kind") != "HostedCluster":
        return []
    if "/hosted-cluster-lifecycle/" not in f"/{rel}":
        return []
    errors: list[str] = []
    sources = ((doc.get("spec", {}) or {}).get("imageContentSources") or [])
    if not isinstance(sources, list) or not sources:
        return [f"{rel}: HostedCluster must set spec.imageContentSources for HCP worker airgap bootstrap"]
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"{rel}: spec.imageContentSources[{index}] must be a mapping")
            continue
        mirrors = source.get("mirrors")
        if not isinstance(source.get("source"), str) or not source["source"]:
            errors.append(f"{rel}: spec.imageContentSources[{index}].source is required")
        if not isinstance(mirrors, list) or not mirrors:
            errors.append(f"{rel}: spec.imageContentSources[{index}].mirrors must be a non-empty list")
            continue
        for mirror in mirrors:
            if not isinstance(mirror, str) or not mirror:
                errors.append(f"{rel}: spec.imageContentSources[{index}].mirrors entries must be non-empty strings")
            elif mirror.startswith(public_image_registries(selected_platform_root())):
                errors.append(f"{rel}: spec.imageContentSources[{index}] mirror {mirror!r} points at a public registry")
    return errors


def validate_app_type_layout(root: Path) -> list[str]:
    errors: list[str] = []
    hcp_root = root / "apps" / "hcp"
    if not hcp_root.exists():
        return errors
    for child in sorted(path for path in hcp_root.iterdir() if path.is_dir()):
        if child.name not in {"management", "tenant"}:
            errors.append(f"stale HCP app path {child.relative_to(root).as_posix()}: use apps/hcp/management or apps/hcp/tenant app-type layout")
    return errors


def validate_bgp_schema(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    kind = doc.get("kind")
    spec = doc.get("spec", {}) or {}
    if not isinstance(spec, dict):
        return [f"{rel}: {kind} spec must be a mapping"]

    if kind == "IsovalentBGPPeerConfig":
        extra = sorted(set(spec) - BGP_PEER_SPEC_KEYS)
        if extra:
            errors.append(f"{rel}: IsovalentBGPPeerConfig has unsupported spec field(s): {', '.join(extra)}")
        transport = spec.get("transport", {}) or {}
        if not isinstance(transport, dict):
            errors.append(f"{rel}: IsovalentBGPPeerConfig spec.transport must be a mapping")
        elif "sourceInterface" in transport and not isinstance(transport["sourceInterface"], str):
            errors.append(f"{rel}: spec.transport.sourceInterface must be a string")
        if "sourceInterface" in spec:
            errors.append(f"{rel}: sourceInterface must be under spec.transport.sourceInterface")
        if "bfdProfile" in spec:
            errors.append(f"{rel}: use spec.bfdProfileRef, not unsupported spec.bfdProfile")
        if "bfdProfileRef" in spec and not isinstance(spec["bfdProfileRef"], str):
            errors.append(f"{rel}: spec.bfdProfileRef must be a string")

    if kind == "IsovalentBFDProfile":
        if doc.get("apiVersion") != "isovalent.com/v1alpha1":
            errors.append(f"{rel}: IsovalentBFDProfile apiVersion must be isovalent.com/v1alpha1")
        extra = sorted(set(spec) - BFD_PROFILE_SPEC_KEYS)
        if extra:
            errors.append(f"{rel}: IsovalentBFDProfile has unsupported spec field(s): {', '.join(extra)}")
        for key in BFD_PROFILE_SPEC_KEYS:
            if key in spec and not isinstance(spec[key], int):
                errors.append(f"{rel}: IsovalentBFDProfile spec.{key} must be an integer")

    if kind == "IsovalentBGPNodeConfigOverride":
        if doc.get("apiVersion") != "isovalent.com/v1":
            errors.append(
                f"{rel}: IsovalentBGPNodeConfigOverride apiVersion must be isovalent.com/v1"
            )
        instances = spec.get("bgpInstances")
        if not isinstance(instances, list) or not instances:
            errors.append(
                f"{rel}: IsovalentBGPNodeConfigOverride spec.bgpInstances must be a non-empty list"
            )
        else:
            for instance in instances:
                if not isinstance(instance, dict) or not instance.get("name"):
                    errors.append(f"{rel}: every BGP override instance must have a name")
                    continue
                peers = instance.get("peers")
                if not isinstance(peers, list) or not peers:
                    errors.append(
                        f"{rel}: BGP override instance {instance['name']} must contain peers"
                    )
                    continue
                for peer in peers:
                    if not isinstance(peer, dict) or not peer.get("name"):
                        errors.append(f"{rel}: every BGP override peer must have a name")
                        continue
                    local_address = peer.get("localAddress")
                    try:
                        parsed = ipaddress.ip_address(local_address)
                    except ValueError:
                        errors.append(
                            f"{rel}: BGP override peer {peer['name']} localAddress must be an IP address"
                        )
                    else:
                        if parsed.version != 4:
                            errors.append(
                                f"{rel}: BGP override peer {peer['name']} localAddress must be IPv4"
                            )

    if kind and contains_watched_bgp_key(doc) and kind not in SUPPORTED_BGP_KINDS:
        errors.append(
            f"{rel}: {kind} contains BFD/BGP source-address fields but is not an approved "
            "Cilium/Isovalent BGP kind"
        )

    return errors



def validate_hosted_registry_ca(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    if doc.get("kind") != "ConfigMap" or (doc.get("metadata", {}) or {}).get("name") != "registry-ca-bundle":
        return errors
    data = doc.get("data", {}) or {}
    ca_bundle = data.get("ca-bundle.crt", "")
    if ca_bundle == "REGISTRY_CA_PLACEHOLDER":
        errors.append(f"{rel}: hosted registry CA placeholder must be enriched before transport/sync")
    elif "BEGIN CERTIFICATE" not in ca_bundle or "END CERTIFICATE" not in ca_bundle:
        errors.append(f"{rel}: hosted registry CA bundle must contain PEM certificate data")
    return errors


def validate_hosted_guest_cni_catalog(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    if "/apps/hcp/tenant/guest-cni/" not in f"/{rel}":
        return errors

    kind = doc.get("kind")
    metadata = doc.get("metadata", {}) or {}
    if kind == "CatalogSource":
        errors.append(f"{rel}: hosted guest-cni overlays must not carry CatalogSource {metadata.get('name')!r}; use platform-catalogsources")

    return errors


def validate_hosted_guest_cni_subscription_sources(
    subscriptions: list[tuple[str, dict[str, Any]]],
    platform_catalog_sources: dict[str, str],
) -> list[str]:
    errors: list[str] = []
    for rel, doc in subscriptions:
        spec = doc.get("spec", {}) or {}
        source = spec.get("source")
        source_namespace = spec.get("sourceNamespace")
        if source not in platform_catalog_sources:
            errors.append(f"{rel}: hosted CLIFE Subscription source {source!r} must reference a generated platform CatalogSource")
            continue
        expected_namespace = platform_catalog_sources[source]
        if source_namespace != expected_namespace:
            errors.append(
                f"{rel}: hosted CLIFE Subscription sourceNamespace {source_namespace!r} "
                f"must match generated CatalogSource namespace {expected_namespace!r}"
            )
    return errors


def validate_mixed_worker_profile_output(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name", "")
    kind = doc.get("kind")

    if kind == "VirtualMachine" and name == _declared_text("spec", "gitops", "workerDhcp", "serverName", label="worker DHCP server name"):
        template_spec = (((doc.get("spec", {}) or {}).get("template", {}) or {}).get("spec", {}) or {})
        volumes = template_spec.get("volumes", []) or []
        images = [
            ((volume.get("containerDisk") or {}).get("image"))
            for volume in volumes if isinstance(volume, dict) and volume.get("containerDisk")
        ]
        if len(images) != 1 or "@sha256:" not in str(images[0]):
            errors.append(f"{rel}: central DHCP VM must use exactly one immutable digest-pinned image")
        if not any((volume.get("persistentVolumeClaim") or {}).get("claimName") for volume in volumes if isinstance(volume, dict)):
            errors.append(f"{rel}: central DHCP VM must mount external persistent lease state")
        interfaces = (((template_spec.get("domain") or {}).get("devices") or {}).get("interfaces") or [])
        if any(interface.get("macAddress") for interface in interfaces if isinstance(interface, dict)):
            errors.append(f"{rel}: central DHCP VM interfaces must not preassign client identity")

    return errors


def _validate_worker_dns_json_patch(rel: str, raw_patch: Any,
                                    allow_management_dns: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(raw_patch, str) or not raw_patch:
        return [f"{rel}: direct worker NodePool must set {HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION} annotation"]
    if any(token in raw_patch for token in ("cloudInitConfigDrive", "networkData", load_identity(selected_platform_root())["labelDomains"]["poc"])):
        errors.append(f"{rel}: KubeVirt VM JSON patch must not use cloud-init networkData")
    try:
        patch = json.loads(raw_patch)
    except json.JSONDecodeError as exc:
        return [f"{rel}: {HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION} is invalid JSON: {exc}"]
    if not isinstance(patch, list):
        return [f"{rel}: {HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION} must decode to a JSON patch list"]

    values_by_path: dict[str, Any] = {}
    for index, op in enumerate(patch):
        if not isinstance(op, dict):
            errors.append(f"{rel}: JSON patch entry {index} must be an object")
            continue
        operation = op.get("op")
        path = op.get("path")
        if operation != "add":
            errors.append(f"{rel}: JSON patch entry {index} must use op=add, got {operation!r}")
        if not isinstance(path, str):
            errors.append(f"{rel}: JSON patch entry {index} must set path")
            continue
        values_by_path[path] = op.get("value")

    dns_policy = values_by_path.get("/spec/template/spec/dnsPolicy")
    dns_config = values_by_path.get("/spec/template/spec/dnsConfig")
    affinity = values_by_path.get("/spec/template/spec/affinity")
    if affinity is not None:
        node_terms = (((affinity.get("nodeAffinity") or {}).get(
            "requiredDuringSchedulingIgnoredDuringExecution") or {}).get("nodeSelectorTerms")
            if isinstance(affinity, dict) else None)
        preferred = ((affinity.get("podAntiAffinity") or {}).get(
            "preferredDuringSchedulingIgnoredDuringExecution")
            if isinstance(affinity, dict) else None)
        if (not isinstance(node_terms, list) or len(node_terms) != 1
                or not isinstance(node_terms[0], dict)
                or not isinstance(node_terms[0].get("matchExpressions"), list)
                or len(node_terms[0]["matchExpressions"]) != 1
                or node_terms[0]["matchExpressions"][0].get("key") != "kubernetes.io/hostname"
                or node_terms[0]["matchExpressions"][0].get("operator") != "In"
                or not node_terms[0]["matchExpressions"][0].get("values")):
            errors.append(f"{rel}: KubeVirt VM affinity must select eligible management hostnames")
        if (not isinstance(preferred, list) or len(preferred) != 1
                or not isinstance(preferred[0], dict)
                or type(preferred[0].get("weight")) is not int
                or not 1 <= preferred[0]["weight"] <= 100
                or (preferred[0].get("podAffinityTerm") or {}).get("topologyKey") != "kubernetes.io/hostname"):
            errors.append(f"{rel}: KubeVirt VM affinity must prefer hostname spreading")
    # The launcher Pod resolver is explicitly supplied to KubeVirt bridge DHCP.
    if dns_policy != "None":
        errors.append(f"{rel}: KubeVirt VM JSON patch must set /spec/template/spec/dnsPolicy to 'None'")

    if not isinstance(dns_config, dict):
        errors.append(f"{rel}: KubeVirt VM JSON patch must set /spec/template/spec/dnsConfig")
        return errors
    nameservers = dns_config.get("nameservers")
    if not isinstance(nameservers, list) or not nameservers:
        errors.append(f"{rel}: KubeVirt VM JSON patch dnsConfig.nameservers must be a non-empty list")
        return errors
    for nameserver in nameservers:
        try:
            ip = ipaddress.IPv4Address(nameserver)
        except (TypeError, ValueError) as exc:
            errors.append(f"{rel}: KubeVirt VM JSON patch has invalid DNS nameserver {nameserver!r}: {exc}")
            continue
        if not allow_management_dns and any(
            ip in ipaddress.IPv4Network(item)
            for item in yaml.safe_load(platform_defaults_file(selected_platform_root()).read_text())["spec"]["openshift"]["serviceNetwork"]
        ):
            errors.append(
                f"{rel}: KubeVirt VM JSON patch must not use OpenShift cluster DNS service IP {ip}; "
                "workers need tenant-reachable DNS before Ignition"
            )
    return errors


def validate_direct_worker_nodepool(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    if doc.get("kind") != "NodePool":
        return []
    platform = (doc.get("spec", {}) or {}).get("platform", {}) or {}
    kubevirt = platform.get("kubevirt", {}) or {}
    additional_networks = kubevirt.get("additionalNetworks") or []
    if not additional_networks:
        return []

    errors: list[str] = []
    names = [
        network.get("name") if isinstance(network, dict) else None
        for network in additional_networks
    ]
    if kubevirt.get("attachDefaultNetwork") is not False:
        errors.append(f"{rel}: NodePool with a workload attachment must set kubevirt.attachDefaultNetwork=false")
    if len(names) != 1:
        errors.append(f"{rel}: single-NIC worker profile must render exactly one additionalNetwork")

    annotations = (doc.get("metadata", {}) or {}).get("annotations", {}) or {}
    if HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION not in annotations:
        return errors
    errors.extend(_validate_worker_dns_json_patch(
        rel, annotations.get(HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION),
        allow_management_dns=annotations.get(api_label("guest-dns-source")) == "management-coredns",
    ))
    for index, network in enumerate(additional_networks):
        name = network.get("name") if isinstance(network, dict) else None
        if not name:
            errors.append(f"{rel}: NodePool additionalNetworks[{index}].name is required")
            continue
    return errors


def _parse_nad_config(doc: dict[str, Any], rel: str) -> tuple[dict[str, Any] | None, list[str]]:
    config_raw = ((doc.get("spec", {}) or {}).get("config"))
    if not config_raw:
        return None, []
    if not isinstance(config_raw, str):
        return None, [f"{rel}: NetworkAttachmentDefinition spec.config must be a JSON string"]
    try:
        config = json.loads(config_raw)
    except json.JSONDecodeError as exc:
        return None, [f"{rel}: NetworkAttachmentDefinition spec.config is invalid JSON: {exc}"]
    if not isinstance(config, dict):
        return None, [f"{rel}: NetworkAttachmentDefinition spec.config must decode to an object"]
    return config, []


def validate_direct_worker_nad(
    path: Path,
    doc: dict[str, Any],
    rel: str,
    default_routes_by_namespace: dict[str, list[str]],
    nad_count_by_namespace: dict[str, int],
) -> list[str]:
    if doc.get("kind") != "NetworkAttachmentDefinition":
        return []
    metadata = doc.get("metadata", {}) or {}
    namespace = metadata.get("namespace", "")
    name = metadata.get("name", "<unnamed>")
    config, errors = _parse_nad_config(doc, rel)
    if not config or config.get("type") != "cnv-bridge":
        return errors

    nad_count_by_namespace[namespace] = nad_count_by_namespace.get(namespace, 0) + 1
    bridge = config.get("bridge")
    if not isinstance(bridge, str) or not bridge.startswith("br-"):
        errors.append(f"{rel}: direct worker NAD {name} must use a br-<vlan> bridge")

    annotations = metadata.get("annotations", {}) or {}
    if annotations.get(api_label("peering-subnet-dhcp")) != "true":
        errors.append(
            f"{rel}: direct worker NAD {name} must set {api_label('peering-subnet-dhcp')}=true; "
            "Whereabouts direct worker IPAM is not supported"
        )
    if "ipam" in config:
        errors.append(f"{rel}: DHCP direct worker NAD {name} must not set CNI ipam")
    if config.get("dns"):
        errors.append(f"{rel}: DHCP direct worker NAD {name} must not set CNI dns; DHCP options own guest DNS")
    if annotations.get(api_label("guest-default-route")) == "true":
        default_routes_by_namespace.setdefault(namespace, []).append(name)

    return errors


def _gateway_publication_accounts() -> tuple[str, str]:
    document = yaml.safe_load(platform_defaults_file(selected_platform_root()).read_text())
    ipam = document["spec"]["gitops"]["workerIpam"]
    periodic = (ipam.get("gatewayArpPeriodic") or {}).get("serviceAccount") or ""
    neighbor = (ipam.get("gatewayNeighborLifecycle") or {}).get("serviceAccount") or ""
    return periodic, neighbor


def validate_direct_worker_absence(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    periodic_account, neighbor_account = _gateway_publication_accounts()
    if ("/parent-network/overlays/" in f"/{rel}"
            and rel.endswith("/gateway-arp-periodic/source-configmap.yaml")
            and doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == periodic_account):
        # Source text reads the retained VM annotation; it does not emit a
        # direct-bridge worker resource carrying that annotation.
        return errors
    if ("/parent-network/overlays/" in f"/{rel}"
            and rel.endswith("/gateway-neighbor/configmap.yaml")
            and doc.get("kind") == "ConfigMap"
            and doc.get("metadata", {}).get("name") == neighbor_account):
        # Source reads allocation annotations; it does not inject them into VMs.
        return errors
    if ("/peer-route-installer/overlays/" in f"/{rel}"
            and rel.endswith("/source.yaml")
            and doc.get("kind") == "ConfigMap"
            and str(doc.get("metadata", {}).get("name", "")).startswith("saif-route-")):
        # The helper source verifies an existing admission claim. It is not a
        # direct-bridge worker resource or an annotation writer.
        return errors
    rel_guard = f"/{rel}"
    if "/worker-dhcp/" in rel_guard:
        return errors
    if "kubevirt-template-dns-patch" in rel or contains_literal(doc, "kubevirt-template-dns-patch"):
        errors.append(f"{rel}: direct bridge worker output must not include out-of-band KubevirtMachineTemplate DNS patch resources")
    if contains_literal(doc, "cloudInitConfigDrive") or contains_literal(doc, "networkData"):
        errors.append(f"{rel}: direct bridge worker output must not patch cloudInitConfigDrive.networkData for DNS")
    return errors


def validate_hcp_cilium_devices(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    if doc.get("kind") != "Kustomization":
        return []
    resources = doc.get("resources") or []
    if "hcp-parent-network.yaml" not in resources or "hcp-hosted-clusters.yaml" not in resources:
        return []

    cluster = selected_cluster(rel)
    management_bond = cluster.management_interface.bond
    expected_devices = legacy_management_cilium_devices(cluster)
    errors: list[str] = []
    for patch in doc.get("patches") or []:
        target = patch.get("target", {}) or {}
        if target.get("kind") != "Application" or target.get("name") != "cilium-config":
            continue
        raw_patch = patch.get("patch")
        if not isinstance(raw_patch, str):
            return [f"{rel}: cilium-config Application patch must be a YAML string"]
        try:
            ops = yaml.safe_load(raw_patch) or []
        except yaml.YAMLError as exc:
            return [f"{rel}: cilium-config Application patch is invalid YAML: {exc}"]
        if not isinstance(ops, list):
            return [f"{rel}: cilium-config Application patch must decode to a list"]

        params: dict[str, str] = {}
        rendered_spec: dict[str, Any] | None = None
        rendered_package_digest: Any = None
        rendered_receipt_sha256: Any = None
        rendered_profile_ref: Any = None
        rendered_profile_sha256: Any = None
        application_annotations: dict[str, Any] = {}
        for op in ops:
            if not isinstance(op, dict):
                continue
            if op.get("path") == "/spec/source/helm/valuesObject":
                rendered = ((op.get("value", {}) or {}).get("renderedCiliumConfig", {}) or {})
                if rendered.get("enabled") is True and isinstance(rendered.get("spec"), dict):
                    rendered_spec = rendered["spec"]
                    rendered_package_digest = rendered.get("packageDigest")
                    rendered_receipt_sha256 = rendered.get("receiptSha256")
                    rendered_profile_ref = rendered.get("profileRef")
                    rendered_profile_sha256 = rendered.get("profileSha256")
            annotation_prefix = "/metadata/annotations/"
            if str(op.get("path", "")).startswith(annotation_prefix):
                key = str(op["path"])[len(annotation_prefix):].replace("~1", "/").replace("~0", "~")
                application_annotations[key] = op.get("value")
            value = op.get("value", {}) or {}
            if not isinstance(value, dict):
                continue
            name = value.get("name")
            if isinstance(name, str):
                params[name] = str(value.get("value", ""))

        if params.get("l2.device") != management_bond:
            errors.append(f"{rel}: l2.device must match selected management interface {management_bond}")

        if rendered_spec is not None:
            if not is_sha256(rendered_package_digest, prefix=True):
                errors.append(f"{rel}: receipt-resolved management Cilium values must bind an immutable package digest")
            if not is_sha256(rendered_receipt_sha256):
                errors.append(f"{rel}: receipt-resolved management Cilium values must bind a receipt SHA256")
            if application_annotations.get(api_label("clife-package-digest")) != rendered_package_digest:
                errors.append(f"{rel}: management Cilium Application package digest must match rendered values")
            if application_annotations.get(api_label("clife-receipt-sha256")) != rendered_receipt_sha256:
                errors.append(f"{rel}: management Cilium Application receipt SHA256 must match rendered values")
            if application_annotations.get(api_label("cilium-config-profile")) != rendered_profile_ref:
                errors.append(f"{rel}: management Cilium Application profile reference must match rendered values")
            if application_annotations.get(api_label("cilium-config-profile-sha256")) != rendered_profile_sha256:
                errors.append(f"{rel}: management Cilium Application profile SHA256 must match rendered values")
            profile_values, profile_error = load_cilium_config_profile(
                rendered_profile_ref, rendered_profile_sha256
            )
            if profile_error:
                errors.append(f"{rel}: {profile_error}")
            else:
                for mismatch in required_mapping_mismatches(rendered_spec, profile_values):
                    errors.append(
                        f"{rel}: receipt-resolved management Cilium values do not match "
                        f"CiliumConfigProfile({rendered_profile_ref}) at {mismatch}"
                    )
            direct_device = ((rendered_spec.get("nodePort", {}) or {}).get("directRoutingDevice"))
            if direct_device != management_bond:
                errors.append(f"{rel}: receipt-resolved Cilium directRoutingDevice must match {management_bond}")
            if rendered_spec.get("devices") != [management_bond]:
                errors.append(f"{rel}: receipt-resolved Cilium devices must match selected management interface {management_bond}")
            if rendered_spec.get("kubeProxyReplacement") is not True:
                errors.append(f"{rel}: receipt-resolved management Cilium must set kubeProxyReplacement=true")
            if ((rendered_spec.get("gatewayAPI", {}) or {}).get("enabled")) is not False:
                errors.append(f"{rel}: receipt-resolved management Cilium must keep Gateway API disabled")
            if ((rendered_spec.get("enterprise", {}) or {}).get("loadbalancer", {}).get("enabled")) is not True:
                errors.append(f"{rel}: receipt-resolved management Cilium must enable enterprise loadbalancer")
            if ((rendered_spec.get("envoy", {}) or {}).get("nodeSelector")) != {"service.cilium.io/node": "t2"}:
                errors.append(f"{rel}: receipt-resolved management Cilium Envoy must select T2 nodes")
            if rendered_spec.get("version") != cluster.release.cilium_day1_version:
                errors.append(f"{rel}: receipt-resolved Cilium version must match selected release {cluster.release.cilium_day1_version}")
            for component, image_ref in sorted((rendered_spec.get("image", {}) or {}).items()):
                if not isinstance(image_ref, str) or "@sha256:" not in image_ref:
                    errors.append(f"{rel}: receipt-resolved management Cilium image.{component} must be digest-pinned")
        else:
            if params.get("ciliumConfig.directRoutingDevice") != management_bond:
                errors.append(f"{rel}: Cilium directRoutingDevice must match selected management interface {management_bond}")
            devices = [
                value for name, value in sorted(params.items())
                if name.startswith("ciliumConfig.devices[")
            ]
            if devices != expected_devices:
                errors.append(
                    f"{rel}: Cilium devices must match selected interface derivation {expected_devices}, got {devices}"
                )
        return errors

    return errors


def validate_all_route_hosted_cluster(doc: dict[str, Any], rel: str) -> tuple[list[str], dict[str, str]]:
    if doc.get("kind") != "HostedCluster":
        return [], {}
    if "/hosted-cluster-lifecycle/" not in f"/{rel}":
        return [], {}

    errors: list[str] = []
    metadata = doc.get("metadata", {}) or {}
    spec = doc.get("spec", {}) or {}
    hcp_name = metadata.get("name", "")
    base_domain = ((spec.get("dns", {}) or {}).get("baseDomain") or "").strip()
    route_hosts: dict[str, str] = {}

    if not hcp_name:
        errors.append(f"{rel}: HostedCluster metadata.name is required")
    if not base_domain:
        errors.append(f"{rel}: HostedCluster spec.dns.baseDomain is required for explicit Route hostnames")

    services = spec.get("services") or []
    by_service = {
        service.get("service"): service
        for service in services
        if isinstance(service, dict)
    }
    seen_hosts: set[str] = set()
    for service in HCP_ROUTE_HOSTNAME_PREFIXES:
        entry = by_service.get(service)
        if not entry:
            errors.append(f"{rel}: HostedCluster must publish {service} with an explicit Route hostname")
            continue
        strategy = entry.get("servicePublishingStrategy") or {}
        if strategy.get("type") != "Route":
            errors.append(f"{rel}: HostedCluster {service} must use servicePublishingStrategy.type=Route")
            continue
        route = strategy.get("route") or {}
        hostname = route.get("hostname")
        expected = expected_hcp_hostname(hcp_name, base_domain, service) if hcp_name and base_domain else None
        if hostname != expected:
            errors.append(f"{rel}: HostedCluster {service} route.hostname must be {expected!r}, got {hostname!r}")
        if hostname:
            if hostname in seen_hosts:
                errors.append(f"{rel}: HostedCluster Route hostname {hostname!r} is duplicated")
            seen_hosts.add(hostname)
            route_hosts[service] = hostname

    return errors, route_hosts


def validate_control_plane_ingress_bgp(doc: dict[str, Any], rel: str) -> tuple[list[str], tuple[str, str] | None]:
    if "/parent-network/" not in f"/{rel}" or "/cilium-bgp/" not in f"/{rel}":
        return [], None

    errors: list[str] = []
    kind = doc.get("kind")
    if contains_literal(doc, "kube-apiserver"):
        errors.append(f"{rel}: all-Route control-plane ingress must not select kube-apiserver Services")

    if kind == "IsovalentBGPPeerConfig":
        for family in ((doc.get("spec", {}) or {}).get("families", []) or []):
            advertise = (((family.get("advertisements") or {}).get("matchLabels") or {}).get("advertise"))
            if advertise and not str(advertise).startswith("control-plane-ingress-"):
                errors.append(f"{rel}: BGPPeerConfig must match control-plane-ingress advertisements, got {advertise!r}")

    if kind == "IsovalentBGPAdvertisement":
        metadata = doc.get("metadata", {}) or {}
        name = metadata.get("name", "")
        labels = metadata.get("labels", {}) or {}
        if not name.startswith("control-plane-ingress-"):
            errors.append(f"{rel}: BGPAdvertisement name must start with control-plane-ingress-, got {name!r}")
        advertise = labels.get("advertise")
        if not isinstance(advertise, str) or not advertise.startswith("control-plane-ingress-"):
            errors.append(f"{rel}: BGPAdvertisement label advertise must be control-plane-ingress-*, got {advertise!r}")
        advertisements = ((doc.get("spec", {}) or {}).get("advertisements", []) or [])
        if len(advertisements) != 1:
            errors.append(f"{rel}: BGPAdvertisement must contain exactly one Service advertisement")
        for advertisement in advertisements:
            if advertisement.get("advertisementType") != "Service":
                errors.append(f"{rel}: BGPAdvertisement advertisementType must be Service")
            service = advertisement.get("service", {}) or {}
            if service.get("addresses") != ["LoadBalancerIP"]:
                errors.append(f"{rel}: BGPAdvertisement service.addresses must be ['LoadBalancerIP']")
            labels = ((advertisement.get("selector", {}) or {}).get("matchLabels", {}) or {})
            if not labels.get(control_plane_ingress_service_label()):
                errors.append(f"{rel}: BGPAdvertisement must select the labeled HCP router Service")

    pool_vip: tuple[str, str] | None = None
    if kind == "CiliumLoadBalancerIPPool":
        metadata = doc.get("metadata", {}) or {}
        name = metadata.get("name", "")
        if not name.startswith("control-plane-ingress-"):
            errors.append(f"{rel}: CiliumLoadBalancerIPPool name must start with control-plane-ingress-, got {name!r}")
        labels = ((((doc.get("spec", {}) or {}).get("serviceSelector", {}) or {}).get("matchLabels", {}) or {}))
        hcp_name = labels.get(control_plane_ingress_service_label())
        if not hcp_name:
            errors.append(f"{rel}: CiliumLoadBalancerIPPool must select the labeled HCP router Service")
        blocks = ((doc.get("spec", {}) or {}).get("blocks", []) or [])
        if len(blocks) != 1:
            errors.append(f"{rel}: CiliumLoadBalancerIPPool must contain exactly one /32 block")
        elif not isinstance(blocks[0], dict) or "cidr" not in blocks[0]:
            errors.append(f"{rel}: CiliumLoadBalancerIPPool block must set cidr")
        else:
            cidr = blocks[0]["cidr"]
            try:
                network = ipaddress.IPv4Network(cidr, strict=False)
            except ValueError as exc:
                errors.append(f"{rel}: CiliumLoadBalancerIPPool block has invalid cidr {cidr!r}: {exc}")
            else:
                if network.prefixlen != 32:
                    errors.append(f"{rel}: CiliumLoadBalancerIPPool block must be a /32, got {cidr!r}")
                if hcp_name:
                    pool_vip = (hcp_name, str(network.network_address))

    return errors, pool_vip


def parse_control_plane_ingress_dns_records(doc: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = doc.get("metadata", {}) or {}
    if doc.get("kind") != "ConfigMap" or metadata.get("name") != "hcp-control-plane-ingress-dns-records":
        return []
    raw_records = ((doc.get("data", {}) or {}).get("records.yaml") or "")
    try:
        parsed = yaml.safe_load(raw_records) or {}
    except yaml.YAMLError:
        return [{"_error": "records.yaml is not valid YAML"}]
    records = parsed.get("records") if isinstance(parsed, dict) else None
    if not isinstance(records, list):
        return [{"_error": "records.yaml must contain a records list"}]
    return [record for record in records if isinstance(record, dict)]


def guest_cni_hcp_from_path(rel: str) -> str | None:
    parts = rel.split("/")
    try:
        index = parts.index("overlays")
    except ValueError:
        return None
    if index + 1 >= len(parts):
        return None
    if parts[:4] != ["apps", "hcp", "tenant", "guest-cni"]:
        return None
    return parts[index + 1]


def _api_port(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def guest_cni_api_endpoints(doc: dict[str, Any], rel: str) -> list[tuple[str, str, str | None, Any]]:
    hcp_name = guest_cni_hcp_from_path(rel)
    kind = doc.get("kind")
    metadata = doc.get("metadata", {}) or {}
    endpoints: list[tuple[str, str, str | None, Any]] = []

    if kind == "Application" and application_role(doc) == "hosted-remote:guest-cni":
        destination = ((doc.get("spec", {}) or {}).get("destination", {}) or {})
        hcp_name = destination.get("name")
        values = application_helm_values(doc)
        if not isinstance(hcp_name, str) or values is None:
            return []
        cilium_config = values.get("ciliumConfig", {}) or {}
        clife = values.get("clife", {}) or {}
        for field, config, host_key, port_key in (
            (
                "CiliumConfig.spec.k8sServiceHost/k8sServicePort",
                cilium_config,
                "k8sServiceHost",
                "k8sServicePort",
            ),
            (
                "clife-controller-manager.KUBERNETES_SERVICE_HOST/KUBERNETES_SERVICE_PORT",
                clife,
                "kubernetesServiceHost",
                "kubernetesServicePort",
            ),
        ):
            host = config.get(host_key)
            endpoints.append((
                hcp_name,
                field,
                host if isinstance(host, str) else None,
                config.get(port_key),
            ))
        return endpoints

    if not hcp_name:
        return []

    if kind == "CiliumConfig":
        spec = doc.get("spec", {}) or {}
        host = spec.get("k8sServiceHost")
        endpoints.append((
            hcp_name,
            "CiliumConfig.spec.k8sServiceHost/k8sServicePort",
            host if isinstance(host, str) else None,
            spec.get("k8sServicePort"),
        ))

    if kind == "Deployment" and metadata.get("name") == "clife-controller-manager":
        containers = ((((doc.get("spec", {}) or {}).get("template", {}) or {}).get("spec", {}) or {}).get("containers", []) or [])
        for container in containers:
            if not isinstance(container, dict) or container.get("name") != "manager":
                continue
            env_values: dict[str, Any] = {}
            for env in container.get("env", []) or []:
                if isinstance(env, dict) and isinstance(env.get("name"), str):
                    env_values[env["name"]] = env.get("value")
            host = env_values.get("KUBERNETES_SERVICE_HOST")
            endpoints.append((
                hcp_name,
                "clife-controller-manager.KUBERNETES_SERVICE_HOST/KUBERNETES_SERVICE_PORT",
                host if isinstance(host, str) else None,
                env_values.get("KUBERNETES_SERVICE_PORT"),
            ))

    return endpoints


def validate_no_public_image_refs(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    for field_path, image_ref in public_image_refs(doc):
        errors.append(f"{rel}: {field_path} must use the local registry, not public image reference {image_ref!r}")
    return errors


def validate_argocd_cluster_secret_job(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    metadata = doc.get("metadata", {}) or {}
    if doc.get("kind") != "Job" or metadata.get("name") != "argocd-cluster-secret-sync":
        return errors

    containers = (((doc.get("spec", {}) or {}).get("template", {}) or {}).get("spec", {}) or {}).get("containers", [])
    if len(containers) != 1:
        return [f"{rel}: argocd-cluster-secret-sync Job must have exactly one container"]
    container = containers[0]
    if container.get("command") != ["/bin/bash", "-c"]:
        errors.append(f"{rel}: argocd-cluster-secret-sync must run an explicit /bin/bash -c command")
    args = container.get("args", [])
    if len(args) != 1 or not isinstance(args[0], str):
        errors.append(f"{rel}: argocd-cluster-secret-sync must have one script argument")
        return errors

    script = args[0]
    if not script.startswith("set -euo pipefail"):
        errors.append(f"{rel}: sync script must start with set -euo pipefail")
    padded = f" {script} "
    for token in DISALLOWED_SHELL_TOKENS:
        if token in padded:
            errors.append(f"{rel}: sync script contains disallowed shell token {token.strip()!r}")
    required_tokens = {
        'SERVER="https://kube-apiserver.$HCP_NS-$HCP_NAME.svc:6443"': "use the management-local HCP API Service for Argo bootstrap",
        "oc create secret generic": "create/update the cluster Secret with oc create secret generic",
        "--from-literal=name=\"$HCP_NAME\"": "set Secret stringData.name from the hosted cluster name",
        "--from-literal=server=\"$SERVER\"": "set Secret stringData.server from the hosted cluster API URL",
        "--from-literal=config=\"$CONFIG\"": "set Secret stringData.config from the generated JSON config",
        "--dry-run=client -o yaml": "render Secret YAML client-side before apply",
        "oc label --local -f - argocd.argoproj.io/secret-type=cluster -o yaml": "add the ArgoCD cluster Secret label locally",
        "oc annotate --local -f - managed-by=render-gitops.py -o yaml": "add the managed-by annotation locally",
        "oc apply -f -": "apply the generated Secret YAML",
    }
    for token, purpose in required_tokens.items():
        if token not in script:
            errors.append(f"{rel}: sync script must {purpose}")
    if "<<EOSECRET" in script or "config: |" in script:
        errors.append(f"{rel}: sync script must not embed multi-line Secret config in a YAML heredoc")

    return errors


def validate_router_service_labeler_job(path: Path, doc: dict[str, Any], rel: str) -> list[str]:
    errors: list[str] = []
    metadata = doc.get("metadata", {}) or {}
    if doc.get("kind") != "Job" or metadata.get("name") != "hcp-router-service-labeler":
        return errors

    containers = (((doc.get("spec", {}) or {}).get("template", {}) or {}).get("spec", {}) or {}).get("containers", [])
    if len(containers) != 1:
        return [f"{rel}: hcp-router-service-labeler Job must have exactly one container"]
    container = containers[0]
    if container.get("command") != ["/bin/bash", "-c"]:
        errors.append(f"{rel}: hcp-router-service-labeler must run an explicit /bin/bash -c command")
    args = container.get("args", [])
    if len(args) != 1 or not isinstance(args[0], str):
        errors.append(f"{rel}: hcp-router-service-labeler must have one script argument")
        return errors

    script = args[0]
    if not script.startswith("set -euo pipefail"):
        errors.append(f"{rel}: labeler script must start with set -euo pipefail")
    padded = f" {script} "
    for token in DISALLOWED_SHELL_TOKENS:
        if token in padded:
            errors.append(f"{rel}: labeler script contains disallowed shell token {token.strip()!r}")
    required_tokens = {
        "oc -n \"$namespace\" get service router": "wait for the HyperShift router Service",
        f"{control_plane_ingress_service_label()}=": "apply the control-plane ingress Service label",
        "io.cilium/lb-ipam-ips=": "request the exact router LoadBalancer /32",
        "oc -n \"$namespace\" label service router $label_args --overwrite": "label the router Service with overwrite",
        "oc -n \"$namespace\" annotate service router $annotation_args --overwrite": "annotate the router Service with the requested /32",
    }
    for token, purpose in required_tokens.items():
        if token not in script:
            errors.append(f"{rel}: labeler script must {purpose}")

    return errors


def _rule_allows_tcp_8443(rule: dict[str, Any]) -> bool:
    for to_port in rule.get("toPorts", []) or []:
        for port in to_port.get("ports", []) or []:
            if str(port.get("port")) == "8443" and port.get("protocol") == "TCP":
                return True
    return False


def validate_hcp_router_ingress_cnp(doc: dict[str, Any], rel: str) -> tuple[list[str], str | None]:
    errors: list[str] = []
    if doc.get("kind") != "CiliumNetworkPolicy":
        return errors, None
    metadata = doc.get("metadata", {}) or {}
    name = metadata.get("name")
    namespace = metadata.get("namespace")

    if isinstance(name, str) and name.startswith(OLD_REQUEST_SERVING_CNP_PREFIX):
        errors.append(f"{rel}: old direct request-serving CNP {name!r} must not be rendered for all-Route HCP ingress")

    if name != HCP_ROUTER_INGRESS_CNP:
        return errors, None
    if not isinstance(namespace, str) or not namespace.startswith("clusters-"):
        errors.append(f"{rel}: {HCP_ROUTER_INGRESS_CNP} must render into an HCP runtime namespace")
        namespace = None

    spec = doc.get("spec", {}) or {}
    match_labels = ((spec.get("endpointSelector", {}) or {}).get("matchLabels", {}) or {})
    if match_labels.get("app") != "private-router":
        errors.append(f"{rel}: {HCP_ROUTER_INGRESS_CNP} must select app=private-router")
    if match_labels.get("hypershift.openshift.io/request-serving-component") != "true":
        errors.append(
            f"{rel}: {HCP_ROUTER_INGRESS_CNP} must select hypershift.openshift.io/request-serving-component=true"
        )

    all_sources_ok = False
    for rule in spec.get("ingress", []) or []:
        allows_8443 = _rule_allows_tcp_8443(rule)
        entities = rule.get("fromEntities", []) or []
        if "all" in entities and allows_8443:
            all_sources_ok = True
        if "fromCIDRSet" in rule or "fromEndpoints" in rule:
            errors.append(f"{rel}: router ingress CNP should not microsegment sources for the current test model")

    if not all_sources_ok:
        errors.append(f"{rel}: {HCP_ROUTER_INGRESS_CNP} must allow fromEntities=all on TCP/8443")

    return errors, namespace


def _platform_root_for(generated: Path) -> Path | None:
    resolved = generated.resolve()
    for candidate in [resolved, *resolved.parents]:
        if (candidate / "data" / "defaults").is_dir() and (candidate / "scripts" / "site_identity.py").is_file():
            return candidate
    return None


def validate(root: Path, enforce_app_type_layout: bool, platform_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not root.exists():
        return [f"GitOps output root does not exist: {root}"]
    source = platform_root or _platform_root_for(root)
    if source is None:
        raise ValueError("GitOps validation requires the platform root that owns the selected inputs")
    _bind_identity(source)

    if enforce_app_type_layout:
        errors.extend(validate_app_type_layout(root))

    application_count = 0
    cluster_secret_jobs = 0
    applications: list[tuple[str, dict[str, Any]]] = []
    platform_catalog_sources: dict[str, str] = {}
    hosted_guest_cni_subscriptions: list[tuple[str, dict[str, Any]]] = []
    direct_nad_defaults_by_namespace: dict[str, list[str]] = {}
    direct_nad_count_by_namespace: dict[str, int] = {}
    hosted_route_hosts: dict[str, dict[str, str]] = {}
    control_plane_ingress_pool_vips: dict[str, str] = {}
    control_plane_ingress_dns_records: list[dict[str, Any]] = []
    guest_api_endpoints: list[tuple[str, str, str | None, Any]] = []
    router_ingress_cnp_namespaces: set[str] = set()
    router_labeler_jobs = 0
    active_hcp_consumers = False
    for path in yaml_files(root):
        rel = path.relative_to(root).as_posix()
        if rel.endswith("/request-serving-remote-node-cnp.yaml"):
            errors.append(f"{rel}: old direct request-serving CNP file must not be rendered")
        for doc in load_yaml_documents(path):
            if not isinstance(doc, dict):
                continue
            if (rel.endswith("/kustomization.yaml")
                    and "/hosted-cluster-lifecycle/overlays/" in f"/{rel}"
                    and "hostedcluster.yaml" in (doc.get("resources") or [])):
                active_hcp_consumers = True
            if doc.get("kind") == "CatalogSource" and rel.startswith("apps/platform-catalogsources/"):
                metadata = doc.get("metadata", {}) or {}
                name = metadata.get("name")
                namespace = metadata.get("namespace")
                if name and namespace:
                    platform_catalog_sources[name] = namespace
            if (
                doc.get("kind") == "Subscription"
                and "/apps/hcp/tenant/guest-cni/" in f"/{rel}"
                and (doc.get("metadata", {}) or {}).get("name") == "clife"
            ):
                hosted_guest_cni_subscriptions.append((rel, doc))
            if doc.get("kind") == "Application":
                application_count += 1
                applications.append((rel, doc))
                errors.extend(validate_application(path, doc, rel))
            errors.extend(validate_bgp_schema(path, doc, rel))
            errors.extend(validate_hosted_registry_ca(path, doc, rel))
            errors.extend(validate_hosted_cluster_image_content_sources(path, doc, rel))
            errors.extend(validate_hosted_guest_cni_catalog(path, doc, rel))
            errors.extend(validate_mixed_worker_profile_output(path, doc, rel))
            errors.extend(validate_direct_worker_nodepool(path, doc, rel))
            errors.extend(validate_direct_worker_nad(
                path,
                doc,
                rel,
                direct_nad_defaults_by_namespace,
                direct_nad_count_by_namespace,
            ))
            errors.extend(validate_direct_worker_absence(path, doc, rel))
            errors.extend(validate_hcp_cilium_devices(path, doc, rel))
            errors.extend(validate_no_public_image_refs(path, doc, rel))
            router_cnp_errors, router_cnp_namespace = validate_hcp_router_ingress_cnp(doc, rel)
            errors.extend(router_cnp_errors)
            if router_cnp_namespace:
                router_ingress_cnp_namespaces.add(router_cnp_namespace)
            hosted_cluster_errors, route_hosts = validate_all_route_hosted_cluster(doc, rel)
            errors.extend(hosted_cluster_errors)
            if route_hosts:
                hcp_name = (doc.get("metadata", {}) or {}).get("name")
                if isinstance(hcp_name, str):
                    hosted_route_hosts[hcp_name] = route_hosts
            bgp_errors, pool_vip = validate_control_plane_ingress_bgp(doc, rel)
            errors.extend(bgp_errors)
            if pool_vip:
                hcp_name, address = pool_vip
                previous = control_plane_ingress_pool_vips.get(hcp_name)
                if previous and previous != address:
                    errors.append(f"{hcp_name}: multiple control-plane ingress pool VIPs rendered: {previous}, {address}")
                control_plane_ingress_pool_vips[hcp_name] = address
            dns_records = parse_control_plane_ingress_dns_records(doc)
            for record in dns_records:
                if "_error" in record:
                    errors.append(f"{rel}: {record['_error']}")
                else:
                    control_plane_ingress_dns_records.append(record)
            guest_api_endpoints.extend(guest_cni_api_endpoints(doc, rel))
            if doc.get("kind") == "Job" and (doc.get("metadata", {}) or {}).get("name") == "argocd-cluster-secret-sync":
                cluster_secret_jobs += 1
                errors.extend(validate_argocd_cluster_secret_job(path, doc, rel))
            if doc.get("kind") == "Job" and (doc.get("metadata", {}) or {}).get("name") == "hcp-router-service-labeler":
                router_labeler_jobs += 1
                errors.extend(validate_router_service_labeler_job(path, doc, rel))

    if application_count == 0:
        errors.append(f"no ArgoCD Applications found under {root}")
    else:
        errors.extend(validate_platform_idms_delivery(applications))
    errors.extend(validate_hosted_guest_cni_subscription_sources(
        hosted_guest_cni_subscriptions,
        platform_catalog_sources,
    ))
    for namespace, nad_count in sorted(direct_nad_count_by_namespace.items()):
        if not namespace.startswith("clusters-"):
            continue
        defaults = direct_nad_defaults_by_namespace.get(namespace, [])
        if nad_count and len(defaults) != 1:
            errors.append(
                f"{namespace}: direct worker NADs require exactly one default-producing NAD, "
                f"got {defaults}"
            )
    if not control_plane_ingress_dns_records:
        errors.append("control-plane ingress DNS record ConfigMap not found in generated GitOps output")
    dns_records_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for record in control_plane_ingress_dns_records:
        hcp_name = record.get("hostedCluster")
        service = record.get("service")
        if not isinstance(hcp_name, str) or not isinstance(service, str):
            errors.append(f"control-plane ingress DNS record missing hostedCluster/service: {record}")
            continue
        dns_records_by_key[(hcp_name, service)] = record
    for hcp_name, route_hosts in sorted(hosted_route_hosts.items()):
        pool_vip = control_plane_ingress_pool_vips.get(hcp_name)
        if not pool_vip:
            errors.append(f"{hcp_name}: missing control-plane ingress CiliumLoadBalancerIPPool /32")
            continue
        for service, route_host in sorted(route_hosts.items()):
            record = dns_records_by_key.get((hcp_name, service))
            if not record:
                errors.append(f"{hcp_name}: missing DNS record for {service} route host {route_host}")
                continue
            if record.get("name") != route_host:
                errors.append(f"{hcp_name}: DNS record for {service} must use route host {route_host!r}, got {record.get('name')!r}")
            if record.get("type") != "A":
                errors.append(f"{hcp_name}: DNS record for {service} must be type A")
            if record.get("address") != pool_vip:
                errors.append(f"{hcp_name}: DNS record for {service} must point to {pool_vip}, got {record.get('address')!r}")
    for hcp_name, field, host, port in guest_api_endpoints:
        expected = (hosted_route_hosts.get(hcp_name) or {}).get("APIServer")
        if not expected:
            errors.append(f"{hcp_name}: cannot validate {field}; APIServer Route hostname not rendered")
        elif not host:
            errors.append(f"{hcp_name}: {field} must set APIServer Route hostname {expected!r}")
        elif host != expected:
            errors.append(f"{hcp_name}: {field} must use APIServer Route hostname {expected!r}, got {host!r}")
        parsed_port = _api_port(port)
        if parsed_port != HCP_APISERVER_ROUTE_PORT:
            errors.append(
                f"{hcp_name}: {field} must use all-Route APIServer port "
                f"{HCP_APISERVER_ROUTE_PORT}, got {port!r}"
            )
    for hcp_name in sorted(hosted_route_hosts):
        namespace = f"clusters-{hcp_name}-{hcp_name}"
        if namespace not in router_ingress_cnp_namespaces:
            errors.append(f"{hcp_name}: missing {HCP_ROUTER_INGRESS_CNP} in namespace {namespace}")
    if active_hcp_consumers and cluster_secret_jobs == 0:
        errors.append("argocd-cluster-secret-sync Job not found in generated GitOps output")
    if active_hcp_consumers and router_labeler_jobs == 0:
        errors.append("hcp-router-service-labeler Job not found in generated GitOps output")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated GitOps output")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--platform-root", type=Path, default=None)
    parser.add_argument(
        "--enforce-app-type-layout",
        action="store_true",
        help="fail on stale apps/hcp/<parent>/... paths after app-type layout migration is active",
    )
    args = parser.parse_args()
    platform_root = args.platform_root
    if platform_root is None:
        candidate = Path(__file__).resolve().parents[1]
        if (candidate / "data" / "defaults").is_dir():
            platform_root = candidate
    if platform_root is None:
        parser.error("pass --platform-root for the selected inputs")
    root = args.root or (
        platform_root / "generated" / "repos" / load_identity(platform_root)["generatedRepos"]["gitops"]
    )

    errors = validate(root.resolve(), args.enforce_app_type_layout, platform_root.resolve())
    if errors:
        print(f"INVALID: {len(errors)} GitOps validation error(s):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)
    print(f"VALID: generated GitOps output passes validation gates at {root}")


if __name__ == "__main__":
    main()
