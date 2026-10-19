#!/usr/bin/env python3
"""Shared resolver for platform contract files.

Builds a resolved object graph from NetworkCatalog, OCPCluster, and HCPCluster
source files. All renderers consume the resolver output — no renderer loads or
parses YAML directly.

Usage as library:
    from resolver import resolve
    platform = resolve(root=Path(".."))

Usage as CLI (debug):
    python resolver.py [--root DIR] [--dump]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import ipaddress
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from management_exporter import management_exporter_parameters
from jsonschema import Draft202012Validator

from declared_subjects import PRODUCTION_ROLES, egw_phases, worker_namespace
from site_identity import identity_errors, load_identity, registry_url

from platform_contract import ResolvedPlatformIntent, WORKER_PROFILE_REFS, resolve_profile_intent


WORKER_DHCP_EMIT_OPTIONS = (
    "subnet-mask",
    "router",
    "dns",
    "mtu",
    "hostname",
    "renewal",
)

# ---------------------------------------------------------------------------
# Data classes — the resolved object graph
# ---------------------------------------------------------------------------

@dataclass
class ResolvedPool:
    name: str
    cidr: str
    kind: str  # "loadBalancer" or "egress"
    owning_network: str  # tenant network name
    allocation_start: str = ""
    allocation_stop: str = ""


@dataclass
class ResolvedAPIVIP:
    name: str
    cidr: str
    owning_network: str  # kubeAPI network name


@dataclass
class ResolvedControlPlaneIngressLoadBalancerVIP:
    name: str
    cidr: str
    owning_network: str  # kubeAPI network name


@dataclass
class ResolvedApplicationIngress:
    mode: str
    pool_ref: str
    vip: str
    dns_wildcard: str
    dns_zone: str
    dns_authority: str
    dns_publication: str


@dataclass
class ResolvedHCPManagementServicePool:
    name: str
    cidr: str
    owning_network: str  # kubeAPI network name


@dataclass
class ResolvedSVIPeer:
    ip: str  # e.g. "192.0.2.1/28"
    leaf_node_id: int


@dataclass
class ResolvedWorkerPeering:
    dhcp: bool = False
    server_address: str = ""
    pool_start: str = ""
    pool_end: str = ""
    router: str = ""
    dns_from_defaults: bool = False
    emitted_options: list[str] = field(
        default_factory=lambda: list(WORKER_DHCP_EMIT_OPTIONS)
    )


@dataclass
class ResolvedWorkerIpam:
    enabled: bool = False
    pool_start: str = ""
    pool_end: str = ""
    guest_prefix: int = 32
    guest_gateway: str = ""
    dns_from_defaults: bool = False
    dns_source: str = ""


# ---------------------------------------------------------------------------
# ACI-specific resolved types (see DESIGN.md §3)
# ---------------------------------------------------------------------------

@dataclass
class ResolvedAciLeaf:
    node_id: int


@dataclass
class ResolvedOspfConfig:
    area_id: str
    area_type: str = "regular"
    interface_policy: str = ""
    export_route_control_enforcement: bool = False
    default_routes: list[str] = field(default_factory=list)
    interface_port: int | None = None
    node_interface_ips: dict[int, str] = field(default_factory=dict)


@dataclass
class ResolvedAciNetwork:
    """ACI-specific metadata for a network, resolved from aci.* catalog fields."""
    tenant: str | None = None
    router_ids: dict[int, str] = field(default_factory=dict)  # {nodeId: routerId}
    svi_peers: list[ResolvedSVIPeer] = field(default_factory=list)
    floating_ip: str | None = None  # e.g. "192.0.2.3/28" — routable shared gateway
    path_floating_ip: str | None = None  # e.g. "192.0.2.4/28" — NAC path floating_ip
    ospf: ResolvedOspfConfig | None = None


@dataclass
class ResolvedAciBfdPolicy:
    name: str
    description: str = ""
    subinterface_optimization: bool = False
    detection_multiplier: int = 10
    echo_admin_state: bool = True
    echo_rx_interval: int = 50
    min_rx_interval: int = 300
    min_tx_interval: int = 300


@dataclass
class ResolvedAciBgpBestPathPolicy:
    name: str
    ignore_igp_metric: bool = True


@dataclass
class ResolvedAciBgpPeerPrefixPolicy:
    name: str
    description: str = ""
    action: str = "reject"
    threshold: int = 75
    max_prefixes: int = 500


@dataclass
class ResolvedAciOspfInterfacePolicy:
    name: str
    description: str = ""
    network_type: str = "p2p"
    mtu_ignore: bool = True
    advertise_subnet: bool = True


@dataclass
class ResolvedAciFilter:
    name: str
    entries: list[dict]


@dataclass
class ResolvedAciContractSubject:
    name: str
    filters: list[dict]


@dataclass
class ResolvedAciRouteControl:
    """Route-control policy shape from data/defaults/aci.yaml."""
    import_enforcement: bool
    export_enforcement: bool
    route_map_type: str  # "global" or "combinable"
    default_action: str  # "deny" or "permit"


@dataclass
class ResolvedAciDefaults:
    """ACI desired-state values loaded from data/defaults/aci.yaml."""
    vrf_name: str
    mtu: int
    bfd_policy: ResolvedAciBfdPolicy
    bgp_best_path_policy: ResolvedAciBgpBestPathPolicy
    bgp_peer_prefix_policy: ResolvedAciBgpPeerPrefixPolicy
    ospf_interface_policy: ResolvedAciOspfInterfacePolicy | None
    filters: list[ResolvedAciFilter]
    contract_subjects: list[ResolvedAciContractSubject]
    contract_scope: str
    rendered_contract_subject: str = ""
    endpoint_management_contract: dict | None = None
    route_control: ResolvedAciRouteControl | None = None


@dataclass
class ResolvedUcsDefaults:
    """UCS target-repo conventions loaded from data/defaults/ucs.yaml."""
    org: str
    dom_vlan_policy: str
    srv_vlan_policy: str
    multicast_policy: str
    mgmt_eng_policy: str
    tenant_eng_policy: str
    lan_policy_template: str
    mac_pool: str
    eth_adapter: str
    eth_qos: str
    eth_ctrl: str

    def lan_policy_for(self, node_name: str) -> str:
        return self.lan_policy_template.format(node_name=node_name)


@dataclass
class ResolvedAciFabric:
    """ACI fabric metadata resolved from spec.fabric.aci.*"""
    fabric_asn: int
    border_leaf_nodes: list[ResolvedAciLeaf]
    physical_domain: str | None = None
    shared_aaep: str | None = None
    ai_pod_aaep: str | None = None
    transit_aaep: str | None = None
    tenant_ports: list[dict] = field(default_factory=list)


@dataclass
class ResolvedNetwork:
    name: str
    category: str  # "management", "kubeAPI", "tenant", "domain"
    vlan: int
    mtu: int | None = None
    aci_attachment: dict | None = None
    description: str = ""
    native: bool = False
    cidr: str | None = None
    gateway: str | None = None
    tenant_supernet: str | None = None
    peering_subnet: str | None = None
    cilium_asn: int | None = None
    aci: ResolvedAciNetwork | None = None
    api_vips: list[ResolvedAPIVIP] = field(default_factory=list)
    control_plane_ingress_load_balancer_vips: list[ResolvedControlPlaneIngressLoadBalancerVIP] = field(default_factory=list)
    hcp_management_services: list[ResolvedHCPManagementServicePool] = field(default_factory=list)
    pools_lb: list[ResolvedPool] = field(default_factory=list)
    pools_egress: list[ResolvedPool] = field(default_factory=list)
    vmm_domain: str | None = None  # VMware VMM domain for endpoint EPGs
    import_destinations: list[dict] = field(default_factory=list)  # [{cidr, description, minLen?, maxLen?}] for BGP import
    egress_destinations: list[dict] = field(default_factory=list)  # [{cidr, description}] endpoint CIDRs for EGW policy
    worker_peering: ResolvedWorkerPeering = field(default_factory=ResolvedWorkerPeering)

    # Convenience accessors for ACI fields
    @property
    def aci_tenant(self) -> str | None:
        return self.aci.tenant if self.aci else None

    @property
    def svi_peers(self) -> list[ResolvedSVIPeer]:
        return self.aci.svi_peers if self.aci else []

    @property
    def router_ids(self) -> dict[int, str]:
        return self.aci.router_ids if self.aci else {}

    @property
    def floating_ip(self) -> str | None:
        return self.aci.floating_ip if self.aci else None

    @property
    def path_floating_ip(self) -> str | None:
        return self.aci.path_floating_ip if self.aci else None


@dataclass
class ResolvedNode:
    name: str
    hostname: str
    ip: str
    serial: str | None = None
    ucs_profile: str | None = None
    interfaces: list[dict[str, str]] = field(default_factory=list)
    root_device_hints: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedVNICPair:
    """Concrete UCS host vNIC pair intent; renderers translate this to target policies."""
    names: list[str]
    native_vlan: ResolvedNetwork | None = None
    tagged_vlans: list[ResolvedNetwork] = field(default_factory=list)


@dataclass
class ResolvedUCSHostNetworking:
    management: ResolvedVNICPair
    tenant: ResolvedVNICPair | None = None


@dataclass
class ResolvedManagementInterface:
    bond: str
    members: list[str] = field(default_factory=list)
    mode: str = ""
    options: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedPeeringNetworkEntry:
    """A single peeringNetworks[] entry from day2GitOps.parentNetworking."""
    network_ref: str
    purpose: str  # "hostedClusterApi" or "hostedClusterWorkers"
    bgp_source_address_mode: str = "sourceInterface"
    parent_node_peering_ips: dict[str, str] = field(default_factory=dict)  # {nodeName: ip}


@dataclass
class ResolvedManagementLoadBalancerPool:
    name: str
    start: str
    stop: str
    service_selector: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedParentNetworking:
    tenant_bond: str | None = None
    tenant_members: list[str] = field(default_factory=list)
    peering_networks: list[ResolvedPeeringNetworkEntry] = field(default_factory=list)
    management_lb_pool: ResolvedManagementLoadBalancerPool | None = None


@dataclass
class ResolvedCiliumImageOverride:
    enabled: bool = False
    repository: str = ""
    tag: str = ""
    digest: str = ""
    generic_digest: str = ""
    suffix: str = ""


@dataclass
class ResolvedCiliumConfigIntent:
    prerelease_version: str = ""
    ci_commit: str = ""
    ci_chart: str = ""
    gateway_api_enabled: bool | None = None
    image_overrides: dict[str, ResolvedCiliumImageOverride] = field(default_factory=dict)


@dataclass
class ResolvedPreReleaseCLIFEIntent:
    enabled: bool = False
    lifecycle: str = "manifest"
    manifest_bundle: str = ""
    image: str = ""
    development_mode: bool | None = None
    package_scenario: str = ""


@dataclass
class ResolvedTenantGuestCNIIntent:
    lifecycle: str = "olm"
    package_release_ref: str = ""
    package: ResolvedClifePackage | None = None
    manifest_bundle: str = ""
    channel: str = ""
    starting_csv: str = ""
    install_plan_approval: str = "Manual"
    clife_image: str = ""
    development_mode: bool | None = None
    gateway_api_enabled: bool | None = None
    route_import_enabled: bool | None = None
    package_scenarios: dict[str, str] = field(default_factory=dict)


@dataclass
class ResolvedCiliumConfigProfile:
    name: str
    source_sha256: str = ""
    compatible_cilium_versions: list[str] = field(default_factory=list)
    compatible_openshift_minors: list[str] = field(default_factory=list)
    package_contract_sha256: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    values_schema: dict[str, Any] = field(default_factory=dict, repr=False)
    chart_values: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolvedInClusterLoadBalancerIntent:
    enabled: bool = False
    lifecycle: str = "staged"
    router_replicas: int = 0
    router_t1_nodes: list[str] = field(default_factory=list)
    t2_nodes: list[str] = field(default_factory=list)
    placement_job_image: str = ""
    cilium_config_profile_ref: str = ""
    cilium_config_profile: ResolvedCiliumConfigProfile | None = None
    target_release_ref: str = ""
    target_router_t1_nodes: list[str] = field(default_factory=list)
    target_t2_nodes: list[str] = field(default_factory=list)
    target_activation_overlay: str = ""


@dataclass
class ResolvedClifePackage:
    repository: str
    version: str
    oci_digest: str
    package_sha256: str
    contract_path: str
    contract_sha256: str
    receipt_repository: str
    receipt_revision: str
    receipt_path: str
    receipt_sha256: str
    controller_image: str
    scenarios: dict[str, dict[str, Any]] = field(default_factory=dict)
    runtime_images: list[dict[str, str]] = field(default_factory=list)


@dataclass

@dataclass
class ResolvedReleaseProfile:
    """Named release profile from platform-release.yaml.

    Cluster shape stays in data/clusters/<cluster>/; clusters select one of
    these named profiles by releaseRef/currentReleaseRef/targetReleaseRef.
    """
    name: str
    ocp_version: str
    ocp_minor: str
    ocp_install_core_bundle: str
    cilium_day1_version: str
    cilium_day1_channel: str
    cilium_day1_bundle: str
    gitops_bootstrap_bundle: str
    platform_day2_bundle: str
    platform_day2_profile: str
    cilium_image: str
    cilium_operator_image: str
    clife_image: str
    clife_manifest_bundle: str
    clife_manifest_archive: dict[str, str] = field(default_factory=dict)
    clife_package: ResolvedClifePackage | None = None
    support_status: str = "supported"
    validation_status: str = "validated"


@dataclass
class ResolvedWorkerNetworkAttachment:
    network_ref: str
    guest_default_route: bool = False
    subnet_ref: str = ""
    attachment_name: str = ""
    network_name: str = ""
    subnet_name: str = ""


@dataclass
class ResolvedWorkerDhcpScope:
    """DHCP authority for one Multus worker attachment."""

    profile_ref: str
    attachment_kind: str
    network_ref: str
    attachment_name: str
    network_name: str
    subnet_name: str
    address_authority: str
    server_address: str
    pool_start: str
    pool_end: str
    router: str
    dns_servers: list[str]
    mtu: int | None
    delivery: str
    allocation: str
    emitted_options: list[str] = field(
        default_factory=lambda: list(WORKER_DHCP_EMIT_OPTIONS)
    )
    exposure: str = "private-only"
    local_access_interface: str = ""
    return_path_cidrs: list[str] = field(default_factory=list)

    @property
    def scope_id(self) -> str:
        return f"{self.attachment_kind}:{self.network_name}:{self.subnet_name}"


@dataclass
class ResolvedWorkerIpamScope:
    profile_ref: str
    attachment_kind: str
    network_ref: str
    attachment_name: str
    network_name: str
    subnet_name: str
    address_authority: str
    pool_start: str
    pool_end: str
    guest_prefix: int
    guest_gateway: str
    dns_servers: list[str]
    mtu: int | None
    exposure: str
    dns_source: str = ""
    local_access_gateway: str = ""
    local_access_interface: str = ""
    return_path_cidrs: list[str] = field(default_factory=list)

    @property
    def scope_id(self) -> str:
        return f"{self.attachment_kind}:{self.network_name}:{self.subnet_name}"


@dataclass
class ResolvedHCPCluster:
    name: str
    parent_cluster: str
    ocp_version: str
    network_type: str
    replicas: int
    cluster_networks: list[dict]  # [{cidr, hostPrefix}]
    service_networks: list[dict]  # [{cidr}]
    worker_networks: list[str]  # logical single-NIC worker network names
    worker_replica_default: int = 0
    worker_replica_min: int = 0
    worker_replica_max: int | None = None
    worker_replica_checkpoints: tuple[int, ...] = ()
    worker_network_profile_ref: str = ""
    worker_attachment_kind: str = ""
    worker_allocator: str = ""
    worker_egress_gateway_placement: str = ""
    worker_bgp_owners: tuple[str, ...] = ()
    worker_renderer_authorizations: tuple[str, ...] = ()
    worker_network_attachments: list[ResolvedWorkerNetworkAttachment] = field(default_factory=list)
    worker_dhcp_scopes: list[ResolvedWorkerDhcpScope] = field(default_factory=list)
    publish_worker_dhcp: bool = True
    worker_ipam_scopes: list[ResolvedWorkerIpamScope] = field(default_factory=list)
    resolved_worker_networks: list[ResolvedNetwork] = field(default_factory=list)
    cilium_bgp_enabled: bool = False
    cilium_advertise_lb: bool = False
    cilium_advertise_egress: bool = False
    cilium_advertise_pods: bool = False
    lb_pool_names: list[str] = field(default_factory=list)
    egress_pool_names: list[str] = field(default_factory=list)
    resolved_lb_pools: list[ResolvedPool] = field(default_factory=list)
    resolved_egress_pools: list[ResolvedPool] = field(default_factory=list)
    kube_api_network: ResolvedNetwork | None = None
    kube_api_vip: ResolvedAPIVIP | None = None
    control_plane_ingress_network: ResolvedNetwork | None = None
    control_plane_ingress_load_balancer_vip: ResolvedControlPlaneIngressLoadBalancerVIP | None = None
    application_ingress: ResolvedApplicationIngress | None = None
    timescape_exporter_image_pull: str = ""
    timescape_exporter_activation: str = "automatic"
    timescape_exporter_enrollment: str = ""
    timescape_exporter_metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def uses_multus_workers(self) -> bool:
        return self.worker_attachment_kind == "multus-l2"

    @property
    def uses_central_dhcp(self) -> bool:
        return "render.hcp.worker-dhcp" in self.worker_renderer_authorizations


@dataclass
class ResolvedOCPCluster:
    name: str
    mode: str  # "compact" or "sno"
    features: dict[str, bool]
    ocp_version: str
    base_domain: str
    network_catalog_name: str
    network_type: str
    cilium_day2_migration: bool
    release_ref: str = ""
    current_release_ref: str | None = None
    target_release_ref: str | None = None
    release: ResolvedReleaseProfile | None = None
    current_release: ResolvedReleaseProfile | None = None
    target_release: ResolvedReleaseProfile | None = None
    target_cilium_config_profile: ResolvedCiliumConfigProfile | None = None
    capabilities: list[str] = field(default_factory=list)
    machine_network: ResolvedNetwork | None = None
    api_vip: str | None = None
    ingress_vip: str | None = None
    nodes: list[ResolvedNode] = field(default_factory=list)
    management_interface: ResolvedManagementInterface | None = None
    inventory_mode: str = "runtime"
    registry_trust_policy: str = ""
    image_delivery_mode: str = ""
    image_delivery_scopes: list[str] = field(default_factory=list)
    rendezvous_node: str = ""
    day1_cilium_spec: dict[str, Any] = field(default_factory=dict)
    hosted_clusters: list[ResolvedHCPCluster] = field(default_factory=list)
    ucs_host_networking: ResolvedUCSHostNetworking | None = None
    # Union of all VLANs needed by this cluster
    vlan_union: list[ResolvedNetwork] = field(default_factory=list)
    # Domain-only VLANs (UCS FI uplinks, not on server vNICs)
    domain_vlans: list[ResolvedNetwork] = field(default_factory=list)
    # All kubeAPI networks referenced by hosted clusters
    kube_api_networks: list[ResolvedNetwork] = field(default_factory=list)
    # All tenant networks referenced by hosted clusters
    tenant_networks: list[ResolvedNetwork] = field(default_factory=list)
    parent_networking: ResolvedParentNetworking | None = None
    cilium_config: ResolvedCiliumConfigIntent = field(default_factory=ResolvedCiliumConfigIntent)
    management_clife: ResolvedPreReleaseCLIFEIntent = field(default_factory=ResolvedPreReleaseCLIFEIntent)
    tenant_guest_cni: ResolvedTenantGuestCNIIntent = field(default_factory=ResolvedTenantGuestCNIIntent)
    in_cluster_load_balancer: ResolvedInClusterLoadBalancerIntent = field(
        default_factory=ResolvedInClusterLoadBalancerIntent
    )
    gitops_addons: dict[str, bool] = field(default_factory=dict)
    gitops_addon_selections: dict = field(default_factory=dict)
    observability: dict[str, Any] = field(default_factory=dict)
    storage: dict[str, Any] = field(default_factory=dict)
    hosted_cluster_lifecycle: dict[str, Any] = field(default_factory=dict)
    console_login: dict[str, Any] = field(default_factory=dict)
    retained_hosted_network_claims: dict[str, Any] = field(default_factory=dict)
    local_registry: dict[str, Any] = field(default_factory=dict)
    oc_mirror_inventory: dict[str, Any] = field(default_factory=dict)
    cache_production: dict[str, Any] = field(default_factory=dict)
    cache_readiness: dict[str, Any] = field(default_factory=dict)
    oc_mirror_installation: dict[str, Any] = field(default_factory=dict)
    maintenance_vm_holds: list[str] = field(default_factory=list)


@dataclass
class ResolvedFabric:
    fabric_asn: int
    border_leaf_nodes: list[dict]  # [{nodeId, routerId}] — legacy compat
    physical_domain: str | None = None
    aci: ResolvedAciFabric | None = None


@dataclass
class ResolvedDefaults:
    """Shared infrastructure defaults loaded from data/defaults/<name>.yaml.

    Contains cross-repo environment facts and platform deployment defaults.
    Renderers consume these resolved values instead of carrying deploy-impacting
    defaults in code.
    """
    dns_servers: list[str]
    ntp_servers: list[str]
    registry_url: str
    release_image_repository: str
    image_digest_sources: list[dict]
    ocp_cluster_network: list[dict]
    ocp_service_network: list[str]
    gitops_repo_url: str
    gitops_target_revision: str
    gitops_platform_day2_target_revision_template: str = ""
    gitops_namespaces: dict[str, str] = field(default_factory=dict)
    gitops_sync_waves: dict[str, int] = field(default_factory=dict)
    gitops_network: dict[str, Any] = field(default_factory=dict)
    gitops_argocd: dict[str, Any] = field(default_factory=dict)
    gitops_app_types: dict[str, Any] = field(default_factory=dict)
    gitops_catalog_source_aliases: dict[str, str] = field(default_factory=dict)
    gitops_catalog_sources: dict[str, Any] = field(default_factory=dict)
    gitops_operator_subscriptions: dict[str, Any] = field(default_factory=dict)
    gitops_images: dict[str, Any] = field(default_factory=dict)
    gitops_hosted_cluster: dict[str, Any] = field(default_factory=dict)
    gitops_virtualization: dict[str, Any] = field(default_factory=dict)
    gitops_worker_dhcp: dict[str, Any] = field(default_factory=dict)
    gitops_worker_ipam: dict[str, Any] = field(default_factory=dict)
    gitops_timescape: dict[str, Any] = field(default_factory=dict)
    gitops_storage: dict[str, Any] = field(default_factory=dict)
    gitops_traffic: dict[str, Any] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    day1_compatibility: list[dict[str, str]] = field(default_factory=list)
    input_root: Path | None = None

    @property
    def api_group(self) -> str:
        group = str(self.identity.get("apiGroup") or "")
        if not group:
            raise ValueError("PlatformDefaults spec.identity.apiGroup is required")
        return group

    def api_label(self, suffix: str) -> str:
        return f"{self.api_group}/{suffix}"

    def _ref_value(self, ref: str) -> Any:
        parts = ref.split(".")
        if ref == "registry.url":
            return self.registry_url
        if ref == "gitops.repoURL":
            return self.gitops_repo_url
        if ref == "gitops.targetRevision":
            return self.gitops_target_revision
        if ref == "gitops.platformDay2TargetRevisionTemplate":
            return self.gitops_platform_day2_target_revision_template
        if parts[:2] == ["gitops", "namespaces"] and len(parts) == 3:
            return self.namespace(parts[2])
        if parts[:2] == ["gitops", "syncWaves"] and len(parts) == 3:
            return self.sync_wave(parts[2])
        if parts[:2] == ["gitops", "catalogSourceAliases"] and len(parts) == 3:
            return self.catalog_source_name(parts[2])
        raise ValueError(f"Unsupported PlatformDefaults reference {ref!r}")

    def _resolve_ref_field(self, data: dict[str, Any], value_key: str, ref_key: str, path: str) -> Any:
        value = data.get(value_key)
        ref = data.get(ref_key)
        if value and ref:
            raise ValueError(f"{path} must not set both {value_key} and {ref_key}")
        if ref:
            return self._ref_value(str(ref))
        if value is not None:
            return value
        raise ValueError(f"{path} must set {value_key} or {ref_key}")

    def sync_wave(self, name: str, offset: int = 0) -> int:
        if name not in self.gitops_sync_waves:
            raise ValueError(f"PlatformDefaults spec.gitops.syncWaves.{name} is required")
        return int(self.gitops_sync_waves[name]) + offset

    def namespace(self, name: str) -> str:
        value = self.gitops_namespaces.get(name)
        if not value:
            raise ValueError(f"PlatformDefaults spec.gitops.namespaces.{name} is required")
        return str(value)

    def catalog_source_name(self, alias: str) -> str:
        value = self.gitops_catalog_source_aliases.get(alias)
        if not value:
            raise ValueError(f"PlatformDefaults spec.gitops.catalogSourceAliases.{alias} is required")
        return str(value)

    def catalog_source(self, alias: str) -> dict[str, Any]:
        name = self.catalog_source_name(alias)
        source = self.gitops_catalog_sources.get(name)
        if not isinstance(source, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.catalogSources.{name} is required")
        return {
            "name": name,
            "namespace": self._resolve_ref_field(source, "namespace", "namespaceRef", f"catalogSources.{name}"),
            "image": source["image"],
            "displayName": source["displayName"],
            "publisher": source["publisher"],
            "pollInterval": source["pollInterval"],
        }

    def operator_subscription(self, name: str) -> dict[str, Any]:
        sub = self.gitops_operator_subscriptions.get(name)
        if not isinstance(sub, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.operatorSubscriptions.{name} is required")
        resolved = {key: value for key, value in sub.items() if not key.endswith("Ref")}
        resolved["name"] = name
        resolved["namespace"] = self._resolve_ref_field(sub, "namespace", "namespaceRef", f"operatorSubscriptions.{name}")
        resolved["source"] = self._resolve_ref_field(sub, "source", "sourceRef", f"operatorSubscriptions.{name}")
        resolved["sourceNamespace"] = self._resolve_ref_field(
            sub, "sourceNamespace", "sourceNamespaceRef", f"operatorSubscriptions.{name}"
        )
        for key in ("name", "namespace", "source", "sourceNamespace"):
            if not isinstance(resolved.get(key), str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", resolved[key]):
                raise ValueError(f"operatorSubscriptions.{name}.{key} must be a Kubernetes name")
        if not isinstance(resolved.get("channel"), str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", resolved["channel"]):
            raise ValueError(f"operatorSubscriptions.{name}.channel is required")
        if resolved.get("installPlanApproval") not in {"Manual", "Automatic"}:
            raise ValueError(f"operatorSubscriptions.{name}.installPlanApproval must be Manual or Automatic")
        return resolved

    def platform_day2_target_revision(self, profile: str) -> str:
        template = self.gitops_platform_day2_target_revision_template
        if not template:
            raise ValueError("PlatformDefaults spec.gitops.platformDay2TargetRevisionTemplate is required")
        try:
            return template.format(profile=profile)
        except KeyError as exc:
            raise ValueError(
                "PlatformDefaults spec.gitops.platformDay2TargetRevisionTemplate "
                "may only use the {profile} placeholder"
            ) from exc

    def gitops_app_source(self, app_type: str, *, profile: str | None = None) -> dict[str, str]:
        app = self.gitops_app_types.get(app_type)
        if not isinstance(app, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.appTypes.{app_type} is required")
        source = app.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.appTypes.{app_type}.source is required")

        resolved: dict[str, str] = {
            "repoURL": str(self._resolve_ref_field(source, "repoURL", "repoURLRef", f"appTypes.{app_type}.source")),
        }
        if "chart" in source:
            resolved["chart"] = str(source["chart"])
        if "path" in source:
            resolved["path"] = str(source["path"])

        target_revision = source.get("targetRevision")
        if "targetRevisionRef" in source:
            target_revision = self._ref_value(str(source["targetRevisionRef"]))
        template = source.get("targetRevisionTemplate")
        if "targetRevisionTemplateRef" in source:
            template = self._ref_value(str(source["targetRevisionTemplateRef"]))
        if template:
            if profile is None:
                raise ValueError(f"PlatformDefaults spec.gitops.appTypes.{app_type}.source needs a release profile")
            target_revision = str(template).format(profile=profile)
        if target_revision:
            resolved["targetRevision"] = str(target_revision)
        return resolved

    def gitops_app_type_sync_wave(self, app_type: str, scope: str) -> int:
        app = self.gitops_app_types.get(app_type)
        if not isinstance(app, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.appTypes.{app_type} is required")
        value_key = f"{scope}SyncWave"
        ref_key = f"{scope}SyncWaveRef"
        return int(self._resolve_ref_field(app, value_key, ref_key, f"appTypes.{app_type}"))

    def gitops_image_reference(self, name: str) -> str:
        value = self.gitops_images.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"PlatformDefaults spec.gitops.images.{name} must be an image reference string")
        return value

    def gitops_image_values(self, name: str) -> dict[str, Any]:
        value = self.gitops_images.get(name)
        if not isinstance(value, dict):
            raise ValueError(f"PlatformDefaults spec.gitops.images.{name} must be a mapping")
        return dict(value)

    def gitops_argocd_oci_repository(self) -> dict[str, Any]:
        repo = self.gitops_argocd.get("ociRepository")
        if not isinstance(repo, dict):
            raise ValueError("PlatformDefaults spec.gitops.argocd.ociRepository is required")
        return {
            "name": repo["name"],
            "displayName": repo["displayName"],
            "url": self._resolve_ref_field(repo, "url", "urlRef", "argocd.ociRepository"),
            "enableOCI": bool(repo["enableOCI"]),
            "type": repo["type"],
        }

    def gitops_hosted_kubevirt_worker(self) -> dict[str, Any]:
        kubevirt = self.gitops_hosted_cluster.get("kubevirt")
        if not isinstance(kubevirt, dict):
            raise ValueError("PlatformDefaults spec.gitops.hostedCluster.kubevirt is required")
        compute = kubevirt.get("compute")
        root_volume = kubevirt.get("rootVolume")
        if not isinstance(compute, dict) or not isinstance(root_volume, dict):
            raise ValueError("PlatformDefaults spec.gitops.hostedCluster.kubevirt compute/rootVolume are required")
        return {
            "compute": dict(compute),
            "rootVolume": {
                "type": "Persistent",
                "persistent": {
                    "size": root_volume["size"],
                    "storageClass": root_volume["storageClass"],
                    "accessModes": list(root_volume["accessModes"]),
                    "volumeMode": root_volume["volumeMode"],
                },
            },
        }

    def gitops_hosted_image_content_sources(self) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for scope, entries in [
            ("spec.imageDigestSources", self.image_digest_sources),
            (
                "spec.gitops.hostedCluster.imageContentSources",
                self.gitops_hosted_cluster.get("imageContentSources", []),
            ),
        ]:
            if not isinstance(entries, list):
                raise ValueError(f"PlatformDefaults {scope} must be a list")
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    raise ValueError(f"PlatformDefaults {scope}[{index}] must be a mapping")
                source = entry.get("source")
                mirrors = entry.get("mirrors")
                if not isinstance(source, str) or not source:
                    raise ValueError(f"PlatformDefaults {scope}[{index}].source is required")
                if not isinstance(mirrors, list) or not mirrors or not all(isinstance(m, str) and m for m in mirrors):
                    raise ValueError(f"PlatformDefaults {scope}[{index}].mirrors must be a non-empty string list")
                if source in seen:
                    continue
                seen.add(source)
                sources.append({"source": source, "mirrors": list(mirrors)})
        return sources


@dataclass
class ResolvedPlatform:
    fabric: ResolvedFabric
    catalog_name: str
    all_networks: dict[str, ResolvedNetwork]  # name -> network
    clusters: list[ResolvedOCPCluster]
    release_profiles: dict[str, ResolvedReleaseProfile] = field(default_factory=dict)
    defaults: ResolvedDefaults | None = None
    aci_defaults: ResolvedAciDefaults | None = None
    ucs_defaults: ResolvedUcsDefaults | None = None
    profile_intent: ResolvedPlatformIntent | None = None


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def _load_local_registry(cluster_dir: Path) -> dict:
    """Canonical local-registry input. Absent file means the cluster has none."""
    path = cluster_dir / "local-registry.yaml"
    if not path.exists():
        return {}
    loaded = _load_yaml(path)
    spec = loaded.get("spec", loaded)
    return spec if isinstance(spec, dict) else {}


def _validate_local_registry(prefix: str, registry: dict,
                             expected_consumers: dict[str, str], errors: list[str],
                             *, registry_prefix: str = "") -> None:
    if not registry:
        return
    required = ("namespace", "pullService", "syncService", "port", "workloadName", "listeners")
    for key in required:
        if not registry.get(key):
            errors.append(f"{prefix}: local-registry.yaml spec.{key} is required")
    pvc = registry.get("pvc") if isinstance(registry.get("pvc"), dict) else {}
    for key in ("name", "size", "storageClass"):
        if not pvc.get(key):
            errors.append(f"{prefix}: local-registry.yaml spec.pvc.{key} is required")
    images = registry.get("images") if isinstance(registry.get("images"), dict) else {}
    if not registry_prefix:
        errors.append(f"{prefix}: selected spec.registry.url is required to validate local registry images")
    else:
        configured_registry = registry_prefix.rstrip("/") + "/"
        for key in ("distribution", "accessProxy"):
            if not str(images.get(key, "")).startswith(configured_registry):
                errors.append(f"{prefix}: local-registry.yaml spec.images.{key} must be a digest reference under spec.registry.url")
    if type(registry.get("podUid")) is not int:
        errors.append(f"{prefix}: local-registry.yaml spec.podUid must be an integer")
    consumer = registry.get("consumer") if isinstance(registry.get("consumer"), dict) else {}
    if consumer.get("managementReleaseMode") != "site-recovery":
        errors.append(f"{prefix}: local-registry.yaml consumer.managementReleaseMode must be site-recovery")
    by_role = consumer.get("hostedClustersByRole") or {}
    if not isinstance(by_role, dict):
        errors.append(f"{prefix}: local-registry.yaml consumer.hostedClustersByRole must be a mapping")
    else:
        for role, selected in by_role.items():
            if (not isinstance(selected, dict)
                    or not isinstance(selected.get("pullSecret"), str)
                    or not selected.get("pullSecret")
                    or not selected.get("trustBundle")):
                errors.append(f"{prefix}: local-registry.yaml consumer.hostedClustersByRole.{role} requires pullSecret and trustBundle")
    mirrors = registry.get("mirrors")
    if not isinstance(mirrors, list) or not mirrors:
        errors.append(f"{prefix}: local-registry.yaml spec.mirrors must list source repositories")
    else:
        for item in mirrors:
            if not isinstance(item, dict) or not item.get("source") or not item.get("repository"):
                errors.append(f"{prefix}: every local-registry mirror needs source and repository")
                break
    resources = registry.get("resources") if isinstance(registry.get("resources"), dict) else {}
    empty_dir = registry.get("emptyDir") if isinstance(registry.get("emptyDir"), dict) else {}
    for container in ("distribution", "accessProxy"):
        limits = resources.get(container) if isinstance(resources.get(container), dict) else {}
        if not (limits.get("requests") or {}).get("cpu") or not (limits.get("limits") or {}).get("memory"):
            errors.append(f"{prefix}: local-registry.yaml spec.resources.{container} requests and limits are required")
    for key in ("distributionTmp", "proxyTmp", "proxyCache"):
        if not empty_dir.get(key):
            errors.append(f"{prefix}: local-registry.yaml spec.emptyDir.{key} is required")
    secrets = registry.get("runtimeSecrets") if isinstance(registry.get("runtimeSecrets"), dict) else {}
    for key in ("http", "pullAuth", "syncAuth", "pullTls", "syncTls"):
        if not secrets.get(key):
            errors.append(f"{prefix}: local-registry.yaml spec.runtimeSecrets.{key} is required")


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _input_dir(root: Path, name: str) -> Path:
    """Return the customer-facing input directory, falling back to legacy layout."""
    data_dir = root / "data" / name
    if data_dir.is_dir():
        return data_dir
    return root / name


def _require_closed_object_schemas(value: Any, path: str) -> None:
    """Require every object in a deploy-impacting values schema to be closed."""
    if isinstance(value, dict):
        if value.get("type") == "object" and value.get("additionalProperties") is not False:
            raise ValueError(f"{path} object schema must set additionalProperties=false")
        for key, child in value.items():
            _require_closed_object_schemas(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _require_closed_object_schemas(child, f"{path}[{index}]")


def _load_cilium_config_profiles(root: Path) -> dict[str, ResolvedCiliumConfigProfile]:
    """Load versioned, schema-closed management CiliumConfig overlays."""
    profiles_dir = _input_dir(root, "cilium-config-profiles")
    if not profiles_dir.is_dir():
        return {}
    contract_schema_path = root / "schemas" / "cilium-config-profile-v1.schema.json"
    if not contract_schema_path.is_file():
        raise ValueError("schemas/cilium-config-profile-v1.schema.json is required")
    contract_validator = Draft202012Validator(json.loads(contract_schema_path.read_text()))
    profiles: dict[str, ResolvedCiliumConfigProfile] = {}
    for path in sorted(profiles_dir.glob("*.yaml")):
        source = _load_yaml(path)
        contract_errors = sorted(
            contract_validator.iter_errors(source), key=lambda item: list(item.absolute_path)
        )
        if contract_errors:
            raise ValueError(f"{path}: {contract_errors[0].message}")
        metadata = source["metadata"]
        spec = source["spec"]
        name = str(metadata["name"])
        if name in profiles:
            raise ValueError(f"duplicate CiliumConfigProfile {name!r}")
        values_schema = copy.deepcopy(spec["valuesSchema"])
        _require_closed_object_schemas(
            values_schema, f"CiliumConfigProfile({name}).spec.valuesSchema"
        )
        value_errors = sorted(
            Draft202012Validator(values_schema).iter_errors(spec["values"]),
            key=lambda item: list(item.absolute_path),
        )
        if value_errors:
            raise ValueError(f"{path}: spec.values: {value_errors[0].message}")
        if ("chartValues" in spec) != ("chartValuesSchema" in spec):
            raise ValueError(f"{path}: chartValues and chartValuesSchema must be provided together")
        chart_values = copy.deepcopy(spec.get("chartValues", {}))
        if chart_values:
            chart_schema = copy.deepcopy(spec["chartValuesSchema"])
            _require_closed_object_schemas(
                chart_schema, f"CiliumConfigProfile({name}).spec.chartValuesSchema"
            )
            chart_errors = sorted(
                Draft202012Validator(chart_schema).iter_errors(chart_values),
                key=lambda item: list(item.absolute_path),
            )
            if chart_errors:
                raise ValueError(f"{path}: spec.chartValues: {chart_errors[0].message}")
        compatibility = spec["compatibility"]
        profiles[name] = ResolvedCiliumConfigProfile(
            name=name,
            source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            compatible_cilium_versions=list(compatibility["ciliumVersions"]),
            compatible_openshift_minors=list(compatibility["openshiftMinors"]),
            package_contract_sha256=str(spec["packageContractSha256"]),
            values=copy.deepcopy(spec["values"]),
            values_schema=values_schema,
            chart_values=chart_values,
        )
    return profiles


def _load_clife_package(root: Path, package: dict[str, Any]) -> ResolvedClifePackage | None:
    if not package:
        return None

    contract_ref = package.get("contract", {}) or {}
    receipt_ref = package.get("receipt", {}) or {}
    contract_path = str(contract_ref.get("path", ""))
    source = root / contract_path
    if not contract_path or not source.is_file():
        raise ValueError(f"CLIFE package contract not found: {contract_path!r}")
    contract_bytes = source.read_bytes()
    contract_sha256 = hashlib.sha256(contract_bytes).hexdigest()
    expected_contract_sha256 = str(contract_ref.get("sha256", ""))
    if contract_sha256 != expected_contract_sha256:
        raise ValueError(
            f"CLIFE package contract sha256 mismatch: expected {expected_contract_sha256}, discovered {contract_sha256}"
        )
    contract = yaml.safe_load(contract_bytes)
    if contract.get("kind") != "ClifePackageContract":
        raise ValueError(f"unsupported CLIFE package contract kind in {contract_path}")
    spec = contract.get("spec", {}) or {}
    source_spec = spec.get("source", {}) or {}
    chart_spec = spec.get("chart", {}) or {}
    version_template = str(chart_spec.get("versionTemplate", ""))
    expected_version = (
        version_template
        .replace("{sourceSha12}", str(source_spec.get("sha256", ""))[:12])
        .replace("{contractSha12}", contract_sha256[:12])
    )
    if package.get("repository") != chart_spec.get("repository"):
        raise ValueError("CLIFE package repository does not match its accepted contract")
    if package.get("version") != expected_version:
        raise ValueError(
            f"CLIFE package version {package.get('version')!r} does not match contract-derived {expected_version!r}"
        )
    expected_receipt_path = f"mirror/packages/clife-install/{expected_version}/receipt.yaml"
    if receipt_ref.get("repository") != load_identity(root)["sourceRepositories"]["sysAdmin"]:
        raise ValueError("CLIFE package receipt must be owned by spec.identity.sourceRepositories.sysAdmin")
    if receipt_ref.get("path") != expected_receipt_path:
        raise ValueError(
            f"CLIFE package receipt path must be {expected_receipt_path!r}"
        )
    scenarios = {
        str(item["name"]): copy.deepcopy(item)
        for item in spec.get("embeddedChartVerification", {}).get("scenarios", [])
    }
    runtime_images = copy.deepcopy(spec.get("runtimeImages", []))
    controller = next((item for item in runtime_images if item.get("name") == "clife"), None)
    if controller is None:
        raise ValueError(f"CLIFE package contract {contract_path} has no clife runtime image")
    controller_image = f"{controller['source']}@{controller['digest']}"
    return ResolvedClifePackage(
        repository=str(package.get("repository", "")),
        version=str(package.get("version", "")),
        oci_digest=str(package.get("oci_digest", "")),
        package_sha256=str(package.get("package_sha256", "")),
        contract_path=contract_path,
        contract_sha256=contract_sha256,
        receipt_repository=str(receipt_ref.get("repository", "")),
        receipt_revision=str(receipt_ref.get("revision", "")),
        receipt_path=str(receipt_ref.get("path", "")),
        receipt_sha256=str(receipt_ref.get("sha256", "")),
        controller_image=controller_image,
        scenarios=scenarios,
        runtime_images=runtime_images,
    )



def _load_release_profiles(root: Path) -> dict[str, ResolvedReleaseProfile]:
    """Load named release profiles from platform-release.yaml."""
    release_path = root / "platform-release.yaml"
    if not release_path.exists():
        return {}

    data = _load_yaml(release_path)
    releases = data.get("releases", {}) or {}
    profiles: dict[str, ResolvedReleaseProfile] = {}
    for name, profile in releases.items():
        openshift = profile.get("openshift", {}) or {}
        cilium = profile.get("cilium", {}) or {}
        qualification = profile.get("qualification", {}) or {}
        gitops_bootstrap = profile.get("gitops_bootstrap", {}) or {}
        platform_day2 = profile.get("platform_day2", {}) or {}
        ocp_version = openshift.get("version", "")
        profiles[name] = ResolvedReleaseProfile(
            name=name,
            ocp_version=ocp_version,
            ocp_minor=".".join(ocp_version.split(".")[:2]) if ocp_version else "",
            ocp_install_core_bundle=openshift.get("image_policy_bundle", ""),
            cilium_day1_version=cilium.get("version", ""),
            cilium_day1_channel=cilium.get("channel", ""),
            cilium_day1_bundle=cilium.get("image_policy_bundle", ""),
            gitops_bootstrap_bundle=gitops_bootstrap.get("image_policy_bundle", ""),
            platform_day2_bundle=platform_day2.get("image_policy_bundle", ""),
            platform_day2_profile=f"ocp-{'.'.join(ocp_version.split('.')[:2])}-cilium-{cilium.get('channel', '')}",
            cilium_image=cilium.get("image", ""),
            cilium_operator_image=cilium.get("operator_image", ""),
            clife_image=cilium.get("clife_image", ""),
            clife_manifest_bundle=cilium.get("clife_manifest_bundle", ""),
            clife_manifest_archive=cilium.get("manifestArchive", {}) or {},
            clife_package=_load_clife_package(root, cilium.get("clife_package", {}) or {}),
            support_status=str(qualification.get("supportStatus", "supported")),
            validation_status=str(qualification.get("validationStatus", "validated")),
        )
    return profiles


def has_contract_inputs(root: Path) -> bool:
    """Return true if root looks like a SAIF platform contract root."""
    return (root / "data" / "networks").is_dir() or (root / "networks").is_dir()


def resolved_management_cilium_config_spec(cluster: ResolvedOCPCluster) -> dict[str, Any]:
    """Return the receipt-verified management values plus fixed release identity."""
    if not cluster.release or not cluster.release.clife_package:
        raise ValueError("receipt-bound management CiliumConfig requires a CLIFE package")
    package = cluster.release.clife_package
    scenario = package.scenarios[cluster.management_clife.package_scenario]
    spec = copy.deepcopy(scenario["values"])
    profile = cluster.in_cluster_load_balancer.cilium_config_profile
    if cluster.in_cluster_load_balancer.enabled and not profile:
        raise ValueError("enabled in-cluster load balancer requires a resolved CiliumConfigProfile")
    if profile:
        _merge_mapping(spec, profile.values)
    spec["version"] = cluster.release.cilium_day1_version
    spec["image"] = {
        "cilium": cluster.release.cilium_image,
        "operator": cluster.release.cilium_operator_image,
        "clife": cluster.release.clife_image,
    }
    return spec


def _merge_mapping(target: dict[str, Any], overlay: dict[str, Any]) -> None:
    """Recursively merge explicit deployment intent into a resolved mapping."""
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge_mapping(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


def image_repository(reference: str) -> str:
    """Return the repository portion of a tag- or digest-qualified image."""
    repository = reference.split("@", 1)[0]
    prefix, separator, leaf = repository.rpartition("/")
    if ":" in leaf:
        leaf = leaf.split(":", 1)[0]
    return f"{prefix}{separator}{leaf}"


def resolved_clife_image_content_sources(
    base_sources: list[dict[str, Any]],
    package: ResolvedClifePackage | None,
) -> list[dict[str, Any]]:
    """Merge base mirrors with the receipt-bound CLIFE runtime image closure."""
    merged = {
        item["source"]: list(item["mirrors"])
        for item in base_sources
    }
    if package:
        for image in package.runtime_images:
            source = image_repository(image["source"])
            mirror = image_repository(image["mirror"])
            mirrors = merged.setdefault(source, [])
            if mirror not in mirrors:
                mirrors.append(mirror)
    return [
        {"source": source, "mirrors": mirrors}
        for source, mirrors in merged.items()
    ]


# ---------------------------------------------------------------------------
# Infrastructure defaults — loaded from data/defaults/<name>.yaml
# ---------------------------------------------------------------------------

def _load_defaults(root: Path) -> ResolvedDefaults | None:
    """Load platform defaults from the customer-facing defaults directory.

    Returns None if no defaults file found (backwards compatible).
    """
    defaults_dir = _input_dir(root, "defaults")
    if not defaults_dir.is_dir():
        return None
    defaults_files = sorted(defaults_dir.glob("*.yaml"))
    if not defaults_files:
        return None

    for f in defaults_files:
        data = _load_yaml(f)
        if data.get("kind") == "PlatformDefaults":
            break
    else:
        return None

    spec = data.get("spec", {})
    ocp = spec.get("openshift", {})
    gitops = spec.get("gitops", {})

    return ResolvedDefaults(
        dns_servers=spec.get("dns", {}).get("servers", []),
        ntp_servers=spec.get("ntp", {}).get("servers", []),
        registry_url=spec.get("registry", {}).get("url", ""),
        release_image_repository=spec.get("releaseImageRepository", ""),
        image_digest_sources=spec.get("imageDigestSources", []),
        ocp_cluster_network=ocp.get("clusterNetwork", []),
        ocp_service_network=ocp.get("serviceNetwork", []),
        gitops_repo_url=gitops.get("repoURL", ""),
        gitops_target_revision=gitops.get("targetRevision", ""),
        gitops_platform_day2_target_revision_template=gitops.get("platformDay2TargetRevisionTemplate", ""),
        gitops_namespaces=gitops.get("namespaces", {}),
        gitops_sync_waves=gitops.get("syncWaves", {}),
        gitops_network=gitops.get("network", {}),
        gitops_argocd=gitops.get("argocd", {}),
        gitops_app_types=gitops.get("appTypes", {}),
        gitops_catalog_source_aliases=gitops.get("catalogSourceAliases", {}),
        gitops_catalog_sources=gitops.get("catalogSources", {}),
        gitops_operator_subscriptions=gitops.get("operatorSubscriptions", {}),
        gitops_images=gitops.get("images", {}),
        gitops_hosted_cluster=gitops.get("hostedCluster", {}),
        gitops_virtualization=gitops.get("virtualization", {}),
        gitops_worker_dhcp=gitops.get("workerDhcp", {}),
        gitops_worker_ipam=gitops.get("workerIpam", {}),
        gitops_timescape=gitops.get("timescape", {}),
        gitops_storage=gitops.get("storage", {}),
        gitops_traffic=gitops.get("traffic", {}),
        identity=spec.get("identity") or {},
        day1_compatibility=spec.get("day1Compatibility", []),
        input_root=root,
    )


def _load_aci_defaults(root: Path) -> ResolvedAciDefaults | None:
    """Load ACI desired-state values from the customer-facing defaults directory."""
    aci_path = _input_dir(root, "defaults") / "aci.yaml"
    if not aci_path.exists():
        return None

    data = _load_yaml(aci_path)
    if data.get("kind") != "AciDefaults":
        return None

    spec = data.get("spec", {})

    bfd = spec.get("bfdPolicy", {})
    bfd_policy = ResolvedAciBfdPolicy(
        name=bfd["name"],
        description=bfd.get("description", ""),
        subinterface_optimization=bfd.get("subinterfaceOptimization", False),
        detection_multiplier=bfd.get("detectionMultiplier", 10),
        echo_admin_state=bfd.get("echoAdminState", True),
        echo_rx_interval=bfd.get("echoRxInterval", 50),
        min_rx_interval=bfd.get("minRxInterval", 300),
        min_tx_interval=bfd.get("minTxInterval", 300),
    )

    bp = spec.get("bgpBestPathPolicy", {})
    bgp_bp = ResolvedAciBgpBestPathPolicy(
        name=bp["name"],
        ignore_igp_metric=bp.get("ignoreIgpMetric", True),
    )

    pp = spec.get("bgpPeerPrefixPolicy", {})
    bgp_pp = ResolvedAciBgpPeerPrefixPolicy(
        name=pp["name"],
        description=pp.get("description", ""),
        action=pp.get("action", "reject"),
        threshold=pp.get("threshold", 75),
        max_prefixes=pp.get("maxPrefixes", 500),
    )

    ospf_raw = spec.get("ospfInterfacePolicy")
    ospf_policy = None
    if ospf_raw:
        ospf_policy = ResolvedAciOspfInterfacePolicy(
            name=ospf_raw["name"],
            description=ospf_raw.get("description", ""),
            network_type=ospf_raw.get("networkType", "p2p"),
            mtu_ignore=ospf_raw.get("mtuIgnore", True),
            advertise_subnet=ospf_raw.get("advertiseSubnet", True),
        )

    filters = [
        ResolvedAciFilter(name=f["name"], entries=f.get("entries", []))
        for f in spec.get("filters", [])
    ]

    subjects = [
        ResolvedAciContractSubject(name=s["name"], filters=s.get("filters", []))
        for s in spec.get("contractSubjects", [])
    ]

    rc_raw = spec.get("routeControl")
    route_control = None
    if rc_raw:
        route_control = ResolvedAciRouteControl(
            import_enforcement=rc_raw.get("importEnforcement", True),
            export_enforcement=rc_raw.get("exportEnforcement", True),
            route_map_type=rc_raw.get("routeMapType", "global"),
            default_action=rc_raw.get("defaultAction", "deny"),
        )

    return ResolvedAciDefaults(
        vrf_name=spec["vrfName"],
        mtu=spec["mtu"],
        bfd_policy=bfd_policy,
        bgp_best_path_policy=bgp_bp,
        bgp_peer_prefix_policy=bgp_pp,
        ospf_interface_policy=ospf_policy,
        filters=filters,
        contract_subjects=subjects,
        contract_scope=spec.get("contractScope", "global"),
        rendered_contract_subject=str(spec.get("renderedContractSubject") or ""),
        endpoint_management_contract=spec.get("endpointManagementContract"),
        route_control=route_control,
    )


def _required_lan_policy_template(spec: dict) -> str:
    template = spec.get("lanPolicyTemplate", spec.get("lanPolicy"))
    if not isinstance(template, str) or "{node_name}" not in template:
        raise ValueError("UCS defaults spec.lanPolicyTemplate is required and must include {node_name}")
    return template


def _load_ucs_defaults(root: Path) -> ResolvedUcsDefaults | None:
    """Load UCS target-repo conventions from the customer-facing defaults directory."""
    ucs_path = _input_dir(root, "defaults") / "ucs.yaml"
    if not ucs_path.exists():
        return None

    data = _load_yaml(ucs_path)
    if data.get("kind") != "UcsDefaults":
        return None

    spec = data.get("spec", {})
    return ResolvedUcsDefaults(
        org=spec["org"],
        dom_vlan_policy=spec["domainVlanPolicy"],
        srv_vlan_policy=spec["serverVlanPolicy"],
        multicast_policy=spec["multicastPolicy"],
        mgmt_eng_policy=spec["managementEngPolicy"],
        tenant_eng_policy=spec["tenantEngPolicy"],
        lan_policy_template=_required_lan_policy_template(spec),
        mac_pool=spec["macPool"],
        eth_adapter=spec["ethernetAdapterPolicy"],
        eth_qos=spec["ethernetQosPolicy"],
        eth_ctrl=spec["ethernetNetworkControlPolicy"],
    )


# ---------------------------------------------------------------------------
# Resolver core
# ---------------------------------------------------------------------------

def _derive_svi_peers(
    peering_subnet: str, leaf_ids: list[int]
) -> tuple[list[ResolvedSVIPeer], str | None]:
    """Derive SVI peer IPs and legacy shared gateway from peering subnet.

    First usable IP -> first router-source ID, second -> second.
    Third usable IP -> shared gateway, unless aci.floatingSvi overrides it.
    Returns (peers, ip_shared).
    """
    net = ipaddress.IPv4Network(peering_subnet, strict=False)
    hosts = list(net.hosts())
    prefix_len = net.prefixlen
    peers = []
    for i, leaf_id in enumerate(leaf_ids):
        if i >= len(hosts):
            break
        peers.append(ResolvedSVIPeer(
            ip=f"{hosts[i]}/{prefix_len}",
            leaf_node_id=leaf_id,
        ))
    shared_idx = len(leaf_ids)
    ip_shared = f"{hosts[shared_idx]}/{prefix_len}" if shared_idx < len(hosts) else None
    return peers, ip_shared


def _resolve_floating_svi(aci_spec: dict, derived_ip_shared: str | None) -> tuple[str | None, str | None]:
    """Resolve explicit floating-SVI addresses from network catalog inventory."""
    floating_svi = aci_spec.get("floatingSvi", {}) or {}
    ip_shared = floating_svi.get("ipShared", derived_ip_shared)
    path_floating_ip = floating_svi.get("pathFloatingIp", floating_svi.get("floatingIp", derived_ip_shared))
    return ip_shared, path_floating_ip


def _resolve_dhcp_authority(network_name: str, raw: Any) -> ResolvedWorkerPeering:
    """Resolve an explicit DHCP endpoint and pool owned by network input."""
    if raw in (None, False):
        return ResolvedWorkerPeering()
    if raw is True:
        return ResolvedWorkerPeering(dhcp=True)
    if not isinstance(raw, dict):
        raise ValueError(f"network {network_name}: dhcp must be a boolean or mapping")
    pool = raw.get("pool", {}) or {}
    if not isinstance(pool, dict):
        raise ValueError(f"network {network_name}: dhcp.pool must be a mapping")
    options = raw.get("options", {}) or {}
    if not isinstance(options, dict):
        raise ValueError(f"network {network_name}: dhcp.options must be a mapping")
    dns_source = options.get("dns", "")
    if dns_source not in {"", "platform-defaults"}:
        raise ValueError(
            f"network {network_name}: dhcp.options.dns must be platform-defaults or omitted"
        )
    emitted_options = options.get("emit")
    if emitted_options is None:
        emitted_options = list(WORKER_DHCP_EMIT_OPTIONS)
    if not isinstance(emitted_options, list) or not all(
        isinstance(item, str) for item in emitted_options
    ):
        raise ValueError(f"network {network_name}: dhcp.options.emit must be a list of option names")
    unsupported_options = sorted(set(emitted_options) - set(WORKER_DHCP_EMIT_OPTIONS))
    if unsupported_options:
        raise ValueError(
            f"network {network_name}: dhcp.options.emit contains unsupported values: "
            f"{', '.join(unsupported_options)}"
        )
    if len(emitted_options) != len(set(emitted_options)):
        raise ValueError(f"network {network_name}: dhcp.options.emit must not contain duplicates")
    return ResolvedWorkerPeering(
        dhcp=bool(raw.get("enabled", True)),
        server_address=str(raw.get("serverAddress", "")),
        pool_start=str(pool.get("start", "")),
        pool_end=str(pool.get("end", "")),
        router=str(options.get("router", "")),
        dns_from_defaults=dns_source == "platform-defaults",
        emitted_options=list(emitted_options),
    )


def _resolve_ipam_authority(network_name: str, raw: Any) -> ResolvedWorkerIpam:
    """Resolve the customer-operated admission allocation pool and guest delivery."""
    if raw in (None, False):
        return ResolvedWorkerIpam()
    if not isinstance(raw, dict):
        raise ValueError(f"network {network_name}: ipam must be a mapping")
    pool = raw.get("pool", {}) or {}
    if not isinstance(pool, dict):
        raise ValueError(f"network {network_name}: ipam.pool must be a mapping")
    guest = raw.get("guest", {}) or {}
    if not isinstance(guest, dict):
        raise ValueError(f"network {network_name}: ipam.guest must be a mapping")
    dns_source = guest.get("dns", "")
    if dns_source not in {"", "platform-defaults", "management-coredns"}:
        raise ValueError(
            f"network {network_name}: ipam.guest.dns must be platform-defaults, management-coredns or omitted"
        )
    return ResolvedWorkerIpam(
        enabled=bool(raw.get("enabled", True)),
        pool_start=str(pool.get("start", "")),
        pool_end=str(pool.get("end", "")),
        guest_prefix=int(guest.get("prefix", 32)),
        guest_gateway=str(guest.get("gateway", "")),
        dns_from_defaults=dns_source == "platform-defaults",
        dns_source=dns_source,
    )


def _resolve_network_subnet(network_name: str, field_path: str,
                            subnet_spec: Any) -> tuple[str | None, ResolvedWorkerPeering]:
    """Resolve a canonical network subnet and optional DHCP authority."""
    if subnet_spec is None:
        return None, ResolvedWorkerPeering()
    if not isinstance(subnet_spec, dict):
        raise ValueError(
            f"network {network_name}: {field_path} must be an object with cidr and optional dhcp"
        )
    cidr = subnet_spec.get("cidr")
    if not cidr:
        raise ValueError(f"network {network_name}: {field_path}.cidr is required")
    return str(cidr), _resolve_dhcp_authority(network_name, subnet_spec.get("dhcp"))


def _resolve_catalog(catalog_data: dict, border_leaves: list[dict]) -> dict[str, ResolvedNetwork]:
    """Resolve all networks from a NetworkCatalog spec into ResolvedNetwork objects."""
    spec = catalog_data.get("spec", {})
    networks: dict[str, ResolvedNetwork] = {}

    # Management networks
    for name, net in spec.get("managementNetworks", {}).items():
        networks[name] = ResolvedNetwork(
            name=name, category="management", vlan=net["vlan"],
            description=net.get("description", ""),
            cidr=net.get("cidr"), gateway=net.get("gateway"),
            mtu=net.get("mtu"), aci_attachment=copy.deepcopy(net.get("aciAttachment")),
        )

    # Domain VLANs (UCS FI-only, not on server vNICs)
    for name, net in spec.get("domainVLANs", {}).items():
        networks[name] = ResolvedNetwork(
            name=name, category="domain", vlan=net["vlan"],
            description=net.get("description", ""),
            native=net.get("native", False),
        )

    # KubeAPI networks
    for name, net in spec.get("kubeAPINetworks", {}).items():
        peering, _worker_peering = _resolve_network_subnet(
            name, "peeringSubnet", net.get("peeringSubnet")
        )
        aci_spec = net.get("aci", {})
        aci_tenant = aci_spec.get("tenant")
        aci_router_ids = {int(k): v for k, v in aci_spec.get("routerIds", {}).items()}
        leaf_ids = sorted(aci_router_ids.keys())
        svi, derived_ip_shared = _derive_svi_peers(peering, leaf_ids) if peering else ([], None)
        ip_shared, path_floating_ip = _resolve_floating_svi(aci_spec, derived_ip_shared)

        aci_net = ResolvedAciNetwork(
            tenant=aci_tenant,
            router_ids=aci_router_ids,
            svi_peers=svi,
            floating_ip=ip_shared,
            path_floating_ip=path_floating_ip,
        ) if aci_tenant or aci_router_ids else None

        rn = ResolvedNetwork(
            name=name, category="kubeAPI", vlan=net["vlan"], mtu=net.get("mtu"),
            description=net.get("description", ""),
            peering_subnet=peering,
            cilium_asn=net.get("bgp", {}).get("ciliumASN"),
            aci=aci_net,
        )
        for pool in net.get("hcpManagementServices", []):
            rp = ResolvedHCPManagementServicePool(name=pool["name"], cidr=pool["cidr"], owning_network=name)
            rn.hcp_management_services.append(rp)
        for vip in net.get("apiVIPs", []):
            rv = ResolvedAPIVIP(name=vip["name"], cidr=vip["cidr"], owning_network=name)
            rn.api_vips.append(rv)
        for vip in net.get("controlPlaneIngressLoadBalancerVIPs", []):
            rv = ResolvedControlPlaneIngressLoadBalancerVIP(
                name=vip["name"],
                cidr=vip["cidr"],
                owning_network=name,
            )
            rn.control_plane_ingress_load_balancer_vips.append(rv)
        networks[name] = rn

    # Tenant networks
    for name, net in spec.get("tenantNetworks", {}).items():
        worker_subnets = net.get("subnets", {}) or {}
        if not isinstance(worker_subnets, dict):
            raise ValueError(f"network {name}: subnets must be a mapping")
        peering, worker_peering = _resolve_network_subnet(
            name, "subnets.workers", worker_subnets.get("workers")
        )
        aci_spec = net.get("aci", {})
        aci_tenant = aci_spec.get("tenant")
        aci_router_ids = {int(k): v for k, v in aci_spec.get("routerIds", {}).items()}
        leaf_ids = sorted(aci_router_ids.keys())
        svi, derived_ip_shared = _derive_svi_peers(peering, leaf_ids) if peering else ([], None)
        ip_shared, path_floating_ip = _resolve_floating_svi(aci_spec, derived_ip_shared)

        aci_net = ResolvedAciNetwork(
            tenant=aci_tenant,
            router_ids=aci_router_ids,
            svi_peers=svi,
            floating_ip=ip_shared,
            path_floating_ip=path_floating_ip,
        ) if aci_tenant or aci_router_ids else None

        rn = ResolvedNetwork(
            name=name, category="tenant", vlan=net["vlan"], mtu=net.get("mtu"),
            description=net.get("description", ""),
            tenant_supernet=net.get("tenantSupernet"),
            peering_subnet=peering,
            cilium_asn=net.get("bgp", {}).get("ciliumASN"),
            aci=aci_net,
            worker_peering=worker_peering,
        )
        for pool in net.get("pools", {}).get("loadBalancer", []):
            allocation = pool.get("allocation") or {}
            if allocation and not isinstance(allocation, dict):
                raise ValueError(f"network {name}: loadBalancer pool {pool.get('name')} allocation must be a mapping")
            rn.pools_lb.append(ResolvedPool(
                name=pool["name"], cidr=pool["cidr"],
                kind="loadBalancer", owning_network=name,
                allocation_start=str(allocation.get("start") or ""),
                allocation_stop=str(allocation.get("stop") or ""),
            ))
        for pool in net.get("pools", {}).get("egress", []):
            rn.pools_egress.append(ResolvedPool(
                name=pool["name"], cidr=pool["cidr"],
                kind="egress", owning_network=name,
            ))
        networks[name] = rn

    # Fabric transit networks — ACI-routed OSPF transit, no endpoint pools
    for name, net in spec.get("fabricNetworks", {}).items():
        aci_spec = net.get("aci", {})
        aci_tenant = aci_spec.get("tenant")
        aci_router_ids = {int(k): v for k, v in aci_spec.get("routerIds", {}).items()}
        ospf_spec = net.get("ospf", {})
        node_interface_ips = {int(k): v for k, v in ospf_spec.get("nodeInterfaceIPs", {}).items()}
        peering = next(iter(node_interface_ips.values()), None)
        svi = [
            ResolvedSVIPeer(ip=ip, leaf_node_id=node_id)
            for node_id, ip in sorted(node_interface_ips.items())
        ]
        ospf = ResolvedOspfConfig(
            area_id=ospf_spec["areaId"],
            area_type=ospf_spec.get("areaType", "regular"),
            interface_policy=ospf_spec.get("interfacePolicy", ""),
            export_route_control_enforcement=ospf_spec.get("exportRouteControlEnforcement", False),
            default_routes=ospf_spec.get("defaultRoutes", []),
            interface_port=ospf_spec.get("interfacePort"),
            node_interface_ips=node_interface_ips,
        ) if ospf_spec else None

        aci_net = ResolvedAciNetwork(
            tenant=aci_tenant,
            router_ids=aci_router_ids,
            svi_peers=svi,
            floating_ip=None,
            ospf=ospf,
        ) if aci_tenant or aci_router_ids else None

        networks[name] = ResolvedNetwork(
            name=name, category="fabricTransit", vlan=net["vlan"],
            description=net.get("description", ""),
            peering_subnet=peering,
            aci=aci_net,
        )

    # Endpoint networks — BD/EPG with VMM domain for test VMs, no peering/L3Out
    for name, net in spec.get("endpointNetworks", {}).items():
        aci_spec = net.get("aci", {})
        aci_tenant = aci_spec.get("tenant")
        vmm_domain = aci_spec.get("vmmDomain")

        aci_net = ResolvedAciNetwork(
            tenant=aci_tenant,
        ) if aci_tenant else None

        vlan_val = net.get("vlan")
        vlan = 0 if vlan_val == "auto" else int(vlan_val)

        rn = ResolvedNetwork(
            name=name, category="endpoint", vlan=vlan,
            description=net.get("description", ""),
            cidr=net.get("cidr"),
            gateway=net.get("gateway"),
            aci=aci_net,
            vmm_domain=vmm_domain,
        )
        networks[name] = rn

    # Derive import_destinations for Cilium BGP import policies from the same
    # canonical tenant route classes consumed by ACI route-control rendering.
    # Exact routes omit minLen/maxLen; HCP management-service pools intentionally
    # accept only /32 specifics inside the allocated /28 route class.
    for net in networks.values():
        if net.category not in {"tenant", "kubeAPI"} or not net.aci_tenant:
            continue

        for ep in networks.values():
            if ep.category == "endpoint" and ep.aci_tenant == net.aci_tenant and ep.cidr:
                endpoint_dest = {
                    "cidr": ep.cidr,
                    "description": ep.description or ep.name,
                }
                net.import_destinations.append(endpoint_dest)
                net.egress_destinations.append(endpoint_dest)

        if net.category == "kubeAPI":
            for worker in networks.values():
                if worker.category == "tenant" and worker.aci_tenant == net.aci_tenant and worker.peering_subnet:
                    net.import_destinations.append({
                        "cidr": worker.peering_subnet,
                        "description": f"{worker.name} worker peering route class",
                    })

        if net.category == "tenant":
            for mgmt in networks.values():
                if mgmt.category == "kubeAPI" and mgmt.aci_tenant == net.aci_tenant:
                    for pool in mgmt.hcp_management_services:
                        net.import_destinations.append({
                            "cidr": pool.cidr,
                            "description": f"{pool.name} host routes",
                            "minLen": 32,
                            "maxLen": 32,
                        })
            for transit in networks.values():
                if transit.category == "fabricTransit" and transit.aci_tenant == net.aci_tenant and transit.aci and transit.aci.ospf:
                    for prefix in transit.aci.ospf.default_routes:
                        net.import_destinations.append({
                            "cidr": prefix,
                            "description": f"{transit.name} default route",
                        })

    for network in networks.values():
        if network.mtu is not None and (type(network.mtu) is not int or not 576 <= network.mtu <= 9000):
            raise ValueError(f"network {network.name}: MTU must be an integer between 576 and 9000")
    return networks


def _find_api_vip(networks: dict[str, ResolvedNetwork], vip_name: str) -> tuple[ResolvedNetwork, ResolvedAPIVIP] | None:
    """Find a named API VIP across all kubeAPI networks."""
    for net in networks.values():
        if net.category != "kubeAPI":
            continue
        for vip in net.api_vips:
            if vip.name == vip_name:
                return net, vip
    return None


def _find_control_plane_ingress_load_balancer_vip(
    networks: dict[str, ResolvedNetwork],
    vip_name: str,
) -> tuple[ResolvedNetwork, ResolvedControlPlaneIngressLoadBalancerVIP] | None:
    """Find a named control-plane ingress LoadBalancer VIP across kubeAPI networks."""
    for net in networks.values():
        if net.category != "kubeAPI":
            continue
        for vip in net.control_plane_ingress_load_balancer_vips:
            if vip.name == vip_name:
                return net, vip
    return None


def _find_pool(networks: dict[str, ResolvedNetwork], pool_name: str, kind: str) -> ResolvedPool | None:
    """Find a named pool across all tenant networks."""
    for net in networks.values():
        if net.category != "tenant":
            continue
        pools = net.pools_lb if kind == "loadBalancer" else net.pools_egress
        for pool in pools:
            if pool.name == pool_name:
                return pool
    return None


def _dhcp_authority_errors(owner: str, cidr: str,
                           dhcp: ResolvedWorkerPeering,
                           reserved: set[ipaddress.IPv4Address] | None = None) -> list[str]:
    """Validate one authored same-domain DHCP endpoint and pool."""
    if not dhcp.dhcp:
        return []
    errors: list[str] = []
    if not dhcp.server_address or not dhcp.pool_start or not dhcp.pool_end:
        return [f"{owner}: DHCP requires serverAddress and pool.start/end"]
    try:
        authority = ipaddress.IPv4Network(cidr, strict=False)
        server = ipaddress.IPv4Address(dhcp.server_address)
        start = ipaddress.IPv4Address(dhcp.pool_start)
        end = ipaddress.IPv4Address(dhcp.pool_end)
    except ValueError as exc:
        return [f"{owner}: invalid DHCP authority: {exc}"]
    if server not in authority or start not in authority or end not in authority:
        errors.append(f"{owner}: DHCP endpoint and pool must be inside {authority}")
    unusable = {authority.network_address, authority.broadcast_address} | (reserved or set())
    if server in unusable:
        errors.append(f"{owner}: DHCP serverAddress must be an unreserved host in {authority}")
    if start in unusable or end in unusable:
        errors.append(f"{owner}: DHCP pool bounds must be unreserved hosts in {authority}")
    if start > end:
        errors.append(f"{owner}: DHCP pool.start must be <= pool.end")
    if start <= server <= end:
        errors.append(f"{owner}: DHCP pool must not include serverAddress")
    if any(start <= address <= end for address in (reserved or set())):
        errors.append(f"{owner}: DHCP pool overlaps a reserved network address")
    return errors



def _host_selects_ingress(host: object, wildcard: object) -> bool:
    if not isinstance(host, str) or not isinstance(wildcard, str) or not host or not wildcard:
        return False
    if wildcard.startswith("*."):
        suffix = wildcard[1:]
        return host.endswith(suffix) and host != suffix.lstrip(".")
    return host == wildcard


def _validate_conformance_allocation(platform: ResolvedPlatform, identity: dict[str, Any]) -> list[str]:
    """Identity copies of allocated addresses must match the inventory that owns them.

    Cluster workerPlacement.eligibleManagementNodes owns the management node set.
    tenantA.egressCIDR owns the production egress address. currentTargetOverride.ilb
    owns the ILB VIP. The hosted applicationIngress selected by consoleHost owns
    the console address. spec.identity repeats those values for collectors that
    do not load the cluster graph; a mismatch is an error.
    """
    conformance = identity.get("conformance")
    if not isinstance(conformance, dict):
        return ["spec.identity.conformance is required"]
    host = conformance.get("consoleHost")
    address = conformance.get("consoleAddress")
    matches = [
        (cluster.name, hcp.name, hcp.application_ingress.vip)
        for cluster in platform.clusters
        for hcp in cluster.hosted_clusters
        if hcp.application_ingress and _host_selects_ingress(host, hcp.application_ingress.dns_wildcard)
    ]
    if len(matches) != 1 or matches[0][2] != address:
        return [
            "spec.identity.conformance.consoleAddress must equal the applicationIngress.vip "
            "of the hosted cluster selected by spec.identity.conformance.consoleHost"
        ]
    return []



def _ipam_authority_errors(owner: str, cidr: str,
                           ipam: ResolvedWorkerIpam) -> list[str]:
    """Validate one customer-operated admission allocation pool."""
    if not ipam.enabled:
        return []
    if not ipam.pool_start or not ipam.pool_end or not ipam.guest_gateway:
        return [f"{owner}: IPAM requires pool.start/end and guest.gateway"]
    errors: list[str] = []
    try:
        authority = ipaddress.IPv4Network(cidr, strict=False)
        start = ipaddress.IPv4Address(ipam.pool_start)
        end = ipaddress.IPv4Address(ipam.pool_end)
        ipaddress.IPv4Address(ipam.guest_gateway)
    except ValueError as exc:
        return [f"{owner}: invalid IPAM authority: {exc}"]
    if start not in authority or end not in authority:
        errors.append(f"{owner}: IPAM pool must be inside {authority}")
    if start in {authority.network_address, authority.broadcast_address} or end in {
        authority.network_address, authority.broadcast_address
    }:
        errors.append(f"{owner}: IPAM pool bounds must be usable hosts in {authority}")
    if start > end:
        errors.append(f"{owner}: IPAM pool.start must be <= pool.end")
    if ipam.guest_prefix != 32:
        errors.append(f"{owner}: admission address prefix must be 32")
    return errors


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_defaults(defaults: ResolvedDefaults, *, day2: bool = True, registry_required: bool = True, identity_features: tuple[str, ...] = ()) -> list[str]:
    errors: list[str] = []
    prefix = "PlatformDefaults"
    for message in identity_errors(defaults.identity, features=identity_features):
        errors.append(f"{prefix}: {message}")
    if registry_required and not defaults.registry_url:
        errors.append(f"{prefix}: selected mirrored/Day2 operation requires spec.registry.url")
    if not defaults.release_image_repository:
        errors.append(f"{prefix}: spec.releaseImageRepository is required")
    policy = defaults.day1_compatibility
    seen = set()
    if not isinstance(policy, list) or not policy:
        errors.append(f"{prefix}: spec.day1Compatibility must declare reviewed OCP/Cilium pairs")
    else:
        for row in policy:
            if not isinstance(row, dict):
                errors.append(f"{prefix}: day1Compatibility rows must be mappings")
                continue
            pair = (row.get("openshiftMinor"), row.get("ciliumChannel"))
            if (any(not isinstance(v, str) or not re.fullmatch(r"\d+\.\d+", v) for v in pair)
                    or not row.get("evidence") or pair in seen):
                errors.append(f"{prefix}: day1Compatibility requires unique minor/channel pairs and evidence")
                continue
            seen.add(pair)
    if defaults.gitops_repo_url or defaults.gitops_target_revision or "openshift-gitops-operator" in defaults.gitops_operator_subscriptions:
        if not defaults.gitops_repo_url or not defaults.gitops_target_revision:
            errors.append(f"{prefix}: selected GitOps bootstrap requires repoURL and targetRevision")
        try:
            defaults.operator_subscription("openshift-gitops-operator")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    if not day2:
        return errors
    if not defaults.gitops_repo_url:
        errors.append(f"{prefix}: spec.gitops.repoURL is required")
    if not defaults.gitops_target_revision:
        errors.append(f"{prefix}: spec.gitops.targetRevision is required")

    required_namespaces = [
        "cnv", "gitops", "hypershift", "marketplace", "mce", "nmstate",
        "hcpWorkerDhcp", "timescape", "portworx", "storage",
        "storageReadiness",
    ]
    required_waves = [
        "argocdConfig", "platformIdmsManagement", "namespace", "operatorGroup",
        "subscription", "nmstate", "networkAttachmentDefinition",
        "workerDhcp", "workerIpam", "bgp", "isovalentVectorExporter", "bgpAdvertisement", "hcpOperatorsApplication",
        "hcpOperandsApplication", "parentNetworkApplication",
        "hostedClustersApplication",
        "hostedCluster", "nodePool", "argocdClusterSecret",
        "tenantCatalogsApplication", "tenantSealedSecretsApplication",
        "guestCniApplication", "tenantObservabilityApplication",
        "tenantApplication", "tenantBgp", "tenantLoadBalancerIpam",
        "tenantEgressGateway",
        "storageHostConfig", "storageReadiness", "storageOperator", "storageCluster",
    ]
    for name in required_namespaces:
        try:
            defaults.namespace(name)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
    for name in required_waves:
        try:
            defaults.sync_wave(name)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")

    try:
        defaults.platform_day2_target_revision("ocp-0.0-cilium-0.0")
        defaults.gitops_app_source("platformIdms", profile="ocp-0.0-cilium-0.0")
        defaults.gitops_app_type_sync_wave("platformIdms", "management")
        defaults.gitops_hosted_image_content_sources()
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")

    for alias in ["certified", "redhat"]:
        try:
            catalog_source = defaults.catalog_source(alias)
        except (KeyError, ValueError) as exc:
            errors.append(f"{prefix}: {exc}")
            continue
        if defaults.registry_url and not str(catalog_source["image"]).startswith(f"{defaults.registry_url}/"):
            errors.append(f"{prefix}: gitops.catalogSources.{catalog_source['name']}.image must use spec.registry.url")

    for name in defaults.gitops_operator_subscriptions:
        try:
            defaults.operator_subscription(name)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")

    try:
        ose_tools = defaults.gitops_image_reference("oseTools")
        if defaults.registry_url and not ose_tools.startswith(f"{defaults.registry_url}/"):
            errors.append(f"{prefix}: spec.gitops.images.oseTools must use spec.registry.url")
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    try:
        vm_disk = defaults.gitops_image_reference("kubevirtRhelContainerDisk")
        if defaults.registry_url and not vm_disk.startswith(f"{defaults.registry_url}/"):
            errors.append(f"{prefix}: spec.gitops.images.kubevirtRhelContainerDisk must use spec.registry.url")
    except ValueError as exc:
        errors.append(f"{prefix}: {exc}")
    worker_dhcp = defaults.gitops_worker_dhcp
    for key in [
        "serverName",
        "cloudInitSecretName",
        "observedLeaseFile",
        "serverInterfaceNameTemplate",
        "leaseSeconds",
        "image",
    ]:
        if not worker_dhcp.get(key):
            errors.append(f"{prefix}: spec.gitops.workerDhcp.{key} is required")
    interface_template = str(worker_dhcp.get("serverInterfaceNameTemplate", ""))
    if interface_template:
        try:
            test_iface = interface_template.format(index=0)
        except (KeyError, IndexError, ValueError) as exc:
            errors.append(f"{prefix}: spec.gitops.workerDhcp.serverInterfaceNameTemplate is invalid: {exc}")
        else:
            if not test_iface or "{" in test_iface or "}" in test_iface:
                errors.append(f"{prefix}: spec.gitops.workerDhcp.serverInterfaceNameTemplate must render a concrete interface name")
    try:
        lease_seconds = int(worker_dhcp.get("leaseSeconds", 0) or 0)
    except (TypeError, ValueError) as exc:
        errors.append(f"{prefix}: spec.gitops.workerDhcp.leaseSeconds is invalid: {exc}")
    else:
        if lease_seconds < 60:
            errors.append(f"{prefix}: spec.gitops.workerDhcp.leaseSeconds must be at least 60")

    image = str(worker_dhcp.get("image", ""))
    if image and (not image.startswith(f"{defaults.registry_url}/") or "@sha256:" not in image):
        errors.append(
            f"{prefix}: spec.gitops.workerDhcp.image must use the configured registry and an immutable digest"
        )
    lease_storage = worker_dhcp.get("leaseStorage")
    if not isinstance(lease_storage, dict):
        errors.append(f"{prefix}: spec.gitops.workerDhcp.leaseStorage is required")
    else:
        qualified = {
            "storageClassName": "px-csi-replicated-default",
            "size": "2Gi",
            "accessMode": "ReadWriteOnce",
            "volumeMode": "Filesystem",
            "runStrategy": "Manual",
        }
        for key, expected in qualified.items():
            if lease_storage.get(key) != expected:
                errors.append(
                    f"{prefix}: spec.gitops.workerDhcp.leaseStorage.{key} "
                    f"must be the qualified {expected!r} contract"
                )
        suffix = lease_storage.get("claimNameSuffix")
        if not isinstance(suffix, str) or not re.fullmatch(r"-[a-z0-9-]+", suffix):
            errors.append(f"{prefix}: workerDhcp lease claimNameSuffix must be a DNS name suffix")
        serial = lease_storage.get("diskSerial")
        if not isinstance(serial, str) or not re.fullmatch(r"[A-Za-z0-9]{1,20}", serial):
            errors.append(f"{prefix}: workerDhcp lease diskSerial must be 1-20 alphanumeric characters")
        label = lease_storage.get("filesystemLabel")
        if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,16}", label):
            errors.append(f"{prefix}: workerDhcp filesystemLabel must fit ext4's 16-byte label")
        mount_path = lease_storage.get("mountPath")
        if (not isinstance(mount_path, str) or not mount_path.startswith("/")
                or Path(str(worker_dhcp.get("observedLeaseFile", ""))).parent != Path(mount_path)):
            errors.append(f"{prefix}: workerDhcp lease mountPath must parent observedLeaseFile")
        minimum = lease_storage.get("minUsablePercent")
        skew = lease_storage.get("clockSkewSeconds")
        if type(minimum) is not int or type(skew) is not int:
            errors.append(f"{prefix}: workerDhcp lease minUsablePercent/clockSkewSeconds must be integers")
        else:
            if minimum != 90:
                errors.append(f"{prefix}: workerDhcp lease minUsablePercent must be the qualified 90 percent floor")
            if skew < 0 or skew > 300:
                errors.append(f"{prefix}: workerDhcp lease clockSkewSeconds must be between 0 and 300")
    worker_ipam = defaults.gitops_worker_ipam
    for key in [
        "mutatorName", "image", "allocationAnnotation",
        "macPrefix", "networkBinding",
    ]:
        if not worker_ipam.get(key):
            errors.append(f"{prefix}: spec.gitops.workerIpam.{key} is required")
    if worker_ipam.get("networkBinding") != "isovalent":
        errors.append(
            f"{prefix}: spec.gitops.workerIpam.networkBinding must use the documented isovalent binding"
        )
    try:
        orphan_grace = int(worker_ipam.get("orphanGraceSeconds", 0))
    except (TypeError, ValueError):
        orphan_grace = 0
    if orphan_grace < 60:
        errors.append(f"{prefix}: spec.gitops.workerIpam.orphanGraceSeconds must be at least 60")
    probe_timeout = worker_ipam.get("probeTimeoutSeconds")
    if type(probe_timeout) is not int or probe_timeout <= 0:
        errors.append(f"{prefix}: spec.gitops.workerIpam.probeTimeoutSeconds must be a positive integer")
    ipam_image = str(worker_ipam.get("image", ""))
    if ipam_image and (
        not ipam_image.startswith(f"{defaults.registry_url}/") or "@sha256:" not in ipam_image
    ):
        errors.append(f"{prefix}: spec.gitops.workerIpam.image must use the configured registry and an immutable digest")
    for name in ["vector", "hubbleK8sExporter"]:
        try:
            image = defaults.gitops_image_values(name)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue
        if not image.get("repository") or not image.get("tag"):
            errors.append(f"{prefix}: spec.gitops.images.{name}.repository and tag are required")
        if defaults.registry_url and image.get("repository") and not str(image["repository"]).startswith(f"{defaults.registry_url}/"):
            errors.append(f"{prefix}: spec.gitops.images.{name}.repository must use spec.registry.url")

    hosted = defaults.gitops_hosted_cluster
    kubevirt = hosted.get("kubevirt", {}) if isinstance(hosted, dict) else {}
    root_volume = kubevirt.get("rootVolume", {}) if isinstance(kubevirt, dict) else {}
    compute = kubevirt.get("compute", {}) if isinstance(kubevirt, dict) else {}
    if not hosted.get("releaseImageArchitectureSuffix"):
        errors.append(f"{prefix}: spec.gitops.hostedCluster.releaseImageArchitectureSuffix is required")
    if not compute.get("cores") or not compute.get("memory"):
        errors.append(f"{prefix}: spec.gitops.hostedCluster.kubevirt.compute.cores and memory are required")
    if (
        not root_volume.get("size")
        or root_volume.get("storageClass") != "px-rwx-block-kubevirt"
        or root_volume.get("accessModes") != ["ReadWriteMany"]
        or root_volume.get("volumeMode") != "Block"
    ):
        errors.append(f"{prefix}: rootVolume must select px-rwx-block-kubevirt with ReadWriteMany/Block")

    timescape = defaults.gitops_timescape
    object_store = timescape.get("objectStore", {}) if isinstance(timescape, dict) else {}
    push_api = timescape.get("pushApi", {}) if isinstance(timescape, dict) else {}
    if not object_store.get("bucket") or not object_store.get("endpoint") or not object_store.get("region"):
        errors.append(f"{prefix}: spec.gitops.timescape.objectStore bucket, endpoint, and region are required")
    if not push_api.get("uri"):
        errors.append(f"{prefix}: spec.gitops.timescape.pushApi.uri is required")

    storage = defaults.gitops_storage
    lvms = storage.get("lvms", {}) if isinstance(storage, dict) else {}
    portworx = storage.get("portworx", {}) if isinstance(storage, dict) else {}
    if not lvms.get("channel") or not lvms.get("catalogSource"):
        errors.append(f"{prefix}: spec.gitops.storage.lvms channel and catalogSource are required")
    if (portworx.get("version"), portworx.get("operatorVersion"), portworx.get("storkVersion")) != (
        "3.7.0", "26.3.2", "26.4.1"
    ):
        errors.append(f"{prefix}: Portworx tuple must be 3.7.0/operator 26.3.2/Stork 26.4.1")
    images = portworx.get("images", {}) if isinstance(portworx, dict) else {}
    if portworx.get("csiProvisionerTagOnlyCompatibility") not in (None, True):
        errors.append(f"{prefix}: csiProvisionerTagOnlyCompatibility must be true or absent")
    if portworx.get("storkRequiredScc") != "anyuid":
        errors.append(f"{prefix}: storkRequiredScc must be the Portworx-supported anyuid SCC")
    if portworx.get("defaultStorageClass") != {
        "name": "px-csi-replicated-default", "replicationFactor": "3"
    }:
        errors.append(f"{prefix}: defaultStorageClass must use the selected replicated Portworx class")
    placement = portworx.get("migratedComponentPlacement")
    if not isinstance(placement, dict) or any(
        not isinstance(placement.get(key), list) or not placement[key]
        for key in ("componentNames", "workloadNames", "tolerations")
    ):
        errors.append(f"{prefix}: migratedComponentPlacement must retain components, workloads and tolerations")
    for name in ("px-enterprise", "oci-monitor", "stork", "cmdexecutor", "px-operator", "px-lib", "csi-provisioner"):
        image = str(images.get(name, ""))
        if not re.fullmatch(r"\S+@sha256:[0-9a-f]{64}", image):
            errors.append(f"{prefix}: spec.gitops.storage.portworx.images.{name} must be digest-pinned")

    return errors


def validate(platform: ResolvedPlatform) -> list[str]:
    """Validate the resolved platform graph. Returns list of error strings."""
    errors: list[str] = []

    identity: dict[str, Any] = {}
    registry_prefix = ""
    if platform.defaults:
        hosted = any(c.features.get("hostedControlPlanes") or c.hosted_clusters for c in platform.clusters)
        day2 = hosted or any(c.storage or c.parent_networking or any(c.gitops_addons.values())
                              or c.in_cluster_load_balancer.enabled or c.observability for c in platform.clusters)
        errors.extend(_validate_defaults(platform.defaults, day2=day2, registry_required=day2 or any(c.image_delivery_mode == "mirrored" for c in platform.clusters), identity_features=("ilb",) if any(c.in_cluster_load_balancer.enabled for c in platform.clusters) else ()))
        identity = platform.defaults.identity or {}
        if "conformance" in identity and not identity_errors(identity):
            errors.extend(_validate_conformance_allocation(platform, identity))
        if platform.defaults.registry_url:
            registry_prefix = platform.defaults.registry_url.rstrip("/") + "/"
    else:
        errors.append("PlatformDefaults data/defaults/*.yaml is required")

    # Dependent checks access required defaults by name. Report missing selected
    # prerequisites before attempting those accesses; never continue with a partial graph.
    if errors:
        return errors

    if not platform.release_profiles:
        errors.append("platform-release.yaml releases map is required for named OCP/Cilium release profiles")
    for rel in platform.release_profiles.values():
        prefix = f"platform-release.yaml releases.{rel.name}"
        if not re.fullmatch(r"\d+\.\d+\.\d+", rel.ocp_version):
            errors.append(f"{prefix}.openshift.version must pin an exact x.y.z release, got {rel.ocp_version!r}")
        if rel.ocp_install_core_bundle != "ocp-install-core":
            errors.append(f"{prefix}.openshift.image_policy_bundle must be 'ocp-install-core'")
        if not re.fullmatch(r"\d+\.\d+\.\d+", rel.cilium_day1_version):
            errors.append(f"{prefix}.cilium.version must pin an exact x.y.z release, got {rel.cilium_day1_version!r}")
        if rel.cilium_day1_channel and not rel.cilium_day1_version.startswith(f"{rel.cilium_day1_channel}."):
            errors.append(f"{prefix}.cilium.channel {rel.cilium_day1_channel!r} does not match version {rel.cilium_day1_version!r}")
        combination = (rel.ocp_minor, rel.cilium_day1_channel)
        policy = platform.defaults.day1_compatibility if platform.defaults else []
        compatible = {(row.get("openshiftMinor"), row.get("ciliumChannel"))
                      for row in (policy if isinstance(policy, list) else [])
                      if isinstance(row, dict) and all(isinstance(row.get(key), str)
                          for key in ("openshiftMinor", "ciliumChannel"))}
        if combination not in compatible:
            errors.append(f"{prefix}: OCP/Cilium Day 1 combination {combination} is not in declared spec.day1Compatibility")
        if rel.cilium_day1_bundle != "cilium-day1":
            errors.append(f"{prefix}.cilium.image_policy_bundle must be 'cilium-day1'")
        if rel.gitops_bootstrap_bundle != "gitops-bootstrap":
            errors.append(f"{prefix}.gitops_bootstrap.image_policy_bundle must be 'gitops-bootstrap'")
        if rel.platform_day2_bundle != "platform-day2":
            errors.append(f"{prefix}.platform_day2.image_policy_bundle must be 'platform-day2'")
        if not re.fullmatch(r"ocp-\d+\.\d+-cilium-\d+\.\d+", rel.platform_day2_profile):
            errors.append(f"{prefix}.platform_day2 derived profile is invalid: {rel.platform_day2_profile!r}")
        for field_name, image in {
            "cilium.image": rel.cilium_image,
            "cilium.operator_image": rel.cilium_operator_image,
            "cilium.clife_image": rel.clife_image,
        }.items():
            if not image:
                errors.append(f"{prefix}.{field_name} is required for Cilium Day 1 rendering")
        archive = rel.clife_manifest_archive
        if archive and (not isinstance(archive, dict) or not str(archive.get("url", "")).startswith("https://")
                        or not re.fullmatch(r"[0-9a-f]{64}", str(archive.get("sha256", "")))):
            errors.append(f"{prefix}.cilium.manifestArchive requires an HTTPS URL and SHA-256")
        package = rel.clife_package
        if package:
            if package.repository != identity["chartRepository"]:
                errors.append(f"{prefix}.cilium.clife_package.repository must match spec.identity.chartRepository")
            if not re.fullmatch(r"1\.19\.(?:5|7)-clife\.[0-9a-f]{12}\.[0-9a-f]{12}", package.version):
                errors.append(f"{prefix}.cilium.clife_package.version must be an approved immutable content-derived 1.19.5 or 1.19.7 version")
            for field_name, value in {
                "oci_digest": package.oci_digest,
                "package_sha256": package.package_sha256,
                "contract.sha256": package.contract_sha256,
                "receipt.sha256": package.receipt_sha256,
            }.items():
                digest = value.removeprefix("sha256:") if field_name == "oci_digest" else value
                if not re.fullmatch(r"[0-9a-f]{64}", digest):
                    errors.append(f"{prefix}.cilium.clife_package.{field_name} must be a SHA-256 value")
            if not re.fullmatch(r"[0-9a-f]{40}", package.receipt_revision):
                errors.append(f"{prefix}.cilium.clife_package.receipt.revision must be a full Git commit")
            scenario_modes = [item.get("mode") for item in package.scenarios.values()]
            if (not package.scenarios
                    or any(mode not in {"management", "hosted"} for mode in scenario_modes)
                    or sum(mode == "management" for mode in scenario_modes) != 1):
                errors.append(f"{prefix}.cilium.clife_package scenarios must come from the package and declare one management mode")
            runtime_images = {
                item.get("name"): f"{item.get('source')}@{item.get('digest')}"
                for item in package.runtime_images
            }
            for name, expected in {
                "cilium": rel.cilium_image,
                "operator-generic": rel.cilium_operator_image,
                "clife": rel.clife_image,
            }.items():
                if runtime_images.get(name) != expected:
                    errors.append(
                        f"{prefix}.cilium.{name} release image must match the receipt-bound runtime closure"
                    )
            for item in package.runtime_images:
                name = item.get("name", "<unnamed>")
                if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(item.get("digest", ""))):
                    errors.append(f"{prefix}.cilium.clife_package runtime image {name} must be digest-pinned")
                if not str(item.get("mirror", "")).startswith(registry_prefix):
                    errors.append(f"{prefix}.cilium.clife_package runtime image {name} must use spec.registry.url")

    for cluster in platform.clusters:
        prefix = f"OCPCluster({cluster.name})"
        if platform.defaults and platform.defaults.input_root:
            from gitops_renderer.console_login import validate_console_login
            validate_console_login(cluster, platform.defaults, errors)

        # Cluster and Service ranges are independent address spaces, but every
        # active or staged owner within each space must have a unique claim.
        retained_claims = cluster.retained_hosted_network_claims
        if not isinstance(retained_claims, dict):
            errors.append(f"{prefix}: retainedHostedNetworkClaims must be a mapping")
            retained_claims = {}
        address_owners = {
            "clusterNetwork": [(cluster.name, item.get("cidr")) for item in platform.defaults.ocp_cluster_network],
            "serviceNetwork": [(cluster.name, item) for item in platform.defaults.ocp_service_network],
        }
        for name, claim in retained_claims.items():
            if not isinstance(claim, dict):
                errors.append(f"{prefix}: retained network claim {name!r} must be a mapping")
                continue
            rendered = next((hcp for hcp in cluster.hosted_clusters if hcp.name == name), None)
            for field_name in address_owners:
                values = claim.get(field_name)
                if not isinstance(values, list) or not values:
                    errors.append(f"{prefix}: retained network claim {name!r} needs {field_name} list")
                    continue
                if rendered:
                    selected = (rendered.cluster_networks if field_name == "clusterNetwork"
                                else rendered.service_networks)
                    if values != [item.get("cidr") for item in selected]:
                        errors.append(f"{prefix}: retained network claim {name!r} differs from rendered {field_name}")
                else:
                    address_owners[field_name].extend((name, cidr) for cidr in values)
        for hcp in cluster.hosted_clusters:
            for field_name, values in (
                ("clusterNetwork", hcp.cluster_networks),
                ("serviceNetwork", hcp.service_networks),
            ):
                address_owners[field_name].extend((hcp.name, item.get("cidr")) for item in values)
        for field_name, claims in address_owners.items():
            parsed = []
            for owner, cidr in claims:
                try:
                    network = ipaddress.IPv4Network(cidr, strict=True)
                except (ValueError, TypeError) as exc:
                    errors.append(f"{prefix}: invalid {field_name} for {owner}: {cidr!r} ({exc})")
                    continue
                # Retained live claims may contain historical ranges. Newly
                # staged claims must fall within one of the three RFC1918 blocks.
                rfc1918 = any(network.subnet_of(block) for block in (
                    ipaddress.IPv4Network("10.0.0.0/8"),
                    ipaddress.IPv4Network("172.16.0.0/12"),
                    ipaddress.IPv4Network("192.168.0.0/16"),
                ))
                if owner not in retained_claims and not rfc1918:
                    errors.append(f"{prefix}: {field_name} for {owner} must be RFC1918: {network}")
                for previous_owner, previous_network in parsed:
                    if network.overlaps(previous_network):
                        errors.append(f"{prefix}: overlapping {field_name}: {previous_owner} {previous_network} and {owner} {network}")
                parsed.append((owner, network))

        observability = cluster.observability
        if not isinstance(observability, dict):
            errors.append(f"{prefix}: day2GitOps.observability must be a mapping")
            observability = {}
        central_timescape = observability.get("centralTimescape", {})
        if "managementExporter" in observability:
            try:
                management_exporter_parameters(observability["managementExporter"])
            except ValueError as exc:
                errors.append(f"{prefix}: {exc}")
        if not isinstance(central_timescape, dict):
            errors.append(f"{prefix}: day2GitOps.observability.centralTimescape must be a mapping")
            central_timescape = {}
        if central_timescape:
            review = central_timescape.get("writerReview")
            expected_api = f"https://api.{cluster.name}.{cluster.base_domain}:6443"
            if (not isinstance(review, dict) or
                    review.get("managementApiEndpoint") != expected_api or
                    not isinstance(review.get("guestControllerRef"), str) or
                    not re.fullmatch(r"[a-z0-9-]+/[a-z0-9-]+",
                                     review["guestControllerRef"]) or
                    type(review.get("commandTimeoutSeconds")) is not int or
                    not 1 <= review["commandTimeoutSeconds"] <= 120 or
                    type(review.get("jobTimeoutMinutes")) is not int or
                    not 1 <= review["jobTimeoutMinutes"] <= 60):
                errors.append(f"{prefix}: centralTimescape.writerReview requires declared API endpoint, guest controller and bounded execution policy")
            if type(central_timescape.get("enabled")) is not bool:
                errors.append(f"{prefix}: centralTimescape.enabled must be boolean")
            if central_timescape.get("enabled") and (
                cluster.mode != "compact"
                or cluster.storage.get("activation") != "provider-cutover"
                or central_timescape.get("storageClass") != "px-csi-replicated-default"
            ):
                errors.append(f"{prefix}: enabled centralTimescape requires compact provider-cutover storage and px-csi-replicated-default")
            retained = central_timescape.get("retainedHostedClusters", [])
            if not isinstance(retained, list) or any(
                not isinstance(name, str) or not name or name in {hcp.name for hcp in cluster.hosted_clusters}
                for name in retained
            ) or len(retained) != len(set(retained)):
                errors.append(f"{prefix}: centralTimescape.retainedHostedClusters must list distinct external hosted cluster names")
            foundation = central_timescape.get("foundation", {})
            if not isinstance(foundation, dict):
                errors.append(f"{prefix}: centralTimescape.foundation must be a mapping")
            else:
                for key, allowed in (("serviceMonitors", {"server", "ingester", "clickhouse"}),
                                     ("alertRules", {"server", "ingester", "clickhouse"})):
                    selected = foundation.get(key, [])
                    if not isinstance(selected, list) or any(
                        not isinstance(item, str) or item not in allowed for item in selected
                    ) or len(selected) != len(set(selected)):
                        errors.append(f"{prefix}: centralTimescape.foundation.{key} must list distinct supported components")
                retention = foundation.get("retention", {})
                if not isinstance(retention, dict):
                    errors.append(f"{prefix}: centralTimescape.foundation.retention must be a mapping")
                elif retention:
                    ttl = retention.get("ttl")
                    limit = retention.get("flowRowLimit")
                    if not isinstance(ttl, str) or not re.fullmatch(r"[1-9][0-9]*h", ttl):
                        errors.append(f"{prefix}: centralTimescape.foundation.retention.ttl must be positive whole hours")
                    if type(limit) is not int or limit <= 0:
                        errors.append(f"{prefix}: centralTimescape.foundation.retention.flowRowLimit must be positive integer")

        if not isinstance(cluster.maintenance_vm_holds, list) or any(
            not isinstance(item, str) for item in cluster.maintenance_vm_holds
        ):
            errors.append(f"{prefix}: day2GitOps.maintenanceVmHolds must be a list of namespace/name strings")
        else:
            if len(cluster.maintenance_vm_holds) != len(set(cluster.maintenance_vm_holds)):
                errors.append(f"{prefix}: day2GitOps.maintenanceVmHolds contains duplicates")
            supported_vm_holds: set[str] = set()
            if cluster.features.get("hostedControlPlanes"):
                if platform.defaults is None:
                    errors.append(f"{prefix}: maintenance VM holds require PlatformDefaults")
                else:
                    dhcp_namespace = platform.defaults.namespace("hcpWorkerDhcp")
                    server_name = str(platform.defaults.gitops_worker_dhcp.get("serverName") or "")
                    if not server_name:
                        errors.append(f"{prefix}: workerDhcp.serverName is required")
                    staged_only = (
                        (cluster.hosted_cluster_lifecycle.get("deploymentSubjects") or {}).get("stagedOnly")
                        or []
                    )
                    supported_vm_holds.update(
                        f"{dhcp_namespace}/{server_name}-{name}"
                        for name in staged_only
                        if isinstance(name, str) and name
                    )
            for vm_hold in cluster.maintenance_vm_holds:
                if vm_hold not in supported_vm_holds:
                    errors.append(f"{prefix}: unsupported day2GitOps.maintenanceVmHolds entry {vm_hold!r}")

        # Node cardinality
        if cluster.mode == "compact" and len(cluster.nodes) != 3:
            errors.append(f"{prefix}: compact mode requires exactly 3 nodes, got {len(cluster.nodes)}")
        if cluster.mode == "sno" and len(cluster.nodes) != 1:
            errors.append(f"{prefix}: SNO mode requires exactly 1 node, got {len(cluster.nodes)}")

        # Supported install topology and backend paths
        if cluster.mode not in ("sno", "compact"):
            errors.append(f"{prefix}: mode must be sno or compact, got {cluster.mode!r}")

        if cluster.network_type not in ("OVNKubernetes", "Cilium"):
            errors.append(f"{prefix}: openshift.networkType must be OVNKubernetes or Cilium, got {cluster.network_type!r}")

        if not cluster.release_ref:
            errors.append(f"{prefix}: spec.releaseRef is required")
        if cluster.release_ref and cluster.release_ref not in platform.release_profiles:
            errors.append(f"{prefix}: spec.releaseRef {cluster.release_ref!r} does not match a named platform-release.yaml profile")
        tenant_ref = cluster.tenant_guest_cni.package_release_ref
        if tenant_ref and tenant_ref not in platform.release_profiles:
            errors.append(f"{prefix}: tenantGuestCNI.packageReleaseRef {tenant_ref!r} does not match a named platform-release.yaml profile")
        tenant_release = platform.release_profiles.get(tenant_ref)
        if tenant_release and cluster.release and tenant_release.ocp_minor != cluster.release.ocp_minor:
            errors.append(f"{prefix}: tenantGuestCNI package release must target the management OpenShift minor")
        if tenant_release and tenant_release.support_status != "supported":
            errors.append(f"{prefix}: tenantGuestCNI package release must be supported")
        for field_name, ref in {
            "currentReleaseRef": cluster.current_release_ref,
            "targetReleaseRef": cluster.target_release_ref,
        }.items():
            if ref and ref not in platform.release_profiles:
                errors.append(f"{prefix}: spec.{field_name} {ref!r} does not match a named platform-release.yaml profile")
        if cluster.release and cluster.release.support_status != "supported":
            errors.append(f"{prefix}: spec.releaseRef must remain on a supported release profile")
        if cluster.target_release:
            if cluster.current_release_ref != cluster.release_ref:
                errors.append(f"{prefix}: currentReleaseRef must identify the active supported releaseRef")
            if cluster.target_release.support_status != "supported":
                errors.append(f"{prefix}: targetReleaseRef must be a documented supported release")
        if not re.fullmatch(r"\d+\.\d+\.\d+", cluster.ocp_version):
            errors.append(f"{prefix}: selected release must pin an exact OpenShift x.y.z release, got {cluster.ocp_version!r}")
        if cluster.network_type == "Cilium" and not cluster.release:
            errors.append(f"{prefix}: networkType Cilium requires a selected release with Cilium Day 1 intent")

        if not platform.defaults or not platform.defaults.ntp_servers:
            errors.append(f"{prefix}: PlatformDefaults spec.ntp.servers is required for AgentConfig additionalNTPSources")

        if cluster.mode == "sno":
            if cluster.api_vip or cluster.ingress_vip:
                errors.append(f"{prefix}: SNO mode uses platform none and must not define apiVIP/ingressVIP")
            if cluster.network_type != "OVNKubernetes":
                errors.append(f"{prefix}: SNO proven install path requires networkType OVNKubernetes")

        # Compact needs VIPs. It can install with OVNKubernetes or with the tested
        # Cilium day-1 path; ciliumDay2Migration remains an SNO-only switch.
        if cluster.mode == "compact":
            if not cluster.api_vip:
                errors.append(f"{prefix}: compact mode requires apiVIP")
            if not cluster.ingress_vip:
                errors.append(f"{prefix}: compact mode requires ingressVIP")
            if cluster.cilium_day2_migration:
                errors.append(f"{prefix}: ciliumDay2Migration applies only to SNO/OVNKubernetes, not compact")
            if cluster.inventory_mode == "declared" and cluster.machine_network:
                subnet = ipaddress.IPv4Network(cluster.machine_network.cidr, strict=False)
                reserved = {n.ip for n in cluster.nodes} | {cluster.machine_network.gateway,
                            str(subnet.network_address), str(subnet.broadcast_address)}
                for name, address in (("apiVIP", cluster.api_vip), ("ingressVIP", cluster.ingress_vip)):
                    try:
                        parsed = ipaddress.IPv4Address(address)
                        if parsed not in subnet or address in reserved:
                            errors.append(f"{prefix}: {name} must be an unused host address in the machine network")
                    except (ValueError, TypeError):
                        errors.append(f"{prefix}: {name} must be an IPv4 host address")
                if cluster.api_vip == cluster.ingress_vip:
                    errors.append(f"{prefix}: apiVIP and ingressVIP must be distinct")

        storage = cluster.storage
        profile = storage.get("profile") if isinstance(storage, dict) else None
        expected_profile = "sno-mdadm-lvms" if cluster.mode == "sno" else "portworx-local"
        storage_required = bool(storage) or cluster.mode == "sno" or cluster.features.get("hostedControlPlanes", False)
        if storage_required and profile != expected_profile:
            errors.append(f"{prefix}: {cluster.mode} mode requires day2GitOps.storage.profile={expected_profile}")
        if storage_required and storage.get("maturity") != "partial":
            errors.append(f"{prefix}: new Day-2 storage mechanism must remain maturity=partial until live proof")
        if profile == "sno-mdadm-lvms":
            if storage.get("activation") != "active":
                errors.append(f"{prefix}: SNO storage activation must be active; staged is not a supported renderer state")
            array = storage.get("array", {})
            devices = array.get("devices", []) if isinstance(array, dict) else []
            if cluster.mode != "sno" or len(devices) != 5:
                errors.append(f"{prefix}: sno-mdadm-lvms requires exactly five devices on an SNO cluster")
            if (array.get("level"), array.get("raidDevices"), array.get("spareDevices")) != ("raid10", 4, 1):
                errors.append(f"{prefix}: SNO array must be RAID10 with four members and one spare")
            if array.get("devicePath") != "/dev/md/nvme-data":
                errors.append(f"{prefix}: SNO array devicePath must be /dev/md/nvme-data")
        elif profile == "portworx-local":
            if cluster.mode != "compact":
                errors.append(f"{prefix}: portworx-local is supported only on compact clusters")
            if "mdadm" in cluster.capabilities:
                errors.append(f"{prefix}: compact Portworx profile must not retain mdadm capability")
            if storage.get("activation") not in {"staged", "consumer-hold", "provider-cutover"}:
                errors.append(
                    f"{prefix}: compact Portworx activation must be staged, consumer-hold, "
                    "or provider-cutover"
                )
            lifecycle = cluster.hosted_cluster_lifecycle
            if lifecycle:
                capacity = lifecycle.get("managementPodCapacity")
                if capacity is not None:
                    policy = capacity.get("restartPolicy") if isinstance(capacity, dict) else None
                    if (not isinstance(capacity, dict)
                            or capacity.get("machineConfigPool") not in {"master", "worker"}
                            or type(capacity.get("maxPods")) is not int
                            or not 1 <= capacity["maxPods"] <= 500
                            or type(capacity.get("podsPerCore")) is not int
                            or not 0 <= capacity["podsPerCore"] <= capacity["maxPods"]
                            or not isinstance(policy, dict)
                            or policy.get("path") != "/etc/kubernetes/kubelet.conf"
                            or policy.get("serviceName") != "kubelet.service"):
                        errors.append(
                            f"{prefix}: managementPodCapacity requires a master/worker pool, "
                            "maxPods in 1..500, podsPerCore in 0..maxPods, and the exact kubelet restart policy"
                        )
                subjects = lifecycle.get("deploymentSubjects")
                if subjects and not isinstance(subjects, dict):
                    errors.append(f"{prefix}: deploymentSubjects must be a mapping")
                    subjects = {}
                if subjects:
                    roles = ("multus",)
                    selected = [subjects.get(key) for key in roles]
                    staged_only = subjects.get("stagedOnly", [])
                    rendered = {hcp.name: hcp for hcp in cluster.hosted_clusters}
                    valid_selected = all(isinstance(name, str) and name for name in selected)
                    selected_names = set(selected) if valid_selected else set()
                    if (not valid_selected
                            or len(selected_names) != len(roles)
                            or not selected_names <= set(rendered)
                            or not isinstance(staged_only, list)
                            or any(not isinstance(name, str) for name in staged_only)
                            or len(staged_only) != len(set(staged_only))
                            or set(staged_only) != set(rendered) - selected_names):
                        errors.append(f"{prefix}: deploymentSubjects must select the Multus cluster and account for staged predecessors")
                    elif not rendered[selected[0]].uses_multus_workers:
                        errors.append(f"{prefix}: deploymentSubjects.multus must select a Multus hosted cluster")
                    placement = lifecycle.get("workerPlacement")
                    eligible = placement.get("eligibleManagementNodes") if isinstance(placement, dict) else None
                    weight = placement.get("spreadWeight") if isinstance(placement, dict) else None
                    declared_nodes = identity.get("managementNodes")
                    if (not isinstance(eligible, list)
                            or len(eligible) != len(cluster.nodes)
                            or any(not isinstance(name, str) for name in eligible)
                            or set(eligible) != {f"{node.name}.{cluster.base_domain}" for node in cluster.nodes}
                            or declared_nodes != eligible
                            or type(weight) is not int or not 1 <= weight <= 100):
                        errors.append(
                            f"{prefix}: workerPlacement.eligibleManagementNodes must equal "
                            "spec.identity.managementNodes and every cluster node, "
                            "with spread weight from 1 to 100"
                        )
                phase = lifecycle.get("phase")
                replicas = lifecycle.get("workerReplicas")
                if storage.get("activation") != "provider-cutover":
                    errors.append(f"{prefix}: explicit HCP lifecycle requires provider-cutover storage")
                if phase not in {"held", "control-planes", "workers"}:
                    errors.append(f"{prefix}: hostedClusterLifecycle.phase must be held, control-planes, or workers")
                if type(replicas) is not int or replicas < 0:
                    errors.append(f"{prefix}: hostedClusterLifecycle.workerReplicas must be a non-negative integer")
                overrides = lifecycle.get("workerReplicasByCluster", {})
                hcp_by_name = {hcp.name: hcp for hcp in cluster.hosted_clusters}
                if not isinstance(overrides, dict) or any(
                    name not in hcp_by_name
                    or type(value) is not int or value < 0
                    for name, value in (overrides.items() if isinstance(overrides, dict) else ())
                ):
                    errors.append(f"{prefix}: hostedClusterLifecycle.workerReplicasByCluster must map hosted cluster names to non-negative integers")
                elif type(replicas) is int and replicas >= 0:
                    for hcp in cluster.hosted_clusters:
                        selected_replicas = overrides.get(hcp.name, replicas)
                        upper_bound = hcp.worker_replica_max if hcp.worker_replica_max is not None else hcp.replicas
                        if (selected_replicas > upper_bound
                                or (hcp.worker_replica_checkpoints
                                    and selected_replicas not in hcp.worker_replica_checkpoints)):
                            errors.append(
                                f"{prefix}: {hcp.name} worker replica intent exceeds its declared NodePool rollout policy"
                            )
                if phase != "workers" and replicas != 0:
                    errors.append(f"{prefix}: held/control-planes HCP phase requires zero worker replicas")
                if phase != "workers" and overrides:
                    errors.append(f"{prefix}: held/control-planes HCP phase cannot override worker replicas")
                if lifecycle.get("localRegistryConsumer") is not None:
                    errors.append(f"{prefix}: hostedClusterLifecycle.localRegistryConsumer is retired; use local-registry.yaml")
                _validate_local_registry(
                    prefix, cluster.local_registry,
                    {}, errors,
                    registry_prefix=registry_prefix,
                )
                if "externalPull" in cluster.local_registry:
                    from gitops_renderer.registry_exposure import registry_exposure
                    from gitops_renderer.selected_inputs import selected
                    try:
                        with selected(platform.defaults):
                            registry_exposure(cluster)
                    except (ValueError, KeyError, TypeError) as exc:
                        errors.append(f"{prefix}: {exc}")
            if (str(storage.get("version")), str(storage.get("operatorVersion")), str(storage.get("storkVersion"))) != (
                "3.7.0", "26.3.2", "26.4.1"
            ):
                errors.append(f"{prefix}: selected Portworx tuple does not match platform defaults")
            px_nodes = storage.get("nodes", [])
            if {item.get("name") for item in px_nodes if isinstance(item, dict)} != {node.name for node in cluster.nodes}:
                errors.append(f"{prefix}: Portworx node inventory must match all compact nodes exactly")
            seen_devices: set[str] = set()
            for item in px_nodes:
                if not isinstance(item, dict):
                    errors.append(f"{prefix}: every Portworx node entry must be a mapping")
                    continue
                metadata_device = item.get("systemMetadataDevice")
                devices = item.get("devices", [])
                if len(devices) != 4 or not metadata_device:
                    errors.append(f"{prefix}: Portworx node {item.get('name')} requires one metadata and four data devices")
                for device in [metadata_device, *devices]:
                    if not isinstance(device, str) or not device.startswith("/dev/disk/by-id/"):
                        errors.append(f"{prefix}: Portworx devices must use stable /dev/disk/by-id paths, got {device!r}")
                        continue
                    if re.fullmatch(r"/dev/sd[a-z]+", device) or "useAll" in device:
                        errors.append(f"{prefix}: unsafe Portworx device selector {device!r}")
                    if device in seen_devices:
                        errors.append(f"{prefix}: Portworx device {device!r} is assigned more than once")
                    seen_devices.add(device)
                if metadata_device in devices:
                    errors.append(f"{prefix}: Portworx metadata device cannot also be a data device")
            nodepool = storage.get("hcpNodePool", {})
            expected_nodepool = {
                "activation": "staged", "upgradeType": "InPlace", "maxUnavailable": 1,
                "autoRepair": False, "accessModes": ["ReadWriteMany"], "volumeMode": "Block",
            }
            for key, value in expected_nodepool.items():
                if nodepool.get(key) != value:
                    errors.append(f"{prefix}: storage.hcpNodePool.{key} must be {value!r}")
            if storage.get("licenseSecretName") != "px-enterprise-license":
                errors.append(f"{prefix}: Portworx must reference the runtime-owned px-enterprise-license secret")

        for capability in cluster.capabilities:
            if capability not in ("disconnectedCatalogs", "mdadm"):
                errors.append(f"{prefix}: unsupported capability {capability!r}; supported: disconnectedCatalogs, mdadm")

        for field_name, intent in {
            "managementCLIFE": cluster.management_clife,
            "tenantGuestCNI": cluster.tenant_guest_cni,
        }.items():
            if intent.lifecycle not in {"olm", "manifest", "oci"}:
                errors.append(f"{prefix}: day2GitOps.{field_name}.lifecycle must be olm, manifest or oci")
            if intent.lifecycle == "manifest" and intent.manifest_bundle:
                bundle_path = Path(intent.manifest_bundle)
                if not bundle_path.is_absolute():
                    bundle_path = Path(__file__).resolve().parent.parent / bundle_path
                if not bundle_path.exists():
                    errors.append(f"{prefix}: day2GitOps.{field_name}.manifestBundle not found: {intent.manifest_bundle}")
            selected_package = (cluster.tenant_guest_cni.package if field_name == "tenantGuestCNI"
                                else cluster.release.clife_package if cluster.release else None)
            if intent.lifecycle == "oci" and not selected_package:
                errors.append(f"{prefix}: day2GitOps.{field_name}.lifecycle=oci requires a receipt-bound release CLIFE package")
        package = cluster.release.clife_package if cluster.release else None
        if cluster.management_clife.lifecycle == "oci" and package:
            scenario = package.scenarios.get(cluster.management_clife.package_scenario)
            if not scenario or scenario.get("mode") != "management":
                errors.append(f"{prefix}: managementCLIFE.packageScenario must select the management package scenario")
        tenant_package = cluster.tenant_guest_cni.package
        if cluster.tenant_guest_cni.lifecycle == "oci" and not cluster.tenant_guest_cni.package_release_ref:
            errors.append(f"{prefix}: tenantGuestCNI.packageReleaseRef must explicitly select a release profile")
        if cluster.tenant_guest_cni.lifecycle == "oci" and tenant_package:
            cilium_hcps = {hcp.name for hcp in cluster.hosted_clusters if hcp.network_type == "Cilium"}
            if set(cluster.tenant_guest_cni.package_scenarios) != cilium_hcps:
                errors.append(
                    f"{prefix}: tenantGuestCNI.packageScenarios must map exactly the Cilium hosted clusters {sorted(cilium_hcps)}"
                )
            for hcp_name, scenario_name in cluster.tenant_guest_cni.package_scenarios.items():
                scenario = tenant_package.scenarios.get(scenario_name)
                # Receipt-bound chart profiles keep their published scenario names.
                # Hosted-cluster identity is the HCPCluster name; render overwrites
                # cluster name, API hostname, and pod CIDR from that object.
                if not scenario or scenario.get("mode") != "hosted":
                    errors.append(
                        f"{prefix}: tenantGuestCNI package scenario {scenario_name!r} for {hcp_name} "
                        "must be an existing hosted-mode chart profile"
                    )
        if cluster.tenant_guest_cni.channel and not re.fullmatch(r"\d+\.\d+", cluster.tenant_guest_cni.channel):
            errors.append(f"{prefix}: day2GitOps.tenantGuestCNI.channel must use '<major>.<minor>' format")
        if cluster.tenant_guest_cni.install_plan_approval not in {"Manual", "Automatic"}:
            errors.append(f"{prefix}: day2GitOps.tenantGuestCNI.installPlanApproval must be Manual or Automatic")
        for field_name, image in {
            "managementCLIFE.image": cluster.management_clife.image,
            "tenantGuestCNI.clifeImage": cluster.tenant_guest_cni.clife_image,
        }.items():
            if image and platform.defaults and not image.startswith(f"{platform.defaults.registry_url}/"):
                errors.append(f"{prefix}: day2GitOps.{field_name} must use PlatformDefaults spec.registry.url")

        supported_addons = {"nvidia", "splunk", "demoApps", "saifDemo"}
        unknown_addons = set(cluster.gitops_addons) - supported_addons
        if unknown_addons:
            errors.append(f"{prefix}: unsupported day2GitOps.addOns {sorted(unknown_addons)!r}; supported: {sorted(supported_addons)!r}")
        if cluster.gitops_addons.get("saifDemo", False):
            if not cluster.gitops_addons.get("demoApps", False):
                errors.append(f"{prefix}: day2GitOps.addOns.saifDemo requires demoApps=true")
            if not cluster.gitops_addons.get("nvidia", False):
                errors.append(f"{prefix}: day2GitOps.addOns.saifDemo requires nvidia=true for the local LLM backend")
            if cluster.network_type != "Cilium":
                errors.append(f"{prefix}: day2GitOps.addOns.saifDemo requires networkType Cilium for Hubble/Timescape flow data")
        if cluster.gitops_addons.get("demoApps", False) and not cluster.gitops_addons.get("nvidia", False):
            errors.append(f"{prefix}: day2GitOps.addOns.demoApps currently includes OpenWebUI/DeepSeek and requires nvidia=true")
        if any(value is True for value in cluster.gitops_addons.values()):
            from gitops_renderer.addons import addon_errors
            errors.extend(f"{prefix}: {item}" for item in addon_errors(cluster, platform.defaults))

        supported_overrides = {"cilium", "operator", "envoy", "hubbleRelay"}
        unknown_overrides = set(cluster.cilium_config.image_overrides) - supported_overrides
        if unknown_overrides:
            errors.append(f"{prefix}: unsupported day2GitOps.ciliumConfig.imageOverrides {sorted(unknown_overrides)!r}; supported: {sorted(supported_overrides)!r}")
        for component, override in cluster.cilium_config.image_overrides.items():
            if not override.enabled:
                continue
            override_prefix = f"{prefix}: day2GitOps.ciliumConfig.imageOverrides.{component}"
            if not override.repository:
                errors.append(f"{override_prefix}.repository is required when enabled")
            if not override.tag:
                errors.append(f"{override_prefix}.tag is required when enabled")
            if component == "operator":
                if not override.generic_digest:
                    errors.append(f"{override_prefix}.genericDigest is required when enabled")
            elif not override.digest:
                errors.append(f"{override_prefix}.digest is required when enabled")
        if cluster.cilium_config.ci_commit:
            for component, override in cluster.cilium_config.image_overrides.items():
                if override.enabled and override.tag and component != "envoy" and override.tag != cluster.cilium_config.ci_commit:
                    errors.append(f"{prefix}: {component} override tag must match day2GitOps.ciliumConfig.ciCommit")

        ilb = cluster.in_cluster_load_balancer
        if ilb.enabled:
            node_names = {node.name for node in cluster.nodes}
            router_nodes = set(ilb.router_t1_nodes)
            t2_nodes = set(ilb.t2_nodes)
            if ilb.lifecycle != "staged":
                errors.append(f"{prefix}: inClusterLoadBalancer.lifecycle must be staged")
            if ilb.router_replicas != 2 or len(router_nodes) != 2:
                errors.append(f"{prefix}: inClusterLoadBalancer requires exactly two Router/T1 nodes and replicas=2")
            if len(t2_nodes) != 1:
                errors.append(f"{prefix}: official 1.19.5 Option A requires exactly one T2 node")
            if router_nodes & t2_nodes:
                errors.append(f"{prefix}: Router/T1 and T2 placement must be disjoint")
            if not router_nodes | t2_nodes <= node_names:
                errors.append(f"{prefix}: inClusterLoadBalancer placement references an unknown management node")
            if not ilb.placement_job_image.startswith(registry_prefix) or "@sha256:" not in ilb.placement_job_image:
                errors.append(f"{prefix}: inClusterLoadBalancer placement job image must be an immutable digest under spec.registry.url")
            profile = ilb.cilium_config_profile
            if not ilb.cilium_config_profile_ref:
                errors.append(f"{prefix}: inClusterLoadBalancer.ciliumConfigProfileRef is required")
            elif not profile:
                errors.append(
                    f"{prefix}: inClusterLoadBalancer.ciliumConfigProfileRef "
                    f"{ilb.cilium_config_profile_ref!r} was not found"
                )
            else:
                for error in sorted(
                    Draft202012Validator(profile.values_schema).iter_errors(profile.values),
                    key=lambda item: list(item.absolute_path),
                ):
                    field_path = ".".join(str(item) for item in error.absolute_path)
                    field = f".{field_path}" if field_path else ""
                    errors.append(
                        f"{prefix}: CiliumConfigProfile({profile.name}).values{field}: {error.message}"
                    )
                if cluster.release:
                    if cluster.release.cilium_day1_version not in profile.compatible_cilium_versions:
                        errors.append(
                            f"{prefix}: CiliumConfigProfile({profile.name}) does not support "
                            f"Cilium {cluster.release.cilium_day1_version}"
                        )
                    if cluster.release.ocp_minor not in profile.compatible_openshift_minors:
                        errors.append(
                            f"{prefix}: CiliumConfigProfile({profile.name}) does not support "
                            f"OpenShift {cluster.release.ocp_minor}"
                        )
        if package and (
            cluster.cilium_config.prerelease_version
            or cluster.cilium_config.ci_commit
            or cluster.cilium_config.ci_chart
            or any(item.enabled for item in cluster.cilium_config.image_overrides.values())
        ):
            errors.append(f"{prefix}: receipt-bound official CLIFE cannot be combined with prerelease or image-override intent")

        delivery = cluster.image_delivery_mode
        scopes = cluster.image_delivery_scopes
        allowed_scopes = {"ocp-install-core", "cilium-day1", "gitops-bootstrap", "platform-day2"}
        required_scopes = {"ocp-install-core"} | ({"cilium-day1"} if cluster.network_type == "Cilium" else set())
        if (delivery not in {"connected", "mirrored"} or not isinstance(scopes, list)
                or any(not isinstance(v, str) or v not in allowed_scopes for v in scopes)
                or len(scopes) != len(set(scopes))):
            errors.append(f"{prefix}: imageDelivery requires connected/mirrored mode and unique known scopes")
        elif delivery == "connected" and scopes:
            errors.append(f"{prefix}: connected imageDelivery must not declare mirrored scopes")
        elif delivery == "mirrored" and not required_scopes <= set(scopes):
            errors.append(f"{prefix}: mirrored imageDelivery must select required Day1 scopes {sorted(required_scopes)}")
        if cluster.registry_trust_policy not in {"public", "additional-ca"}:
            errors.append(f"{prefix}: installConfig.openshift.registryTrustPolicy must be public or additional-ca")
        if cluster.inventory_mode not in {"runtime", "declared"}:
            errors.append(f"{prefix}: agentConfig.inventoryMode must be runtime or declared")
        if cluster.inventory_mode == "declared":
            inventory = {
                "mode": cluster.mode,
                "rendezvousNode": cluster.rendezvous_node,
                "managementInterface": {
                    "bond": cluster.management_interface.bond,
                    "members": cluster.management_interface.members,
                    "mode": cluster.management_interface.mode,
                    "options": cluster.management_interface.options,
                },
                "nodes": [{"name": n.name, "ip": n.ip, "interfaces": n.interfaces,
                           "rootDeviceHints": n.root_device_hints} for n in cluster.nodes],
            }
            schema = json.loads((platform.defaults.input_root / "schemas" / "agent-inventory-v1.schema.json").read_text())
            for err in Draft202012Validator(schema).iter_errors(inventory):
                errors.append(f"{prefix}: declared inventory {'.'.join(map(str, err.path))}: {err.message}")
            if cluster.rendezvous_node not in {n.name for n in cluster.nodes}:
                errors.append(f"{prefix}: rendezvousNode must name a declared host")
            seen_macs = set()
            for node in cluster.nodes:
                if {i.get("name") for i in node.interfaces} != set(cluster.management_interface.members):
                    errors.append(f"{prefix}: {node.name} interfaces must match managementInterface.members")
                for interface in node.interfaces:
                    mac = str(interface.get("macAddress", "")).lower()
                    if mac in seen_macs:
                        errors.append(f"{prefix}: duplicate declared MAC address")
                    seen_macs.add(mac)
            if not cluster.day1_cilium_spec:
                errors.append(f"{prefix}: declared hardware requires explicit installConfig.cilium values")
        if cluster.day1_cilium_spec:
            spec = cluster.day1_cilium_spec
            reserved = {"image", "version", "cluster", "k8sServiceHost", "k8sServicePort", "ipam", "devices", "nodePort"}
            if reserved & set(spec):
                errors.append(f"{prefix}: installConfig.cilium identity/pool/interface fields are derived, not overridden")
            if spec.get("kubeProxyReplacement") is not True:
                errors.append(f"{prefix}: this Day1 renderer requires kubeProxyReplacement=true")
            if spec.get("cni", {}).get("exclusive") is not False or spec.get("cni", {}).get("confPath") != "/var/run/multus/cni/net.d":
                errors.append(f"{prefix}: Day1 Cilium must use the documented OpenShift Multus CNI directory")
            if cluster.release and cluster.release.ocp_minor == "4.22":
                version = tuple(map(int, cluster.release.cilium_day1_version.split(".")))
                if version < (1, 19, 6):
                    errors.append(f"{prefix}: OpenShift4.22 example requires INK1.19.6-cee.1 or later")

        if cluster.management_interface:
            if not cluster.management_interface.bond:
                errors.append(f"{prefix}: agentConfig.managementInterface.bond is required")
            if len(cluster.management_interface.members) != 2:
                errors.append(f"{prefix}: agentConfig.managementInterface.members must list exactly two NICs")

        if cluster.parent_networking and cluster.parent_networking.management_lb_pool:
            pool = cluster.parent_networking.management_lb_pool
            if not pool.name:
                errors.append(f"{prefix}: day2GitOps.parentNetworking.managementLoadBalancerPool.name is required")
            if not pool.start:
                errors.append(f"{prefix}: day2GitOps.parentNetworking.managementLoadBalancerPool.start is required")
            if not pool.stop:
                errors.append(f"{prefix}: day2GitOps.parentNetworking.managementLoadBalancerPool.stop is required")
            if pool.start and pool.stop:
                try:
                    start_ip = ipaddress.IPv4Address(pool.start)
                    stop_ip = ipaddress.IPv4Address(pool.stop)
                    if start_ip > stop_ip:
                        errors.append(f"{prefix}: managementLoadBalancerPool.start must be <= stop")
                    if cluster.machine_network and cluster.machine_network.cidr:
                        mn_net = ipaddress.IPv4Network(cluster.machine_network.cidr, strict=False)
                        if start_ip not in mn_net or stop_ip not in mn_net:
                            errors.append(f"{prefix}: managementLoadBalancerPool {pool.start}-{pool.stop} must be inside machine network {cluster.machine_network.cidr}")
                    for reserved in (cluster.api_vip, cluster.ingress_vip):
                        if reserved:
                            reserved_ip = ipaddress.IPv4Address(reserved)
                            if start_ip <= reserved_ip <= stop_ip:
                                errors.append(f"{prefix}: managementLoadBalancerPool overlaps reserved VIP {reserved}")
                except ValueError as exc:
                    errors.append(f"{prefix}: invalid managementLoadBalancerPool address: {exc}")

        if cluster.parent_networking:
            cluster_node_names = {node.name for node in cluster.nodes}
            for peering in cluster.parent_networking.peering_networks:
                if peering.purpose != "hostedClusterApi":
                    continue
                peering_prefix = (
                    f"{prefix}: day2GitOps.parentNetworking.peeringNetworks"
                    f"[{peering.network_ref}]"
                )
                if peering.bgp_source_address_mode not in {
                    "sourceInterface", "nodeConfigOverride"
                }:
                    errors.append(
                        f"{peering_prefix}.bgpSourceAddressMode must be "
                        "sourceInterface or nodeConfigOverride"
                    )
                if peering.bgp_source_address_mode == "nodeConfigOverride":
                    missing_nodes = sorted(
                        cluster_node_names - set(peering.parent_node_peering_ips)
                    )
                    if missing_nodes:
                        errors.append(
                            f"{peering_prefix} nodeConfigOverride requires a local address "
                            f"for every selected node; missing {missing_nodes}"
                        )

        # Node IPs in machine network
        if cluster.machine_network and cluster.machine_network.cidr:
            mn_net = ipaddress.IPv4Network(cluster.machine_network.cidr, strict=False)
            seen_ips: set[str] = set()
            seen_hostnames: set[str] = set()
            for node in cluster.nodes:
                ip = ipaddress.IPv4Address(node.ip)
                if ip not in mn_net:
                    errors.append(f"{prefix}: node {node.name} IP {node.ip} not in machine network {cluster.machine_network.cidr}")
                if node.ip in seen_ips:
                    errors.append(f"{prefix}: duplicate node IP {node.ip}")
                seen_ips.add(node.ip)
                if node.hostname in seen_hostnames:
                    errors.append(f"{prefix}: duplicate hostname {node.hostname}")
                seen_hostnames.add(node.hostname)

        # Hosted cluster validation. Product installation is parent-owned; every
        # attachment and allocator decision is owned by this explicit selector.
        address_claims_by_scope: dict[str, tuple[str, str, ipaddress.IPv4Network]] = {}
        dhcp_consumers: dict[str, list[str]] = {}
        dhcp_publishers: dict[str, list[str]] = {}
        dhcp_ranges: dict[str, tuple[ipaddress.IPv4Address, ipaddress.IPv4Address]] = {}
        dhcp_replicas: dict[str, int] = {}
        for hcp in cluster.hosted_clusters:
            hp = f"HCPCluster({hcp.name})"

            profile_ref = hcp.worker_network_profile_ref
            if not profile_ref:
                errors.append(f"{hp}: spec.workerNetworkProfileRef is required; network facts do not authorize realization")
            elif profile_ref not in WORKER_PROFILE_REFS:
                errors.append(f"{hp}: unknown workerNetworkProfileRef {profile_ref!r}")
            elif not hcp.worker_attachment_kind or not hcp.worker_allocator:
                errors.append(f"{hp}: worker profile {profile_ref!r} has no resolved capability expansion")
            if hcp.worker_replica_max is not None:
                checkpoints = hcp.worker_replica_checkpoints
                if (hcp.worker_replica_min < 0
                        or hcp.worker_replica_max < hcp.worker_replica_min
                        or not checkpoints
                        or tuple(sorted(set(checkpoints))) != checkpoints
                        or checkpoints[0] != hcp.worker_replica_min
                        or checkpoints[-1] != hcp.worker_replica_max
                        or hcp.worker_replica_default not in checkpoints):
                    errors.append(f"{hp}: stagedRollout requires ordered checkpoints from minReplicas to maxReplicas and a selected default")
                if not hcp.worker_replica_min <= hcp.replicas <= hcp.worker_replica_max:
                    errors.append(
                        f"{hp}: nodePool.replicas {hcp.replicas} is outside stagedRollout bounds "
                        f"{hcp.worker_replica_min}..{hcp.worker_replica_max}"
                    )

            if hcp.network_type not in ("OVNKubernetes", "Cilium"):
                errors.append(f"{hp}: openshift.networkType must be OVNKubernetes or Cilium, got {hcp.network_type!r}")
            if cluster.release and hcp.ocp_version != cluster.release.ocp_minor:
                errors.append(f"{hp}: openshift.version {hcp.ocp_version!r} must match selected parent release minor {cluster.release.ocp_minor!r}")
            if not hcp.control_plane_ingress_load_balancer_vip:
                errors.append(f"{hp}: no controlPlaneIngress.loadBalancerVIPRef resolved")
            if hcp.kube_api_vip and hcp.control_plane_ingress_load_balancer_vip:
                errors.append(f"{hp}: api.kubeApiVIPRef is legacy and must not be set with all-Route controlPlaneIngress")
            if len(hcp.worker_network_attachments) != 1:
                errors.append(
                    f"{hp}: single-NIC worker profile requires exactly one workload attachment, "
                    f"got {len(hcp.worker_network_attachments)}"
                )
            for network_ref in hcp.worker_networks:
                if hcp.uses_multus_workers and network_ref not in platform.all_networks:
                    errors.append(f"{hp}: workerNetworks references unknown tenant network {network_ref!r}")
            if len(set(hcp.worker_networks)) != len(hcp.worker_networks):
                errors.append(f"{hp}: workerNetworks must not contain duplicates")
            default_route_attachments = [
                attachment.network_ref
                for attachment in hcp.worker_network_attachments
                if attachment.guest_default_route
            ]
            if len(default_route_attachments) != 1:
                errors.append(
                    f"{hp}: worker profile requires exactly one owned guestDefaultRoute=true "
                    f"attachment, got {default_route_attachments}"
                )

            if hcp.uses_multus_workers and len(hcp.resolved_worker_networks) != 1:
                errors.append(f"{hp}: Multus worker profile must resolve exactly one management-owned NAD")
            if hcp.uses_multus_workers and (
                hcp.worker_egress_gateway_placement != "hosted-worker"
                or hcp.worker_bgp_owners != ("management", "hosted-worker")
            ):
                errors.append(
                    f"{hp}: Multus L2 worker profile must preserve hosted-worker EGW placement "
                    "and management plus hosted-worker BGP ownership"
                )
            if not hcp.cilium_bgp_enabled and (
                hcp.cilium_advertise_lb
                or hcp.cilium_advertise_egress
                or hcp.cilium_advertise_pods
            ):
                errors.append(
                    f"{hp}: BGP advertisements require cilium.bgp.enabled=true"
                )

            if hcp.uses_central_dhcp and len(hcp.worker_dhcp_scopes) != 1:
                errors.append(f"{hp}: central-DHCP worker profile must resolve exactly one address authority")
            for scope in hcp.worker_dhcp_scopes:
                try:
                    authority = ipaddress.IPv4Network(scope.address_authority, strict=False)
                    server = ipaddress.IPv4Address(scope.server_address)
                    range_start = ipaddress.IPv4Address(scope.pool_start)
                    range_end = ipaddress.IPv4Address(scope.pool_end)
                except ValueError as exc:
                    errors.append(f"{hp}: invalid DHCP scope {scope.scope_id}: {exc}")
                    continue
                if server not in authority or range_start not in authority or range_end not in authority:
                    errors.append(f"{hp}: DHCP server and pool for {scope.scope_id} must be inside {authority}")
                if range_start in {authority.network_address, authority.broadcast_address} or range_end in {
                    authority.network_address,
                    authority.broadcast_address,
                }:
                    errors.append(f"{hp}: DHCP pool for {scope.scope_id} must not use network or broadcast addresses")
                if range_start > range_end:
                    errors.append(f"{hp}: DHCP pool start for {scope.scope_id} must be <= pool end")
                if range_start <= server <= range_end:
                    errors.append(f"{hp}: DHCP pool for {scope.scope_id} must not include the server")

                if scope.delivery != "broadcast":
                    errors.append(f"{hp}: same-domain DHCP scope must use broadcast delivery")
                if scope.attachment_kind == "multus-l2":
                    net = hcp.resolved_worker_networks[0] if len(hcp.resolved_worker_networks) == 1 else None
                    if net is not None and not net.worker_peering.dhcp:
                        errors.append(f"{hp}: central-DHCP profile requires {net.name}.subnets.workers.dhcp")
                    if scope.allocation != "dynamic":
                        errors.append(f"{hp}: Multus DHCP scope must use dynamic DHCP allocation")
                    if net is not None and (
                        scope.server_address != net.worker_peering.server_address
                        or scope.pool_start != net.worker_peering.pool_start
                        or scope.pool_end != net.worker_peering.pool_end
                        or scope.emitted_options != net.worker_peering.emitted_options
                    ):
                        errors.append(f"{hp}: resolved Multus DHCP scope does not match NetworkCatalog input")
                else:
                    errors.append(f"{hp}: DHCP is only authorized for the Multus L2 profile")

                existing = address_claims_by_scope.get(scope.scope_id)
                if existing and existing[2] != authority:
                    errors.append(
                        f"{prefix}: DHCP scope {scope.scope_id} has conflicting address authorities "
                        f"{existing[2]} and {authority}"
                    )
                else:
                    address_claims_by_scope[scope.scope_id] = (hcp.name, profile_ref, authority)
                consumers = dhcp_consumers.setdefault(scope.server_address, [])
                if hcp.name not in consumers:
                    consumers.append(hcp.name)
                if hcp.publish_worker_dhcp:
                    publishers = dhcp_publishers.setdefault(scope.server_address, [])
                    if hcp.name not in publishers:
                        publishers.append(hcp.name)
                dhcp_ranges[scope.server_address] = (range_start, range_end)
                dhcp_replicas[scope.server_address] = dhcp_replicas.get(scope.server_address, 0) + hcp.replicas

            # LB/egress pools must belong to a worker network attachment
            worker_net_names = set(hcp.worker_networks)
            for pool in hcp.resolved_lb_pools:
                if pool.owning_network not in worker_net_names:
                    errors.append(f"{hp}: LB pool {pool.name} belongs to {pool.owning_network}, not in worker attachments {worker_net_names}")
            app_ingress = hcp.application_ingress
            if app_ingress:
                if app_ingress.mode != "direct-bgp-router":
                    errors.append(f"{hp}: applicationIngress.mode must be direct-bgp-router")
                if not hcp.uses_multus_workers or not hcp.cilium_bgp_enabled or not hcp.cilium_advertise_lb:
                    errors.append(f"{hp}: applicationIngress requires Multus workers and direct Cilium Service BGP")
                matching_pools = [p for p in hcp.resolved_lb_pools if p.name == app_ingress.pool_ref]
                if len(matching_pools) != 1:
                    errors.append(f"{hp}: applicationIngress.poolRef must resolve to one selected LB pool")
                else:
                    pool_network = ipaddress.IPv4Network(matching_pools[0].cidr, strict=True)
                    try:
                        vip = ipaddress.IPv4Address(app_ingress.vip)
                    except ipaddress.AddressValueError:
                        errors.append(f"{hp}: applicationIngress.vip must be an IPv4 address")
                    else:
                        selected = matching_pools[0]
                        outside_pool = (
                            vip not in pool_network
                            or vip in (pool_network.network_address, pool_network.broadcast_address)
                        )
                        if selected.allocation_start and selected.allocation_stop:
                            start = ipaddress.IPv4Address(selected.allocation_start)
                            stop = ipaddress.IPv4Address(selected.allocation_stop)
                            if vip < start or vip > stop:
                                outside_pool = True
                        if outside_pool:
                            errors.append(f"{hp}: applicationIngress.vip must be a usable address in {pool_network}")
                base_domain = identity["baseDomain"]
                if app_ingress.dns_wildcard != f"*.apps.{hcp.name}.{base_domain}":
                    errors.append(f"{hp}: applicationIngress.dnsWildcard must match the hosted apps domain")
                if app_ingress.dns_zone != base_domain or not app_ingress.dns_authority:
                    errors.append(f"{hp}: applicationIngress requires explicit spec.identity.baseDomain DNS authority")
                if app_ingress.dns_publication != "external-manual":
                    errors.append(f"{hp}: applicationIngress DNS publication must remain external-manual")
            for pool in hcp.resolved_egress_pools:
                if pool.owning_network not in worker_net_names:
                    errors.append(f"{hp}: egress pool {pool.name} belongs to {pool.owning_network}, not in worker attachments {worker_net_names}")

        for server, consumers in sorted(dhcp_consumers.items()):
            publishers = dhcp_publishers.get(server, [])
            if len(publishers) != 1:
                errors.append(
                    f"{prefix}: DHCP server {server} consumed by {consumers} "
                    f"must have exactly one publisher, got {publishers or ['none']}"
                )
            range_start, range_end = dhcp_ranges[server]
            capacity = int(range_end) - int(range_start) + 1
            if dhcp_replicas.get(server, 0) > capacity:
                errors.append(
                    f"{prefix}: DHCP server {server} range {range_start}-{range_end} "
                    f"has {capacity} addresses for {dhcp_replicas[server]} workers"
                )

        unique_claims = list(address_claims_by_scope.items())
        for index, (left_scope, (left_hcp, left_profile, left_claim)) in enumerate(unique_claims):
            for right_scope, (right_hcp, right_profile, right_claim) in unique_claims[index + 1:]:
                if left_claim.overlaps(right_claim):
                    errors.append(
                        f"{prefix}: worker allocation scopes have overlapping address authority "
                        f"{left_hcp}/{left_profile}/{left_scope}={left_claim} and "
                        f"{right_hcp}/{right_profile}/{right_scope}={right_claim}"
                    )

        if cluster.ucs_host_networking:
            mgmt_pair = cluster.ucs_host_networking.management
            mgmt_names = mgmt_pair.names
            if len(mgmt_names) != 2:
                errors.append(f"{prefix}: UCS managementVNICPair must have exactly two names")
            if not mgmt_pair.native_vlan:
                errors.append(f"{prefix}: UCS managementVNICPair must reference a native VLAN")
            if mgmt_pair.tagged_vlans:
                errors.append(f"{prefix}: UCS managementVNICPair must not carry tagged tenant VLANs")

            if cluster.ucs_host_networking.tenant:
                tenant_pair = cluster.ucs_host_networking.tenant
                tenant_names = tenant_pair.names
                if len(tenant_names) != 2:
                    errors.append(f"{prefix}: UCS tenantVNICPair must have exactly two names")
                overlap = set(mgmt_names) & set(tenant_names)
                if overlap:
                    errors.append(f"{prefix}: UCS management and tenant vNIC names overlap: {sorted(overlap)}")
                if tenant_pair.native_vlan:
                    errors.append(f"{prefix}: UCS tenantVNICPair must not define a native VLAN")
                if not tenant_pair.tagged_vlans:
                    errors.append(f"{prefix}: UCS tenantVNICPair is enabled but has no tagged VLANs")
                required_tenant_vlans = {n.vlan for n in cluster.kube_api_networks + cluster.tenant_networks}
                selected_tenant_vlans = {n.vlan for n in tenant_pair.tagged_vlans}
                missing = required_tenant_vlans - selected_tenant_vlans
                if missing:
                    errors.append(f"{prefix}: UCS tenantVNICPair missing selected GitOps/HCP VLANs {sorted(missing)}")
                if mgmt_pair.native_vlan and mgmt_pair.native_vlan.vlan in selected_tenant_vlans:
                    errors.append(f"{prefix}: UCS tenantVNICPair must not include management/native VLAN {mgmt_pair.native_vlan.vlan}")

    # Peering subnet overlap checks
    peering_nets = []
    for net in platform.all_networks.values():
        if net.peering_subnet:
            peering_nets.append((net.name, ipaddress.IPv4Network(net.peering_subnet, strict=False)))
    for i, (n1, s1) in enumerate(peering_nets):
        for n2, s2 in peering_nets[i+1:]:
            if s1.overlaps(s2):
                errors.append(f"peering subnets overlap: {n1}({s1}) vs {n2}({s2})")

    # Tenant worker-network DHCP checks.
    for net in platform.all_networks.values():
        if net.category != "tenant":
            continue
        if not net.worker_peering.dhcp:
            continue
        if not net.peering_subnet:
            errors.append(f"network {net.name}: subnets.workers.dhcp requires subnets.workers.cidr")
            continue
        try:
            subnet = ipaddress.IPv4Network(net.peering_subnet, strict=False)
        except ValueError as exc:
            errors.append(f"network {net.name}: invalid subnets.workers.cidr for DHCP: {exc}")
            continue
        if platform.defaults:
            worker_dhcp = platform.defaults.gitops_worker_dhcp
            try:
                lease_seconds = int(worker_dhcp.get("leaseSeconds", 0) or 0)
            except (TypeError, ValueError) as exc:
                errors.append(f"PlatformDefaults: spec.gitops.workerDhcp.leaseSeconds is invalid: {exc}")
            else:
                if lease_seconds < 60:
                    errors.append("PlatformDefaults: spec.gitops.workerDhcp.leaseSeconds must be at least 60")

            fabric_reserved = set()
            for peer in net.svi_peers:
                try:
                    address = ipaddress.IPv4Interface(peer.ip).ip
                except ValueError as exc:
                    errors.append(f"network {net.name}: invalid SVI peer address {peer.ip!r}: {exc}")
                    continue
                if address not in subnet:
                    errors.append(f"network {net.name}: SVI peer address {peer.ip} is outside peeringSubnet {subnet}")
                    continue
                fabric_reserved.add(address)
            for value in [net.floating_ip, net.path_floating_ip]:
                if not value:
                    continue
                try:
                    address = ipaddress.IPv4Interface(value).ip
                except ValueError as exc:
                    errors.append(f"network {net.name}: invalid floating SVI address {value!r}: {exc}")
                    continue
                if address not in subnet:
                    errors.append(f"network {net.name}: floating SVI address {value} is outside peeringSubnet {subnet}")
                    continue
                fabric_reserved.add(address)
            errors.extend(_dhcp_authority_errors(
                f"network {net.name}.peeringSubnet",
                str(subnet),
                net.worker_peering,
                fabric_reserved,
            ))

    # ACI tenant required for networks rendered into dynamic ACI output.
    # Domain VLANs such as VLAN 1/default and IMC inband are target-owned UCS
    # defaults, not SAIF-rendered ACI tenant networks.
    for net in platform.all_networks.values():
        if net.category in {"kubeAPI", "tenant", "fabricTransit", "endpoint"} and not net.aci_tenant:
            errors.append(f"network {net.name}: missing aci.tenant")

    # Physical domain required if ACI networks exist
    has_aci = any(n.aci_tenant for n in platform.all_networks.values())
    if has_aci and not platform.fabric.physical_domain:
        errors.append("ACI networks present but fabric.aci.physicalDomain is missing")

    # Shared AAEPs required if ACI networks exist
    if has_aci and platform.fabric.aci and not platform.fabric.aci.shared_aaep:
        errors.append("ACI networks present but fabric.aci.sharedAAEP is missing")
    if has_aci and platform.fabric.aci and not platform.fabric.aci.ai_pod_aaep:
        errors.append("ACI networks present but fabric.aci.aiPodAAEP is missing")

    # ACI defaults required if ACI networks exist
    if has_aci and not platform.aci_defaults:
        errors.append("ACI networks present but data/defaults/aci.yaml is missing")

    # Router IDs: kubeAPI and fabricTransit networks must map to declared ACI
    # border leafs. ACI permits multiple L3Outs in a VRF, but for BGP L3Outs
    # sharing the same tenant VRF a leaf node must use one router ID consistently.
    border_leaf_ids = {n["nodeId"] for n in platform.fabric.border_leaf_nodes}
    bgp_router_ids_by_tenant_node: dict[tuple[str, int], tuple[str, str]] = {}
    for net in platform.all_networks.values():
        if net.category == "management" or not net.peering_subnet or not net.aci_tenant:
            continue
        rid = net.router_ids
        if net.category in {"tenant", "kubeAPI"}:
            if not rid:
                errors.append(f"network {net.name}: missing aci.routerIds")
            if net.category == "kubeAPI":
                for leaf_id in border_leaf_ids:
                    if leaf_id not in rid:
                        errors.append(f"network {net.name}: missing aci.routerIds for leaf {leaf_id}")
            for node_id, router_id in rid.items():
                key = (net.aci_tenant, node_id)
                previous = bgp_router_ids_by_tenant_node.get(key)
                if previous and previous[1] != router_id:
                    errors.append(
                        f"tenant {net.aci_tenant} node {node_id}: BGP L3Out router ID mismatch "
                        f"between {previous[0]} ({previous[1]}) and {net.name} ({router_id})"
                    )
                else:
                    bgp_router_ids_by_tenant_node[key] = (net.name, router_id)
            continue
        if net.category == "fabricTransit":
            if not rid:
                errors.append(f"network {net.name}: missing aci.routerIds")
            if net.cilium_asn is not None:
                errors.append(f"network {net.name}: fabricTransit must not define bgp.ciliumASN")
            if not net.aci or not net.aci.ospf or not net.aci.ospf.area_id:
                errors.append(f"network {net.name}: fabricTransit requires ospf.areaId")
            if not net.aci or not net.aci.ospf or not net.aci.ospf.node_interface_ips:
                errors.append(f"network {net.name}: fabricTransit requires ospf.nodeInterfaceIPs")

    # API VIP, control-plane ingress VIP, and HCP management-services route-class validation
    vip_names: dict[str, str] = {}
    control_plane_ingress_vip_names: dict[str, str] = {}
    hcp_pool_names: dict[str, str] = {}
    for net in platform.all_networks.values():
        for pool in net.hcp_management_services:
            if pool.name in hcp_pool_names:
                errors.append(f"duplicate HCP management-services pool name {pool.name!r} in {net.name} and {hcp_pool_names[pool.name]}")
            hcp_pool_names[pool.name] = net.name
        for vip in net.api_vips:
            if vip.name in vip_names:
                errors.append(f"duplicate API VIP name {vip.name!r} in {net.name} and {vip_names[vip.name]}")
            vip_names[vip.name] = net.name
            if net.hcp_management_services:
                vip_addr = ipaddress.IPv4Interface(vip.cidr).ip
                if not any(vip_addr in ipaddress.IPv4Network(pool.cidr, strict=False) for pool in net.hcp_management_services):
                    errors.append(f"API VIP {vip.name} {vip.cidr} is not covered by HCP management-services pools on {net.name}")
            if ipaddress.IPv4Interface(vip.cidr).network.prefixlen != 32:
                errors.append(f"API VIP {vip.name} {vip.cidr} must be a /32")
        for vip in net.control_plane_ingress_load_balancer_vips:
            if vip.name in control_plane_ingress_vip_names:
                errors.append(
                    f"duplicate control-plane ingress LoadBalancer VIP name {vip.name!r} "
                    f"in {net.name} and {control_plane_ingress_vip_names[vip.name]}"
                )
            control_plane_ingress_vip_names[vip.name] = net.name
            vip_iface = ipaddress.IPv4Interface(vip.cidr)
            if vip_iface.network.prefixlen != 32:
                errors.append(f"control-plane ingress LoadBalancer VIP {vip.name} {vip.cidr} must be a /32")
            if net.hcp_management_services:
                vip_addr = vip_iface.ip
                if not any(vip_addr in ipaddress.IPv4Network(pool.cidr, strict=False) for pool in net.hcp_management_services):
                    errors.append(
                        f"control-plane ingress LoadBalancer VIP {vip.name} {vip.cidr} "
                        f"is not covered by HCP management-services pools on {net.name}"
                    )

    # Pool name uniqueness
    for kind in ("loadBalancer", "egress"):
        pool_names: dict[str, str] = {}
        pool_bounds: list[tuple[str, ipaddress.IPv4Address, ipaddress.IPv4Address]] = []
        for net in platform.all_networks.values():
            pools = net.pools_lb if kind == "loadBalancer" else net.pools_egress
            for pool in pools:
                if pool.name in pool_names:
                    errors.append(f"duplicate {kind} pool name {pool.name!r} in {net.name} and {pool_names[pool.name]}")
                pool_names[pool.name] = net.name
                try:
                    network = ipaddress.IPv4Network(pool.cidr, strict=True)
                    if bool(pool.allocation_start) != bool(pool.allocation_stop):
                        errors.append(f"{kind} pool {pool.name} allocation requires both start and stop")
                        continue
                    if pool.allocation_start:
                        start = ipaddress.IPv4Address(pool.allocation_start)
                        stop = ipaddress.IPv4Address(pool.allocation_stop)
                        if start > stop or start not in network or stop not in network:
                            errors.append(f"{kind} pool {pool.name} allocation must be inside {network}")
                            continue
                        if start in (network.network_address, network.broadcast_address) or stop in (
                            network.network_address, network.broadcast_address,
                        ):
                            errors.append(f"{kind} pool {pool.name} allocation must not use network or broadcast addresses")
                            continue
                    else:
                        start, stop = network.network_address, network.broadcast_address
                except ValueError as exc:
                    errors.append(f"{kind} pool {pool.name} has an invalid range: {exc}")
                    continue
                pool_bounds.append((pool.name, start, stop))
        for index, (left_name, left_start, left_stop) in enumerate(pool_bounds):
            for right_name, right_start, right_stop in pool_bounds[index + 1:]:
                if left_start <= right_stop and right_start <= left_stop:
                    errors.append(
                        f"overlapping {kind} pools {left_name} {left_start}-{left_stop} "
                        f"and {right_name} {right_start}-{right_stop}"
                    )

    return errors


def _resolve_worker_replica_policy(
    raw: Any,
    replicas: Any,
    hcp_name: str,
) -> tuple[int, int, int, int | None, tuple[int, ...]]:
    """Resolve bounds for gated changes to the production worker NodePool."""
    if raw is None:
        if replicas is None:
            replicas = 0
        if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
            raise ValueError(
                f"HCPCluster({hcp_name}): spec.nodePool.replicas must be a non-negative integer"
            )
        return replicas, 0, 0, None, ()
    if not isinstance(raw, dict):
        raise ValueError(f"HCPCluster({hcp_name}): spec.nodePool.stagedRollout must be a mapping")

    values = {
        "defaultReplicas": raw.get("defaultReplicas", 0),
        "minReplicas": raw.get("minReplicas", 0),
        "maxReplicas": raw.get("maxReplicas"),
    }
    for field_name, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(
                f"HCPCluster({hcp_name}): spec.nodePool.stagedRollout.{field_name} "
                "must be a non-negative integer"
            )
    checkpoints = raw.get("checkpoints", [])
    if not isinstance(checkpoints, list) or any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in checkpoints
    ):
        raise ValueError(
            f"HCPCluster({hcp_name}): spec.nodePool.stagedRollout.checkpoints "
            "must be a list of non-negative integers"
        )
    if replicas is None:
        replicas = values["defaultReplicas"]
    if isinstance(replicas, bool) or not isinstance(replicas, int) or replicas < 0:
        raise ValueError(
            f"HCPCluster({hcp_name}): spec.nodePool.replicas must be a non-negative integer"
        )
    return (
        replicas,
        values["defaultReplicas"],
        values["minReplicas"],
        values["maxReplicas"],
        tuple(checkpoints),
    )


def _resolve_worker_network_attachments(raw: Any, hcp_name: str) -> list[ResolvedWorkerNetworkAttachment]:
    """Normalize HCP workerNetworks shorthand or attachment objects."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError(f"HCPCluster({hcp_name}): networking.workerNetworks must be a list")

    parsed: list[tuple[str, bool | None, str]] = []
    for index, item in enumerate(raw):
        if isinstance(item, str):
            parsed.append((item, None, ""))
            continue
        if not isinstance(item, dict):
            raise ValueError(
                f"HCPCluster({hcp_name}): networking.workerNetworks[{index}] "
                "must be a string or mapping"
            )
        network_ref = item.get("networkRef")
        if not isinstance(network_ref, str) or not network_ref:
            raise ValueError(
                f"HCPCluster({hcp_name}): networking.workerNetworks[{index}].networkRef "
                "is required"
            )
        guest_default_route = item.get("guestDefaultRoute")
        if guest_default_route is not None and not isinstance(guest_default_route, bool):
            raise ValueError(
                f"HCPCluster({hcp_name}): networking.workerNetworks[{index}].guestDefaultRoute "
                "must be a boolean"
            )
        subnet_ref = item.get("subnetRef", "")
        if not isinstance(subnet_ref, str):
            raise ValueError(
                f"HCPCluster({hcp_name}): networking.workerNetworks[{index}].subnetRef "
                "must be a string"
            )
        parsed.append((network_ref, guest_default_route, subnet_ref))

    if len(parsed) == 1 and parsed[0][1] is None:
        parsed[0] = (parsed[0][0], True, parsed[0][2])

    return [
        ResolvedWorkerNetworkAttachment(
            network_ref=network_ref,
            guest_default_route=bool(guest_default_route),
            subnet_ref=subnet_ref,
        )
        for network_ref, guest_default_route, subnet_ref in parsed
    ]


def _resolve_hcp_worker_attachment_contract(hcp: ResolvedHCPCluster,
                                            all_networks: dict[str, ResolvedNetwork],
                                            defaults: ResolvedDefaults) -> None:
    """Resolve the selected single-NIC Multus attachment and DHCP authority."""
    hcp.resolved_worker_networks = []
    hcp.worker_dhcp_scopes = []
    hcp.worker_ipam_scopes = []
    if len(hcp.worker_network_attachments) != 1:
        return

    attachment = hcp.worker_network_attachments[0]
    if hcp.uses_multus_workers:
        if attachment.subnet_ref:
            raise ValueError(f"HCPCluster({hcp.name}): subnetRef is not used by a Multus worker")
        net = all_networks.get(attachment.network_ref)
        if net is None:
            return
        attachment.attachment_name = slugify(net.name)
        attachment.network_name = net.name
        attachment.subnet_name = f"vlan-{net.vlan}"
        hcp.resolved_worker_networks.append(net)
        if not hcp.uses_central_dhcp or not net.worker_peering.dhcp:
            return
        gateway = ""
        dns_servers: list[str] = []
        if attachment.guest_default_route:
            if net.floating_ip:
                gateway = str(ipaddress.IPv4Interface(net.floating_ip).ip)
            else:
                subnet = ipaddress.IPv4Network(net.peering_subnet, strict=False)
                gateway = str(ipaddress.IPv4Address(int(subnet.network_address) + 3))
            dns_servers = list(defaults.dns_servers)
        hcp.worker_dhcp_scopes.append(ResolvedWorkerDhcpScope(
            profile_ref=hcp.worker_network_profile_ref,
            attachment_kind=hcp.worker_attachment_kind,
            network_ref=attachment.network_ref,
            attachment_name=attachment.attachment_name,
            network_name=attachment.network_name,
            subnet_name=attachment.subnet_name,
            address_authority=str(ipaddress.IPv4Network(net.peering_subnet, strict=False)),
            server_address=net.worker_peering.server_address,
            pool_start=net.worker_peering.pool_start,
            pool_end=net.worker_peering.pool_end,
            router=gateway,
            dns_servers=dns_servers,
            mtu=None,
            delivery="broadcast",
            allocation="dynamic",
            emitted_options=list(net.worker_peering.emitted_options),
            exposure="local-access",
        ))

# ---------------------------------------------------------------------------
# Main resolve function
# ---------------------------------------------------------------------------

def hosted_control_plane_name(root: Path, requested: str | None = None) -> str:
    """Return the requested cluster, or the only cluster that hosts control planes."""
    if requested:
        return requested
    names = [
        cluster.name
        for cluster in resolve(root).clusters
        if cluster.features.get("hostedControlPlanes")
    ]
    if len(names) != 1:
        raise ValueError(
            "pass --cluster; the inventory does not contain exactly one hosted control plane"
        )
    return names[0]


def resolve(root: Path, *, load_cache: bool = True) -> ResolvedPlatform:
    """Resolve all contract files under root into a ResolvedPlatform.

    Raises ValueError if contract files are missing or structurally broken.
    Call validate() on the result for semantic validation.
    """
    root = root.resolve()

    # Discover files
    networks_dir = _input_dir(root, "networks")
    network_files = sorted(networks_dir.glob("*.yaml"))
    if not network_files:
        raise ValueError(f"No NetworkCatalog files in {networks_dir}")

    # Load first catalog (current design has one)
    cat_data = _load_yaml(network_files[0])
    if cat_data.get("kind") != "NetworkCatalog":
        raise ValueError(f"{network_files[0]}: kind must be NetworkCatalog")
    catalog_name = cat_data.get("metadata", {}).get("name", network_files[0].stem)
    profile_intent = resolve_profile_intent(root)
    profile_expansions = {
        (management.name, hosted.name): hosted.worker_profile_expansion
        for management in profile_intent.management_clusters
        for hosted in management.hosted_clusters
    } if profile_intent else {}

    # Resolve fabric
    spec = cat_data.get("spec", {})
    fabric_spec = spec.get("fabric", {})
    aci_spec = fabric_spec.get("aci", {})
    border_leaf_dicts = aci_spec.get("borderLeafNodes", [])

    aci_fabric = ResolvedAciFabric(
        fabric_asn=fabric_spec.get("fabricASN", 0),
        border_leaf_nodes=[ResolvedAciLeaf(node_id=n["nodeId"]) for n in border_leaf_dicts],
        physical_domain=aci_spec.get("physicalDomain"),
        shared_aaep=aci_spec.get("sharedAAEP"),
        ai_pod_aaep=aci_spec.get("aiPodAAEP"),
        transit_aaep=aci_spec.get("transitAAEP"),
        tenant_ports=copy.deepcopy(aci_spec.get("tenantPorts", [])),
    ) if border_leaf_dicts else None

    fabric = ResolvedFabric(
        fabric_asn=fabric_spec.get("fabricASN", 0),
        border_leaf_nodes=border_leaf_dicts,
        physical_domain=aci_spec.get("physicalDomain"),
        aci=aci_fabric,
    )

    # Resolve catalog networks
    all_networks = _resolve_catalog(cat_data, fabric.border_leaf_nodes)
    # Load named release profiles and infrastructure defaults.
    release_profiles = _load_release_profiles(root)
    cilium_config_profiles = _load_cilium_config_profiles(root)
    defaults = _load_defaults(root)

    # Load ACI defaults
    aci_defaults = _load_aci_defaults(root)
    ucs_defaults = _load_ucs_defaults(root)

    # Discover cluster files
    clusters_dir = _input_dir(root, "clusters")
    ocp_clusters: list[ResolvedOCPCluster] = []

    if clusters_dir.is_dir():
        for cluster_dir in sorted(clusters_dir.iterdir()):
            if not cluster_dir.is_dir():
                continue
            cluster_yaml = cluster_dir / "cluster.yaml"
            if not cluster_yaml.exists():
                continue
            cdata = _load_yaml(cluster_yaml)
            if cdata.get("kind") != "OCPCluster":
                continue

            cmeta = cdata.get("metadata", {})
            cspec = cdata.get("spec", {})
            install_spec = cspec.get("installConfig", {})
            agent_spec = cspec.get("agentConfig", {})
            day2_spec = cspec.get("day2GitOps", {})
            ocp_spec = install_spec.get("openshift", {})
            net_spec = install_spec.get("networking", {})
            capabilities = install_spec.get("capabilities", {})
            if isinstance(capabilities, dict):
                capabilities = [name for name, enabled in capabilities.items() if enabled]

            release_ref = cspec.get("releaseRef", "")
            current_release_ref = cspec.get("currentReleaseRef")
            target_release_ref = cspec.get("targetReleaseRef")
            release = release_profiles.get(release_ref)
            current_release = release_profiles.get(current_release_ref) if current_release_ref else None
            target_release = release_profiles.get(target_release_ref) if target_release_ref else None
            target_cilium_config_profile = None

            # Resolve machine network
            mn_name = net_spec.get("machineNetwork")
            machine_net = all_networks.get(mn_name) if mn_name else None

            # Resolve Day-1 AgentConfig nodes
            management_interface_spec = agent_spec.get("managementInterface", {})
            management_interface = ResolvedManagementInterface(
                bond=management_interface_spec.get("bond", ""),
                members=management_interface_spec.get("members", []),
                mode=management_interface_spec.get("mode", ""),
                options=copy.deepcopy(management_interface_spec.get("options", {})),
            )
            ucs_spec = cspec.get("ucs", {})
            profile_template = ucs_spec.get("profileNameTemplate")
            nodes = []
            for n in agent_spec.get("nodes", []):
                node_name = n["name"]
                hostname = n.get("hostname") or f"{node_name}.{ocp_spec.get('baseDomain', '')}"
                ucs_profile = None
                if profile_template:
                    ucs_profile = profile_template.replace("{node.name}", node_name)
                nodes.append(ResolvedNode(
                    name=node_name,
                    hostname=hostname,
                    ip=n.get("ip", ""),
                    serial=n.get("serial"),
                    ucs_profile=ucs_profile,
                    interfaces=copy.deepcopy(n.get("interfaces", [])),
                    root_device_hints=copy.deepcopy(n.get("rootDeviceHints", {})),
                ))

            # Resolve Day-2 parent networking before constructing the cluster.
            parent_networking_spec = day2_spec.get("parentNetworking", {})
            tenant_interface_spec = parent_networking_spec.get("tenantInterface", {})
            management_lb_pool_spec = parent_networking_spec.get("managementLoadBalancerPool") or {}
            management_lb_pool = None
            if management_lb_pool_spec:
                management_lb_pool = ResolvedManagementLoadBalancerPool(
                    name=management_lb_pool_spec.get("name", ""),
                    start=management_lb_pool_spec.get("start", ""),
                    stop=management_lb_pool_spec.get("stop", ""),
                    service_selector=copy.deepcopy(management_lb_pool_spec.get("serviceSelector") or {}),
                )
            gitops_addons = day2_spec.get("addOns", {}) or {}
            gitops_addon_selections = day2_spec.get("addonSelections") or {}
            cilium_config_spec = day2_spec.get("ciliumConfig", {}) or {}
            image_overrides = {}
            for component, spec in (cilium_config_spec.get("imageOverrides", {}) or {}).items():
                image_overrides[component] = ResolvedCiliumImageOverride(
                    enabled=spec.get("enabled", False),
                    repository=spec.get("repository", ""),
                    tag=spec.get("tag", ""),
                    digest=spec.get("digest", ""),
                    generic_digest=spec.get("genericDigest", ""),
                    suffix=spec.get("suffix", ""),
                )
            cilium_config = ResolvedCiliumConfigIntent(
                prerelease_version=cilium_config_spec.get("prereleaseVersion", ""),
                ci_commit=cilium_config_spec.get("ciCommit", ""),
                ci_chart=cilium_config_spec.get("ciChart", ""),
                gateway_api_enabled=cilium_config_spec.get("gatewayAPI", {}).get("enabled"),
                image_overrides=image_overrides,
            )
            management_clife_spec = day2_spec.get("managementCLIFE", {}) or {}
            management_clife = ResolvedPreReleaseCLIFEIntent(
                enabled=management_clife_spec.get("enabled", False),
                lifecycle=management_clife_spec.get("lifecycle", "manifest"),
                manifest_bundle=management_clife_spec.get("manifestBundle", ""),
                image=management_clife_spec.get("image", ""),
                development_mode=management_clife_spec.get("developmentMode"),
                package_scenario=management_clife_spec.get("packageScenario", ""),
            )
            tenant_guest_cni_spec = day2_spec.get("tenantGuestCNI", {}) or {}
            tenant_guest_cni = ResolvedTenantGuestCNIIntent(
                lifecycle=tenant_guest_cni_spec.get("lifecycle", "olm"),
                package_release_ref=str(tenant_guest_cni_spec.get("packageReleaseRef", "")),
                package=(release_profiles.get(str(tenant_guest_cni_spec.get("packageReleaseRef", ""))).clife_package
                         if str(tenant_guest_cni_spec.get("packageReleaseRef", "")) in release_profiles else None),
                manifest_bundle=tenant_guest_cni_spec.get("manifestBundle", ""),
                channel=tenant_guest_cni_spec.get("channel", release.cilium_day1_channel if release else ""),
                starting_csv=tenant_guest_cni_spec.get("startingCSV", ""),
                install_plan_approval=tenant_guest_cni_spec.get("installPlanApproval", "Manual"),
                clife_image=tenant_guest_cni_spec.get("clifeImage", ""),
                development_mode=tenant_guest_cni_spec.get("developmentMode"),
                gateway_api_enabled=tenant_guest_cni_spec.get("gatewayAPI", {}).get("enabled"),
                route_import_enabled=tenant_guest_cni_spec.get("routeImport", {}).get("enabled"),
                package_scenarios=tenant_guest_cni_spec.get("packageScenarios", {}) or {},
            )
            ilb_spec = day2_spec.get("inClusterLoadBalancer", {}) or {}
            ilb_placement = ilb_spec.get("placement", {}) or {}
            ilb_target_placement = ilb_spec.get("targetPlacement", {}) or {}
            ilb_cilium_config_profile_ref = str(ilb_spec.get("ciliumConfigProfileRef", ""))
            in_cluster_load_balancer = ResolvedInClusterLoadBalancerIntent(
                enabled=ilb_spec.get("enabled", False),
                lifecycle=ilb_spec.get("lifecycle", "staged"),
                router_replicas=ilb_spec.get("routerReplicas", 0),
                router_t1_nodes=ilb_placement.get("routerAndT1Nodes", []) or [],
                t2_nodes=ilb_placement.get("t2Nodes", []) or [],
                placement_job_image=ilb_placement.get("jobImage", ""),
                cilium_config_profile_ref=ilb_cilium_config_profile_ref,
                cilium_config_profile=cilium_config_profiles.get(ilb_cilium_config_profile_ref),
                target_release_ref=str(ilb_target_placement.get("releaseRef", "")),
                target_router_t1_nodes=ilb_target_placement.get("routerAndT1Nodes", []) or [],
                target_t2_nodes=ilb_target_placement.get("t2Nodes", []) or [],
                target_activation_overlay=str(ilb_target_placement.get("activationOverlay", "")),
            )

            # Parse peeringNetworks entries
            peering_network_entries: list[ResolvedPeeringNetworkEntry] = []
            for pn_item in parent_networking_spec.get("peeringNetworks", []):
                peering_network_entries.append(ResolvedPeeringNetworkEntry(
                    network_ref=pn_item.get("networkRef", ""),
                    purpose=pn_item.get("purpose", ""),
                    bgp_source_address_mode=pn_item.get(
                        "bgpSourceAddressMode", "sourceInterface"
                    ),
                    parent_node_peering_ips=pn_item.get("parentNodePeeringIPs", {}),
                ))

            delivery = ocp_spec.get("imageDelivery")
            if not isinstance(delivery, dict) or not {"mode", "scopes"} <= delivery.keys():
                raise ValueError("installConfig.openshift.imageDelivery requires explicit mode and scopes in input data")
            if "registryTrustPolicy" not in ocp_spec:
                raise ValueError("installConfig.openshift.registryTrustPolicy requires an explicit input policy")
            cluster = ResolvedOCPCluster(
                name=cmeta.get("name", cluster_dir.name),
                mode=cspec.get("mode", "compact"),
                features=cspec.get("features", {}),
                ocp_version=release.ocp_version if release else "",
                base_domain=ocp_spec.get("baseDomain", ""),
                network_catalog_name=ocp_spec.get("networkCatalog", ""),
                network_type=ocp_spec.get("networkType", ""),
                cilium_day2_migration=ocp_spec.get("ciliumDay2Migration", False),
                release_ref=release_ref,
                current_release_ref=current_release_ref,
                target_release_ref=target_release_ref,
                release=release,
                current_release=current_release,
                target_release=target_release,
                target_cilium_config_profile=target_cilium_config_profile,
                capabilities=capabilities,
                machine_network=machine_net,
                api_vip=net_spec.get("apiVIP"),
                ingress_vip=net_spec.get("ingressVIP"),
                nodes=nodes,
                management_interface=management_interface,
                inventory_mode=agent_spec.get("inventoryMode", "runtime"),
                registry_trust_policy=ocp_spec["registryTrustPolicy"],
                image_delivery_mode=delivery["mode"],
                image_delivery_scopes=delivery["scopes"],
                rendezvous_node=agent_spec.get("rendezvousNode", ""),
                day1_cilium_spec=copy.deepcopy(install_spec.get("cilium", {})),
                parent_networking=ResolvedParentNetworking(
                    tenant_bond=tenant_interface_spec.get("bond"),
                    tenant_members=tenant_interface_spec.get("members", []),
                    peering_networks=peering_network_entries,
                    management_lb_pool=management_lb_pool,
                ) if tenant_interface_spec or management_lb_pool else None,
                cilium_config=cilium_config,
                management_clife=management_clife,
                tenant_guest_cni=tenant_guest_cni,
                in_cluster_load_balancer=in_cluster_load_balancer,
                gitops_addons=gitops_addons,
                gitops_addon_selections=gitops_addon_selections,
                observability=day2_spec.get("observability", {}) or {},
                storage=day2_spec.get("storage", {}) or {},
                hosted_cluster_lifecycle=day2_spec.get("hostedClusterLifecycle", {}) or {},
                retained_hosted_network_claims=day2_spec.get("retainedHostedNetworkClaims", {}) or {},
                local_registry=_load_local_registry(cluster_dir),
                maintenance_vm_holds=day2_spec.get("maintenanceVmHolds", []) or [],
            )

            # Resolve immutable operator artifacts and selected image inventory once.
            # Renderers must consume this graph, including after input renaming.
            operator = cluster.local_registry.get("ocMirror", {})
            if operator.get("publish"):
                # Canonical selected images are explicit customer inputs. No
                # private release BOM, live registry or lab inventory fallback.
                selected_images = operator["selectedImages"]
                cluster.oc_mirror_inventory = {"roots": [
                    {"name": item["inventoryName"], "source": item["source"],
                     "digest": item["source"].rsplit("@", 1)[-1]}
                    for item in selected_images
                ]}
                install = operator["installation"]
                # Airgap prepares external bytes; resolution only verifies declared local input.
                artifact_contract_path = (root / install["artifactContractPath"]).resolve()
                if not artifact_contract_path.is_relative_to(root.resolve()):
                    raise ValueError("operator artifact contract outside selected root")
                artifact_contract = yaml.safe_load(artifact_contract_path.read_text())
                operator_release = operator["release"]
                bundle = next(item["source"] for item in operator_release["bootstrapImages"] if item["role"] == "bundle")
                if (artifact_contract["commit"] != operator_release["commit"]
                        or artifact_contract["version"] != operator_release["version"]
                        or artifact_contract["bundleImage"] != bundle):
                    raise ValueError("operator artifact provenance differs from selected release")
                artifact_pins = {}
                for item in artifact_contract["files"]:
                    relative = str(Path(install["artifactDirectory"]) / item["path"])
                    source = (root / relative).resolve()
                    if (relative in artifact_pins or not source.is_relative_to(root.resolve())
                            or not source.is_file()):
                        raise ValueError("operator artifact is outside selected root or missing; prepare declared inputs with Airgap first")
                    if hashlib.sha256(source.read_bytes()).hexdigest() != item["sha256"]:
                        raise ValueError("operator artifact differs from declared digest")
                    artifact_pins[relative] = item["sha256"]
                def operator_artifact(relative, expected):
                    if artifact_pins.get(relative) != expected:
                        raise ValueError("operator inspection pin differs from artifact contract")
                    source = (root / relative).resolve()
                    if not source.is_relative_to(root.resolve()) or not source.is_file():
                        raise ValueError("operator artifact is outside selected root or missing; prepare declared inputs with Airgap first")
                    raw = source.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != expected:
                        raise ValueError("operator artifact differs from declared digest")
                    return yaml.safe_load(raw)
                csv = operator_artifact(install["csvPath"], install["csvSha256"])
                bindings = [operator_artifact(item["path"], item["sha256"])
                            for item in install["namespaceBindings"]]
                cluster.oc_mirror_installation = {
                    "csv": csv, "csvPath": install["csvPath"], "csvSha256": install["csvSha256"],
                    "bindingPins": copy.deepcopy(install["namespaceBindings"]),
                    "bindingNamespaces": sorted({subject["namespace"] for binding in bindings
                                                  for subject in binding.get("subjects", [])
                                                  if subject.get("kind") == "ServiceAccount"}),
                }

            # Resolve hosted clusters
            hcp_dir = cluster_dir / "hcpclusters"
            kube_api_vip_refs = {
                item.get("name"): item.get("addressRef")
                for item in parent_networking_spec.get("kubeApiVIPs", [])
            }

            if hcp_dir.is_dir():
                for hcp_file in sorted(hcp_dir.glob("*.yaml")):
                    hdata = _load_yaml(hcp_file)
                    if hdata.get("kind") != "HCPCluster":
                        continue
                    hmeta = hdata.get("metadata", {})
                    hspec = hdata.get("spec", {})
                    hcp_name = hmeta.get("name", hcp_file.stem)
                    hnet = hspec.get("networking", {})
                    hcilium = hspec.get("cilium", {})
                    hbgp = hcilium.get("bgp", {})
                    worker_attachments = _resolve_worker_network_attachments(
                        hnet.get("workerNetworks", []), hcp_name
                    )
                    profile_expansion = profile_expansions.get((cluster.name, hcp_name))
                    node_pool_spec = hspec.get("nodePool", {}) or {}
                    if not isinstance(node_pool_spec, dict):
                        raise ValueError(f"HCPCluster({hcp_name}): spec.nodePool must be a mapping")
                    if "canary" in node_pool_spec:
                        raise ValueError(
                            f"HCPCluster({hcp_name}): spec.nodePool.canary is not a production "
                            "rollout target; use stagedRollout on the workers NodePool"
                        )
                    (
                        worker_replicas,
                        worker_replica_default,
                        worker_replica_min,
                        worker_replica_max,
                        worker_replica_checkpoints,
                    ) = _resolve_worker_replica_policy(
                        node_pool_spec.get("stagedRollout"),
                        node_pool_spec.get("replicas"),
                        hcp_name,
                    )

                    worker_dhcp_spec = hspec.get("workerDhcp") or {}
                    if worker_dhcp_spec and not isinstance(worker_dhcp_spec, dict):
                        raise ValueError(f"HCPCluster({hcp_name}): spec.workerDhcp must be a mapping")
                    publish_worker_dhcp = worker_dhcp_spec.get("publishServer", True)
                    if not isinstance(publish_worker_dhcp, bool):
                        raise ValueError(
                            f"HCPCluster({hcp_name}): spec.workerDhcp.publishServer must be a boolean"
                        )
                    exporter = hspec.get("timescapeExporter") or {}
                    if not isinstance(exporter, dict):
                        raise ValueError(f"HCPCluster({hcp_name}): spec.timescapeExporter must be a mapping")
                    enrollment = exporter.get("enrollment")
                    if hspec.get("openshift", {}).get("networkType", "Cilium") == "Cilium":
                        if enrollment not in ("selected", "retained", "excluded"):
                            raise ValueError(
                                f"HCPCluster({hcp_name}): spec.timescapeExporter.enrollment "
                                "must be selected, retained or excluded"
                            )
                    elif exporter:
                        raise ValueError(
                            f"HCPCluster({hcp_name}): timescapeExporter requires Cilium networking"
                        )
                    image_pull = exporter.get("imagePull", "")
                    if image_pull not in ("", "hosted-mirror"):
                        raise ValueError(
                            f"HCPCluster({hcp_name}): spec.timescapeExporter.imagePull "
                            "must be hosted-mirror when set"
                        )
                    exporter_activation = exporter.get("activation", "automatic")
                    if exporter_activation not in ("automatic", "manual"):
                        raise ValueError(
                            f"HCPCluster({hcp_name}): spec.timescapeExporter.activation "
                            "must be automatic or manual"
                        )
                    if enrollment == "selected" and (image_pull != "hosted-mirror"
                                                         or exporter_activation != "manual"):
                        raise ValueError(
                            f"HCPCluster({hcp_name}): selected Timescape exporter requires "
                            "hosted-mirror imagePull and manual activation"
                        )
                    if enrollment == "retained" and (image_pull or exporter_activation != "automatic"):
                        raise ValueError(
                            f"HCPCluster({hcp_name}): retained Timescape exporter must preserve "
                            "automatic lab-image behavior explicitly"
                        )
                    if enrollment == "excluded" and set(exporter) != {"enrollment"}:
                        raise ValueError(
                            f"HCPCluster({hcp_name}): excluded Timescape exporter cannot set "
                            "activation, imagePull or metrics"
                        )
                    exporter_metrics = exporter.get("metrics", {})
                    if not isinstance(exporter_metrics, dict):
                        raise ValueError(f"HCPCluster({hcp_name}): spec.timescapeExporter.metrics must be a mapping")
                    if exporter_metrics:
                        endpoint = exporter_metrics.get("endpoint")
                        pod_monitor = exporter_metrics.get("podMonitor")
                        port = exporter_metrics.get("port")
                        if type(endpoint) is not bool or type(pod_monitor) is not bool:
                            raise ValueError(
                                f"HCPCluster({hcp_name}): spec.timescapeExporter.metrics endpoint and podMonitor must be booleans"
                            )
                        if pod_monitor and not endpoint:
                            raise ValueError(
                                f"HCPCluster({hcp_name}): spec.timescapeExporter.metrics podMonitor requires endpoint"
                            )
                        if endpoint and (type(port) is not int or not 1024 <= port <= 65535):
                            raise ValueError(
                                f"HCPCluster({hcp_name}): spec.timescapeExporter.metrics port must be an unprivileged TCP port"
                            )

                    hcp = ResolvedHCPCluster(
                        name=hcp_name,
                        parent_cluster=hspec.get("parentCluster", ""),
                        ocp_version=hspec.get("openshift", {}).get("version") or (release.ocp_minor if release else ""),
                        network_type=hspec.get("openshift", {}).get("networkType", "Cilium"),
                        replicas=worker_replicas,
                        cluster_networks=hnet.get("clusterNetwork", []),
                        service_networks=hnet.get("serviceNetwork", []),
                        worker_networks=[attachment.network_ref for attachment in worker_attachments],
                        worker_replica_default=worker_replica_default,
                        worker_replica_min=worker_replica_min,
                        worker_replica_max=worker_replica_max,
                        worker_replica_checkpoints=worker_replica_checkpoints,
                        worker_network_profile_ref=hspec.get("workerNetworkProfileRef", ""),
                        worker_attachment_kind=(
                            profile_expansion.attachment if profile_expansion else ""
                        ),
                        worker_allocator=(
                            profile_expansion.allocator if profile_expansion else ""
                        ),
                        worker_egress_gateway_placement=(
                            profile_expansion.egress_gateway_placement if profile_expansion else ""
                        ),
                        worker_bgp_owners=(
                            profile_expansion.bgp_owners if profile_expansion else ()
                        ),
                        worker_renderer_authorizations=(
                            profile_expansion.renderer_authorizations if profile_expansion else ()
                        ),
                        worker_network_attachments=worker_attachments,
                        publish_worker_dhcp=publish_worker_dhcp,
                        cilium_bgp_enabled=hbgp.get("enabled", False),
                        cilium_advertise_lb=hbgp.get("advertiseLoadBalancerIPs", False),
                        cilium_advertise_egress=hbgp.get("advertiseEgressIPs", False),
                        cilium_advertise_pods=hbgp.get("advertisePodCIDRs", False),
                        lb_pool_names=hcilium.get("loadBalancerPools", []),
                        egress_pool_names=hcilium.get("egressGatewayPools", []),
                        timescape_exporter_image_pull=image_pull,
                        timescape_exporter_activation=exporter_activation,
                        timescape_exporter_enrollment=enrollment or "",
                        timescape_exporter_metrics=exporter_metrics,
                    )

                    app_ingress = hspec.get("applicationIngress")
                    if app_ingress is not None:
                        if not isinstance(app_ingress, dict):
                            raise ValueError(f"HCPCluster({hcp_name}): applicationIngress must be a mapping")
                        hcp.application_ingress = ResolvedApplicationIngress(
                            mode=app_ingress.get("mode", ""),
                            pool_ref=app_ingress.get("poolRef", ""),
                            vip=app_ingress.get("vip", ""),
                            dns_wildcard=app_ingress.get("dnsWildcard", ""),
                            dns_zone=app_ingress.get("dnsZone", ""),
                            dns_authority=app_ingress.get("dnsAuthority", ""),
                            dns_publication=app_ingress.get("dnsPublication", ""),
                        )

                    # Resolve hosted-cluster API VIP from HCP ref via parent networking declarations
                    vip_name = hspec.get("api", {}).get("kubeApiVIPRef")
                    vip_ref = kube_api_vip_refs.get(vip_name, vip_name)
                    if vip_ref:
                        result = _find_api_vip(all_networks, vip_ref)
                        if result:
                            hcp.kube_api_network = result[0]
                            hcp.kube_api_vip = result[1]

                    ingress_vip_name = hspec.get("controlPlaneIngress", {}).get("loadBalancerVIPRef")
                    if ingress_vip_name:
                        result = _find_control_plane_ingress_load_balancer_vip(all_networks, ingress_vip_name)
                        if result:
                            hcp.control_plane_ingress_network = result[0]
                            hcp.control_plane_ingress_load_balancer_vip = result[1]

                    _resolve_hcp_worker_attachment_contract(
                        hcp, all_networks, defaults
                    )

                    # Resolve pools
                    for pname in hcp.lb_pool_names:
                        pool = _find_pool(all_networks, pname, "loadBalancer")
                        if pool:
                            hcp.resolved_lb_pools.append(pool)
                    for pname in hcp.egress_pool_names:
                        pool = _find_pool(all_networks, pname, "egress")
                        if pool:
                            hcp.resolved_egress_pools.append(pool)

                    cluster.hosted_clusters.append(hcp)

            # Build VLAN union and network lists
            seen_vlans: set[int] = set()
            if machine_net:
                cluster.vlan_union.append(machine_net)
                seen_vlans.add(machine_net.vlan)

            seen_kubeapi: set[str] = set()
            seen_tenant: set[str] = set()
            for hcp in cluster.hosted_clusters:
                for kube_api_net in (hcp.control_plane_ingress_network, hcp.kube_api_network):
                    if kube_api_net and kube_api_net.name not in seen_kubeapi:
                        seen_kubeapi.add(kube_api_net.name)
                        cluster.kube_api_networks.append(kube_api_net)
                        if kube_api_net.vlan not in seen_vlans:
                            cluster.vlan_union.append(kube_api_net)
                            seen_vlans.add(kube_api_net.vlan)
                for wnet in hcp.resolved_worker_networks:
                    if wnet.name not in seen_tenant:
                        seen_tenant.add(wnet.name)
                        cluster.tenant_networks.append(wnet)
                        if wnet.vlan not in seen_vlans:
                            cluster.vlan_union.append(wnet)
                            seen_vlans.add(wnet.vlan)

            # Collect domain-only VLANs (UCS FI uplinks, not server vNICs)
            for net in all_networks.values():
                if net.category == "domain":
                    cluster.domain_vlans.append(net)

            ucs_spec = cspec.get("ucs", {}).get("hostNetworking", {})
            mgmt_vnic = ucs_spec.get("managementVNICPair", {})
            tenant_vnic = ucs_spec.get("tenantVNICPair", {})

            mgmt_names = mgmt_vnic.get("names", ["eth0", "eth1"])
            mgmt_native_name = mgmt_vnic.get("nativeNetworkRef", mn_name)
            mgmt_native = all_networks.get(mgmt_native_name) if mgmt_native_name else machine_net

            tenant_names = tenant_vnic.get("names", ["eth2", "eth3"])
            tenant_vlan_names = tenant_vnic.get("taggedNetworks", [])
            tenant_nets = []
            for vname in tenant_vlan_names:
                net = all_networks.get(vname)
                if net:
                    tenant_nets.append(net)

            management_pair = ResolvedVNICPair(
                names=mgmt_names,
                native_vlan=mgmt_native,
                tagged_vlans=[],
            )
            tenant_pair = None
            if tenant_vnic.get("enabled", bool(tenant_nets)):
                tenant_pair = ResolvedVNICPair(
                    names=tenant_names,
                    native_vlan=None,
                    tagged_vlans=tenant_nets,
                )
            cluster.ucs_host_networking = (ResolvedUCSHostNetworking(
                management=management_pair,
                tenant=tenant_pair,
            ) if cluster.inventory_mode != "declared" or cspec.get("ucs") else None)
            from gitops_renderer.console_login import expand_console_login, load_console_login
            cluster.console_login = expand_console_login(cluster, load_console_login(cluster_dir))

            ocp_clusters.append(cluster)

    if load_cache:
        from hcp_cache import load_contract, read_pinned
        for selected_cluster in ocp_clusters:
            selected_cluster.cache_production = load_contract(root, selected_cluster, defaults)
            readiness = selected_cluster.local_registry.get("cacheProduction", {}).get("readiness")
            if readiness and selected_cluster.hosted_cluster_lifecycle.get("phase", "legacy") != "held":
                selected_cluster.cache_readiness = read_pinned(root, readiness)

    return ResolvedPlatform(
        fabric=fabric,
        catalog_name=catalog_name,
        all_networks=all_networks,
        clusters=ocp_clusters,
        release_profiles=release_profiles,
        defaults=defaults,
        aci_defaults=aci_defaults,
        ucs_defaults=ucs_defaults,
        profile_intent=profile_intent,
    )


# ---------------------------------------------------------------------------
# Helpers for renderers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert a name to a lowercase slug."""
    return name.lower().replace(" ", "-")


def yaml_dump(data: Any, *, explicit_start: bool = False) -> str:
    """Consistent YAML serialization for renderer output."""
    return yaml.dump(
        data,
        default_flow_style=False,
        sort_keys=False,
        explicit_start=explicit_start,
        width=120,
    )


def legacy_management_cilium_devices(cluster: ResolvedOCPCluster) -> list[str]:
    """Derive legacy Day 2 devices from the selected management/tenant interfaces."""
    management = cluster.management_interface.bond
    tenant = cluster.parent_networking.tenant_bond if cluster.parent_networking else ""
    if management.startswith("bond") and tenant.startswith("bond"):
        return ["bond+"]
    devices = [management]
    if tenant:
        for network in cluster.tenant_networks:
            device = f"{tenant}.{network.vlan}"
            if device not in devices:
                devices.append(device)
    return devices


def generated_header(renderer_name: str) -> str:
    """Standard header comment for generated files."""
    return f"# Generated by {renderer_name} — do not edit manually.\n"


def render_template(template_path: str, context: dict, *, root: Path | None = None) -> str:
    """Render a Jinja2 template from templates/ directory.

    template_path is relative to templates/, e.g. '<templateRoots.clusterInstall>/ucs/vlan.ezi.yaml.j2'.
    Returns rendered string (without generated header — caller adds that).
    """
    import re as _re
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    if root is None:
        root = Path(__file__).resolve().parent.parent

    templates_dir = root / "templates"
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )

    def _slugify(name: str) -> str:
        return _re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')

    env.filters['slugify'] = _slugify

    template = env.get_template(template_path)
    return template.render(**context)


# ---------------------------------------------------------------------------
# CLI for debugging
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve and validate platform contract")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--dump", action="store_true", help="Dump resolved graph summary")
    parser.add_argument("--validate-only", action="store_true", help="Only validate, don't dump")
    args = parser.parse_args()

    root = args.root
    if root is None:
        script_dir = Path(__file__).resolve().parent
        candidate = script_dir.parent
        if has_contract_inputs(candidate):
            root = candidate
        elif has_contract_inputs(Path.cwd()):
            root = Path.cwd()
        else:
            print("ERROR: Cannot find platform root. Use --root.", file=sys.stderr)
            sys.exit(2)

    try:
        platform = resolve(root)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    errors = validate(platform)
    if errors:
        print(f"INVALID: {len(errors)} violation(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    print(f"VALID: catalog={platform.catalog_name}, fabric_asn={platform.fabric.fabric_asn}")
    if platform.release_profiles:
        print(f"  Release profiles: {', '.join(sorted(platform.release_profiles.keys()))}")
    if platform.profile_intent:
        print(
            f"  Profile contract: {platform.profile_intent.schema_version} "
            f"(legacy_adapter={str(platform.profile_intent.legacy_adapter_used).lower()})"
        )
        for diagnostic in platform.profile_intent.diagnostics:
            print(f"  {diagnostic.severity.upper()} [{diagnostic.code}] {diagnostic.source}: {diagnostic.message}")
    print(f"  Networks: {len(platform.all_networks)} ({', '.join(platform.all_networks.keys())})")
    print(f"  Clusters: {len(platform.clusters)}")

    if args.dump:
        for cluster in platform.clusters:
            print(f"\n  OCPCluster: {cluster.name} (mode={cluster.mode})")
            print(f"    Machine network: {cluster.machine_network.name if cluster.machine_network else 'none'}")
            print(f"    API VIP: {cluster.api_vip}, Ingress VIP: {cluster.ingress_vip}")
            print(f"    Nodes: {[n.name for n in cluster.nodes]}")
            print(f"    VLAN union: {[(n.name, n.vlan) for n in cluster.vlan_union]}")
            print(f"    KubeAPI networks: {[n.name for n in cluster.kube_api_networks]}")
            print(f"    Tenant networks: {[n.name for n in cluster.tenant_networks]}")
            for hcp in cluster.hosted_clusters:
                print(f"\n    HCPCluster: {hcp.name}")
                print(f"      KubeAPI VIP: {hcp.kube_api_vip.name if hcp.kube_api_vip else 'none'} -> {hcp.kube_api_vip.cidr if hcp.kube_api_vip else ''}")
                print(f"      KubeAPI network: {hcp.kube_api_network.name if hcp.kube_api_network else 'none'}")
                cp_ingress_vip = hcp.control_plane_ingress_load_balancer_vip
                cp_ingress_net = hcp.control_plane_ingress_network
                print(f"      Control-plane ingress LB VIP: {cp_ingress_vip.name if cp_ingress_vip else 'none'} -> {cp_ingress_vip.cidr if cp_ingress_vip else ''}")
                print(f"      Control-plane ingress network: {cp_ingress_net.name if cp_ingress_net else 'none'}")
                print(
                    "      Worker attachments: "
                    f"{[attachment.attachment_name for attachment in hcp.worker_network_attachments]}"
                )
                print(f"      LB pools: {[(p.name, p.cidr, p.owning_network) for p in hcp.resolved_lb_pools]}")
                print(f"      Egress pools: {[(p.name, p.cidr, p.owning_network) for p in hcp.resolved_egress_pools]}")

    sys.exit(0)


if __name__ == "__main__":
    main()
