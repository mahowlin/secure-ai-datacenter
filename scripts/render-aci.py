#!/usr/bin/env python3
"""Render ACI-as-Code (NAC) inputs from platform contract via resolver.

Consumes the resolved platform graph and renders Jinja2 templates to produce
fully formed NAC ACI YAML for the declared ACI repository to apply via Terraform.

Output is per-tenant folders:
  <tenant>/<tenant>.yml          — VRF, BDs, App Profiles, EPGs, L3Outs, contracts, filters
  <tenant>/access-policies.yml   — VLAN pool, physical/routed domains (shared AAEP model)
  access/10-<management>-domains.yml — management domain and AAEP
  access/30-<management>-attachments.yml — shared host-interface attachment

Templates live in templates/aci/ — the NAC YAML shape and substitution points
are visible there, not hidden in Python dict construction.

Key design rules (see DESIGN.md):
  - Render only networks SELECTED by resolved HCP clusters, not all catalog networks.
  - Render only tenant HCP management-service VIPs and worker-advertised ranges SELECTED by HCPCluster references.
  - Do not render BD subnets for routed peering networks (conflicts with L3Out SVI IPs).
  - Consolidated tenant L3Outs with management-services/worker interface profiles.
  - Floating SVI uses .1/.2 peer IPs, .3 shared gateway, and .4 path floating IP.
  - Shared AAEP model — do not create per-tenant AAEPs.
  - No hardcoded desired-state values — all policy/protocol values from data/defaults/aci.yaml.
  - Per-network router IDs from catalog aci.routerIds, not global fabric defaults.

Usage:
  render-aci.py [--root DIR] [--dry-run] [--output DIR]
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

sys.path.insert(0, str(Path(__file__).resolve().parent))

from site_identity import generated_repo
from resolver import (
    ResolvedAciDefaults,
    ResolvedAciFabric,
    ResolvedHCPManagementServicePool,
    ResolvedNetwork,
    ResolvedPlatform,
    ResolvedPool,
    has_contract_inputs,
    resolve,
    validate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contract_name(prefix_str: str) -> str:
    """Generate contract name: 192.0.2.0/28 -> permit-to-192.0.2.0-28"""
    return f"permit-to-{prefix_str.replace('/', '-')}"



def _l3out_name(net: ResolvedNetwork) -> str:
    return f"L3Out-{net.name}"


def _tenant_l3out_name(tenant_name: str) -> str:
    return f"L3Out-{_tenant_slug(tenant_name)}"


def _tenant_slug(tenant_name: str) -> str:
    """Derive a DNS slug from an ACI tenant name."""
    return tenant_name.lower()


def _management_peering_rule_name(tenant_name: str) -> str:
    return f"{_tenant_slug(tenant_name)}-management-peering"


def _worker_peering_rule_name(tenant_name: str) -> str:
    return f"{_tenant_slug(tenant_name)}-worker-peering"


def _tenant_supernet_rule_name(tenant_name: str) -> str:
    return f"{_tenant_slug(tenant_name)}-tenant-supernet"


def _prefix_length(prefix: str) -> int:
    return ipaddress.IPv4Network(prefix, strict=False).prefixlen


def _subnet_ctx(prefix: str, **flags: bool) -> dict:
    subnet: dict = {"prefix": prefix}
    subnet.update(flags)
    return subnet


def _transit_export_subnet_flags(*, import_security: bool = True) -> dict:
    """Flags for same-VRF prefixes that must be exportable as ACI transit routes."""
    return {
        "export_route_control": True,
        "shared_route_control": True,
        "import_security": import_security,
    }


def _unique(items: list[str]) -> list[str]:
    """Preserve order while dropping duplicate names."""
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _route_control_profile_ref(name: str, direction: str = "export") -> dict:
    return {"name": name, "direction": direction}


# ---------------------------------------------------------------------------
# Collect HCP-selected objects
# ---------------------------------------------------------------------------

def _collect_selected(
    platform: ResolvedPlatform,
) -> dict[str, dict]:
    """Derive ACI-relevant networks, tenant HCP management-service VIPs, and worker-advertised ranges from HCP management clusters.

    Returns {tenant_name: {"networks": [...],
             "hcp_management_services": [...], "lb_pools": [...], "egress_pools": [...]}}.
    """
    result: dict[str, dict] = {}

    for cluster in platform.clusters:
        selected_hcp_service_networks: set[str] = set()
        selected_lb_pool_names: set[str] = set()
        selected_egress_pool_names: set[str] = set()

        for hcp in cluster.hosted_clusters:
            for net in (hcp.control_plane_ingress_network, hcp.kube_api_network):
                if net:
                    selected_hcp_service_networks.add(net.name)
            for pool in hcp.resolved_lb_pools:
                selected_lb_pool_names.add(pool.name)
            for pool in hcp.resolved_egress_pools:
                selected_egress_pool_names.add(pool.name)

        seen_nets: set[str] = set()

        for net in cluster.kube_api_networks:
            if not net.aci_tenant or net.name in seen_nets:
                continue
            seen_nets.add(net.name)
            tenant = net.aci_tenant
            entry = result.setdefault(tenant, {
                "networks": [], "hcp_management_services": [], "lb_pools": [], "egress_pools": [],
            })
            entry["networks"].append(net)
            if net.name in selected_hcp_service_networks:
                for pool in net.hcp_management_services:
                    entry["hcp_management_services"].append((net, pool))

        for net in cluster.tenant_networks:
            if not net.aci_tenant or net.name in seen_nets:
                continue
            seen_nets.add(net.name)
            tenant = net.aci_tenant
            entry = result.setdefault(tenant, {
                "networks": [], "hcp_management_services": [], "lb_pools": [], "egress_pools": [],
            })
            entry["networks"].append(net)
            for pool in net.pools_lb:
                if pool.name in selected_lb_pool_names:
                    entry["lb_pools"].append((net, pool))
            for pool in net.pools_egress:
                if pool.name in selected_egress_pool_names:
                    entry["egress_pools"].append((net, pool))

    return result


# ---------------------------------------------------------------------------
# Build template context from resolved data
# ---------------------------------------------------------------------------

def _tenant_display_name(tenant_name: str) -> str:
    return tenant_name


def _interface_profile_ctx(
    net: ResolvedNetwork,
    aci_fabric: ResolvedAciFabric,
    aci_defaults: ResolvedAciDefaults,
    profile_name: str,
    physical_domain: str,
) -> dict:
    """Build one deployed-style NAC interface profile for a peering network."""
    if len(net.svi_peers) < 2:
        raise ValueError(f"L3Out {_l3out_name(net)} needs 2 SVI peers, got {len(net.svi_peers)}")
    if not net.floating_ip:
        raise ValueError(f"L3Out {_l3out_name(net)} missing ipShared for floating SVI")
    if not net.path_floating_ip:
        raise ValueError(f"L3Out {_l3out_name(net)} missing pathFloatingIp for floating SVI")

    interfaces = []
    for peer in net.svi_peers:
        interfaces.append({
            "svi": True,
            "floating_svi": True,
            "node_id": peer.leaf_node_id,
            "vlan": net.vlan,
            "ip": peer.ip,
            "ip_shared": net.floating_ip,
            "mtu": aci_defaults.mtu,
            "paths": [{"physical_domain": physical_domain, "floating_ip": net.path_floating_ip}],
            "bgp_peers": [{
                "ip": net.peering_subnet,
                "remote_as": net.cilium_asn,
                "local_as": aci_fabric.fabric_asn,
                "bfd": True,
                "as_override": True,
                "disable_peer_as_check": True,
                "multicast_address_family": False,
                "as_propagate": "no-prepend",
                "peer_prefix_policy": aci_defaults.bgp_peer_prefix_policy.name,
            }],
        })

    return {
        "name": profile_name,
        "bfd_policy": aci_defaults.bfd_policy.name,
        "interfaces": interfaces,
    }


def _ospf_interface_profile_ctx(
    net: ResolvedNetwork,
    aci_fabric: ResolvedAciFabric,
    aci_defaults: ResolvedAciDefaults,
    profile_name: str,
    physical_domain: str,
) -> dict:
    """Build one NAC interface profile for an OSPF transit network."""
    if not net.aci or not net.aci.ospf:
        raise ValueError(f"L3Out {_l3out_name(net)} missing OSPF source")
    if len(net.svi_peers) < 1:
        raise ValueError(f"L3Out {_l3out_name(net)} needs at least 1 transit interface, got {len(net.svi_peers)}")
    if not aci_defaults.ospf_interface_policy:
        raise ValueError(f"L3Out {_l3out_name(net)} missing OSPF interface policy defaults")

    interfaces = [{
        "svi": False,
        "node_id": peer.leaf_node_id,
        "port": net.aci.ospf.interface_port,
        "vlan": net.vlan,
        "ip": peer.ip,
        "mtu": aci_defaults.mtu,
    } for peer in net.svi_peers]

    return {
        "name": profile_name,
        "ospf": {"policy": net.aci.ospf.interface_policy or aci_defaults.ospf_interface_policy.name},
        "interfaces": interfaces,
    }


def _build_transit_l3out_context(
    tenant_name: str,
    network: ResolvedNetwork,
    aci_fabric: ResolvedAciFabric,
    aci_defaults: ResolvedAciDefaults,
    kube_api_networks: list[ResolvedNetwork],
    workload_networks: list[ResolvedNetwork],
    endpoint_networks: list[ResolvedNetwork],
    selected_hcp_services_by_net: dict[str, list[ResolvedHCPManagementServicePool]],
    selected_lb_by_net: dict[str, list[ResolvedPool]],
    selected_eg_by_net: dict[str, list[ResolvedPool]],
) -> tuple[dict, list[dict], list[dict], list[dict]]:
    """Build one tenant L3Out context for an OSPF transit network."""
    physical_domain = f"{aci_fabric.physical_domain}-{_tenant_slug(tenant_name)}"
    l3out = _l3out_name(network)
    export_route_map_name = f"{_tenant_slug(tenant_name)}-n5k-ospf-export"
    export_route_profile = [_route_control_profile_ref(export_route_map_name)]
    nodes = [
        {"node_id": node_id, "router_id": router_id}
        for node_id, router_id in sorted(network.router_ids.items())
    ]
    if not nodes:
        raise ValueError(f"L3Out {l3out} missing router_id source")

    selected_subject = aci_defaults.rendered_contract_subject
    if not selected_subject:
        raise ValueError("ACI rendered contract subject is required")
    subjects = [
        {"name": s.name, "filters": s.filters}
        for s in aci_defaults.contract_subjects
        if s.name == selected_subject
    ]
    if len(subjects) != 1:
        raise ValueError("ACI rendered contract subject must match exactly one declared subject")
    contracts_list = [{
        "name": "transit-external",
        "scope": aci_defaults.contract_scope,
        "subjects": subjects,
    }]
    default_routes = network.aci.ospf.default_routes if network.aci and network.aci.ospf else []
    match_rules: list[dict] = []
    route_control_route_maps: list[dict] = []

    export_contexts: list[tuple[str, list[str]]] = []
    interleak_contexts: list[tuple[str, list[str]]] = []
    external_endpoint_groups = [{
        "name": "n5k-default",
        "description": "N5K/lab default route for transit gateway reachability",
        "subnets": [
            {
                "prefix": prefix,
                "import_security": True,
            }
            for prefix in default_routes
        ],
        "contracts": {
            "providers": ["transit-external"],
            "consumers": ["transit-external"],
        },
    }] if default_routes else []
    if default_routes:
        match_rules.append({
            "name": "n5k-default",
            "prefixes": [
                {
                    "ip": prefix,
                    "from_length": _prefix_length(prefix),
                    "to_length": _prefix_length(prefix),
                }
                for prefix in default_routes
            ],
        })
        interleak_contexts.append(("permit-n5k-default", ["n5k-default"]))

    endpoint_prefixes = [net.cidr for net in endpoint_networks if net.cidr]
    if endpoint_prefixes:
        export_contexts.append(("permit-endpoint-subnets", ["endpoint-subnets"]))
        interleak_contexts.append(("permit-endpoint-subnets", ["endpoint-subnets"]))
        external_endpoint_groups.append({
            "name": "endpoint-subnets",
            "description": "Endpoint subnets exported toward N5K OSPF transit",
            "route_control_profiles": export_route_profile,
            "subnets": [
                _subnet_ctx(prefix, **_transit_export_subnet_flags(import_security=False))
                for prefix in endpoint_prefixes
            ],
            "contracts": {
                "providers": ["transit-external"],
                "consumers": ["transit-external"],
            },
        })

    for net in kube_api_networks:
        if net.peering_subnet:
            export_contexts.append(("permit-management-peering", [_management_peering_rule_name(tenant_name)]))
            interleak_contexts.append(("permit-management-peering", [_management_peering_rule_name(tenant_name)]))
            external_endpoint_groups.append(_ext_epg_ctx(
                "tenant-hcp-management-services-nodes",
                "Tenant HCP management-services peering nodes exported toward N5K OSPF transit",
                net.peering_subnet,
                provider_contracts=["transit-external"],
                consumer_contracts=["transit-external"],
                subnet_flags=_transit_export_subnet_flags(),
                route_control_profiles=export_route_profile,
            ))
        for pool in selected_hcp_services_by_net.get(net.name, []):
            export_contexts.append(("permit-management-services", [pool.name]))
            interleak_contexts.append(("permit-management-services", [pool.name]))
            external_endpoint_groups.append(_ext_epg_ctx(
                pool.name,
                f"{_tenant_display_name(tenant_name)} HCP management-services pool exported toward N5K OSPF transit",
                pool.cidr,
                provider_contracts=["transit-external"],
                consumer_contracts=["transit-external"],
                subnet_flags=_transit_export_subnet_flags(),
                route_control_profiles=export_route_profile,
            ))

    for net in workload_networks:
        if net.peering_subnet:
            export_contexts.append(("permit-worker-peering", [_worker_peering_rule_name(tenant_name)]))
            interleak_contexts.append(("permit-worker-peering", [_worker_peering_rule_name(tenant_name)]))
            external_endpoint_groups.append(_ext_epg_ctx(
                "worker-vm-nodes",
                "Worker VM peering nodes exported toward N5K OSPF transit",
                net.peering_subnet,
                provider_contracts=["transit-external"],
                consumer_contracts=["transit-external"],
                subnet_flags=_transit_export_subnet_flags(),
                route_control_profiles=export_route_profile,
            ))
        for pool in selected_lb_by_net.get(net.name, []) + selected_eg_by_net.get(net.name, []):
            export_contexts.append((f"permit-{pool.name}", [pool.name]))
            interleak_contexts.append((f"permit-{pool.name}", [pool.name]))
            external_endpoint_groups.append(_ext_epg_ctx(
                pool.name,
                f"{_tenant_display_name(tenant_name)} {pool.kind} range exported toward N5K OSPF transit",
                pool.cidr,
                provider_contracts=["transit-external"],
                consumer_contracts=["transit-external"],
                subnet_flags=_transit_export_subnet_flags(),
                route_control_profiles=export_route_profile,
            ))

    l3out_ctx = {
        "name": l3out.replace("-lab-core", ""),
        "description": f"{_tenant_display_name(tenant_name)} N5K OSPF transit",
        "vrf": aci_defaults.vrf_name,
        "domain": f"L3Out-{_tenant_slug(tenant_name)}-n5k-ospf-domain",
        "export_route_control_enforcement": network.aci.ospf.export_route_control_enforcement if network.aci and network.aci.ospf else False,
        "ospf": {
            "area": network.aci.ospf.area_id,
            "area_type": network.aci.ospf.area_type,
        },
        "node_profiles": [{
            "name": "n5k-border-leafs",
            "nodes": nodes,
            "interface_profiles": [
                _ospf_interface_profile_ctx(network, aci_fabric, aci_defaults, "n5k-ospf-interfaces", physical_domain)
            ],
        }],
        "external_endpoint_groups": external_endpoint_groups,
    }
    if export_contexts:
        l3out_ctx["export_route_map"] = _route_map(
            export_route_map_name,
            "Export tenant management-services and workload aggregate routes toward N5K OSPF transit",
            export_contexts,
        )
        l3out_ctx["redistribution_route_maps"] = [
            {"source": "static", "route_map": l3out_ctx["export_route_map"]["name"]},
            {"source": "direct", "route_map": l3out_ctx["export_route_map"]["name"]},
        ]
    if interleak_contexts:
        interleak_route_map = _route_map(
            f"{_tenant_slug(tenant_name)}-n5k-ospf-interleak",
            "Permit N5K default and selected tenant transit routes for fabric interleak",
            interleak_contexts,
        )
        l3out_ctx["interleak_route_map"] = interleak_route_map["name"]
        route_control_route_maps.append(interleak_route_map)
    return l3out_ctx, contracts_list, match_rules, route_control_route_maps


def _bgp_l3out_root(
    tenant_name: str,
    networks: list[ResolvedNetwork],
    aci_defaults: ResolvedAciDefaults,
) -> dict:
    """Build the combined tenant BGP L3Out context."""
    if not networks:
        raise ValueError(f"Tenant {tenant_name} missing BGP peering networks")
    l3out = _tenant_l3out_name(tenant_name)
    nodes = [
        {"node_id": node_id, "router_id": router_id, "router_id_as_loopback": False}
        for node_id, router_id in sorted(networks[0].router_ids.items())
    ]
    if not nodes:
        raise ValueError(f"L3Out {l3out} missing router_id source")

    return {
        "name": l3out,
        "description": f"{_tenant_display_name(tenant_name)} BGP peering - management-services and worker SVIs",
        "vrf": aci_defaults.vrf_name,
        "domain": f"{l3out}-domain",
        "node_profiles": [{
            "name": "border-leafs",
            "nodes": nodes,
            "interface_profiles": [],
        }],
        "external_endpoint_groups": [],
    }


def _route_map(name: str, description: str, contexts: list[tuple[str, list[str]]]) -> dict:
    """Build a NAC combinable route map with module-valid 0..9 order values."""
    return {
        "name": name,
        "description": description,
        "type": "combinable",
        "contexts": [
            {"name": ctx_name, "action": "permit", "order": order, "match_rules": rules}
            for order, (ctx_name, rules) in enumerate(contexts)
        ],
    }


def _build_combined_bgp_l3out_context(
    tenant_name: str,
    kube_api_networks: list[ResolvedNetwork],
    workload_networks: list[ResolvedNetwork],
    endpoint_networks: list[ResolvedNetwork],
    transit_networks: list[ResolvedNetwork],
    aci_fabric: ResolvedAciFabric,
    aci_defaults: ResolvedAciDefaults,
    selected_hcp_services_by_net: dict[str, list[ResolvedHCPManagementServicePool]],
    selected_lb_by_net: dict[str, list[ResolvedPool]],
    selected_eg_by_net: dict[str, list[ResolvedPool]],
) -> tuple[list[dict], list[dict], list[dict]]:
    """Build one tenant BGP L3Out containing management-services and worker spokes."""
    selected_subject = aci_defaults.rendered_contract_subject
    if not selected_subject:
        raise ValueError("ACI rendered contract subject is required")
    subjects = [
        {"name": s.name, "filters": s.filters}
        for s in aci_defaults.contract_subjects
        if s.name == selected_subject
    ]
    if len(subjects) != 1:
        raise ValueError("ACI rendered contract subject must match exactly one declared subject")
    contracts_list: list[dict] = []
    seen_contracts: set[str] = set()
    match_rules: list[dict] = []

    def add_named_contract(name: str) -> str:
        if name not in seen_contracts:
            seen_contracts.add(name)
            contracts_list.append({"name": name, "scope": aci_defaults.contract_scope, "subjects": subjects})
        return name

    def add_contract(prefix: str) -> str:
        return add_named_contract(_contract_name(prefix))

    transit_contract = add_named_contract("transit-external")
    bgp_networks = kube_api_networks + workload_networks
    physical_domain = f"{aci_fabric.physical_domain}-{_tenant_slug(tenant_name)}"
    endpoint_prefixes = [net.cidr for net in endpoint_networks if net.cidr]
    hcp_service_pools = [
        pool for net in kube_api_networks for pool in selected_hcp_services_by_net.get(net.name, [])
    ]

    if endpoint_prefixes:
        match_rules.append({
            "name": "endpoint-subnets",
            "prefixes": [
                {"ip": prefix, "from_length": _prefix_length(prefix), "to_length": _prefix_length(prefix)}
                for prefix in endpoint_prefixes
            ],
        })
    for net in kube_api_networks:
        if net.peering_subnet:
            match_rules.append({
                "name": _management_peering_rule_name(tenant_name),
                "prefixes": [{"ip": net.peering_subnet, "from_length": _prefix_length(net.peering_subnet), "to_length": _prefix_length(net.peering_subnet)}],
            })
        for pool in selected_hcp_services_by_net.get(net.name, []):
            match_rules.append({
                "name": pool.name,
                "prefixes": [{"ip": pool.cidr, "from_length": 32, "to_length": 32}],
            })
    for net in workload_networks:
        if net.peering_subnet:
            match_rules.append({
                "name": _worker_peering_rule_name(tenant_name),
                "prefixes": [{"ip": net.peering_subnet, "from_length": _prefix_length(net.peering_subnet), "to_length": _prefix_length(net.peering_subnet)}],
            })
        if net.tenant_supernet:
            match_rules.append({
                "name": _tenant_supernet_rule_name(tenant_name),
                "prefixes": [{"ip": net.tenant_supernet, "from_length": _prefix_length(net.tenant_supernet), "to_length": _prefix_length(net.tenant_supernet)}],
            })
        for pool in selected_lb_by_net.get(net.name, []) + selected_eg_by_net.get(net.name, []):
            match_rules.append({
                "name": pool.name,
                "prefixes": [{"ip": pool.cidr, "from_length": 32, "to_length": 32}],
            })
    export_contexts: list[tuple[str, list[str]]] = []
    if endpoint_prefixes:
        export_contexts.append(("permit-endpoint-subnets", ["endpoint-subnets"]))
    for pool in hcp_service_pools:
        export_contexts.append(("permit-management-services", [pool.name]))
    for net in workload_networks:
        if net.peering_subnet:
            export_contexts.append(("permit-worker-peering", [_worker_peering_rule_name(tenant_name)]))

    l3out = _bgp_l3out_root(tenant_name, bgp_networks, aci_defaults)
    l3out["node_profiles"][0]["interface_profiles"] = [
        _interface_profile_ctx(net, aci_fabric, aci_defaults, "tenant-hcp-management-services-peering", physical_domain)
        for net in kube_api_networks
    ] + [
        _interface_profile_ctx(net, aci_fabric, aci_defaults, "worker-vm-peering", physical_domain)
        for net in workload_networks
    ]
    if export_contexts:
        export_route_map = _route_map(
            f"{_tenant_slug(tenant_name)}-bgp-export",
            "Shared superset export map for tenant BGP L3Out",
            export_contexts,
        )
        l3out["export_route_map"] = export_route_map
        l3out["redistribution_route_maps"] = [
            {"source": "static", "route_map": export_route_map["name"]},
            {"source": "direct", "route_map": export_route_map["name"]},
        ]

    external_endpoint_groups: list[dict] = []
    if endpoint_prefixes:
        external_endpoint_groups.append({
            "name": "endpoint-subnets",
            "description": "Endpoint subnets exported to Cilium BGP peers and N5K transit",
            "subnets": [
                _subnet_ctx(
                    prefix,
                    **_transit_export_subnet_flags(import_security=False),
                )
                for prefix in endpoint_prefixes
            ],
        })

    worker_contracts = [
        add_contract(net.peering_subnet)
        for net in workload_networks
        if net.peering_subnet
    ]
    for net in kube_api_networks:
        node_contract = add_contract(net.peering_subnet)
        external_endpoint_groups.append(_ext_epg_ctx(
            "tenant-hcp-management-services-nodes",
            "Tenant HCP management-services peering nodes",
            net.peering_subnet,
            provider_contracts=[node_contract],
            consumer_contracts=worker_contracts if worker_contracts else None,
            subnet_flags=_transit_export_subnet_flags(),
        ))
        for pool in selected_hcp_services_by_net.get(net.name, []):
            external_endpoint_groups.append(_ext_epg_ctx(
                pool.name,
                f"{_tenant_display_name(tenant_name)} HCP management-services VIP pool",
                pool.cidr,
                provider_contracts=[_contract_name(pool.cidr), transit_contract],
                consumer_contracts=[transit_contract],
                subnet_flags=_transit_export_subnet_flags(),
            ))

    for net in workload_networks:
        node_contract = add_contract(net.peering_subnet)
        for pool in selected_lb_by_net.get(net.name, []) + selected_eg_by_net.get(net.name, []):
            add_contract(pool.cidr)
            external_endpoint_groups.append(_ext_epg_ctx(
                pool.name,
                f"{_tenant_display_name(tenant_name)} worker-originated {pool.kind} range",
                pool.cidr,
                provider_contracts=[_contract_name(pool.cidr), transit_contract],
                consumer_contracts=[transit_contract],
                subnet_flags=_transit_export_subnet_flags(),
            ))
        external_endpoint_groups.append(_ext_epg_ctx(
            "worker-vm-nodes",
            "Worker VM peering nodes",
            net.peering_subnet,
            provider_contracts=[node_contract],
            consumer_contracts=_unique([
                add_contract(pool.cidr)
                for kube_net in kube_api_networks
                for pool in selected_hcp_services_by_net.get(kube_net.name, [])
            ] + [transit_contract]),
            subnet_flags=_transit_export_subnet_flags(),
        ))
        if net.tenant_supernet:
            external_endpoint_groups.append(_ext_epg_ctx(
                "tenant-supernet",
                f"{_tenant_display_name(tenant_name)} tenant supernet transit aggregate",
                net.tenant_supernet,
                provider_contracts=[transit_contract],
                consumer_contracts=[transit_contract],
                subnet_flags=_transit_export_subnet_flags(),
            ))

    l3out["external_endpoint_groups"] = external_endpoint_groups
    return [l3out], contracts_list, match_rules

def _ext_epg_ctx(name: str, description: str, prefix: str,
                 provider_contracts: list[str] | None = None,
                 consumer_contracts: list[str] | None = None,
                 subnet_flags: dict | None = None,
                 route_control_profiles: list[dict] | None = None) -> dict:
    """Build external EPG template context."""
    subnet: dict = {"prefix": prefix}
    if subnet_flags:
        subnet.update(subnet_flags)

    epg: dict = {"name": name, "description": description, "subnets": [subnet]}
    if route_control_profiles:
        epg["route_control_profiles"] = route_control_profiles
    contracts: dict = {}
    if provider_contracts:
        contracts["providers"] = provider_contracts
    if consumer_contracts:
        contracts["consumers"] = consumer_contracts
    if contracts:
        epg["contracts"] = contracts
    return epg


def _build_tenant_context(
    tenant_name: str,
    tenant_networks: list[ResolvedNetwork],
    aci_fabric: ResolvedAciFabric,
    aci_defaults: ResolvedAciDefaults,
    selected_hcp_services_by_net: dict[str, list[ResolvedHCPManagementServicePool]],
    selected_lb_by_net: dict[str, list[ResolvedPool]],
    selected_eg_by_net: dict[str, list[ResolvedPool]],
) -> dict:
    """Build the full template context for a tenant YAML file."""
    vrf_name = aci_defaults.vrf_name
    kube_api_networks = [net for net in tenant_networks if net.category == "kubeAPI" and net.peering_subnet]
    workload_networks = [net for net in tenant_networks if net.category == "tenant" and net.peering_subnet]
    transit_networks = [net for net in tenant_networks if net.category == "fabricTransit" and net.peering_subnet]
    endpoint_networks = [net for net in tenant_networks if net.category == "endpoint" and net.cidr]
    selected_contracts = _unique([
        _contract_name(net.peering_subnet)
        for net in kube_api_networks + workload_networks
        if net.peering_subnet
    ] + [
        _contract_name(pool.cidr)
        for pools in selected_hcp_services_by_net.values()
        for pool in pools
    ] + [
        _contract_name(pool.cidr)
        for pools in selected_lb_by_net.values()
        for pool in pools
    ] + [
        _contract_name(pool.cidr)
        for pools in selected_eg_by_net.values()
        for pool in pools
    ])

    # Bridge domains — only for endpoint networks (with subnets), not peering-only
    bridge_domains = []
    endpoint_l3outs = []
    if kube_api_networks or workload_networks:
        endpoint_l3outs.append(_tenant_l3out_name(tenant_name))
    for transit_net in transit_networks:
        endpoint_l3outs.append(_l3out_name(transit_net).replace("-lab-core", ""))
    for net in tenant_networks:
        if net.peering_subnet:
            continue  # Peering networks use L3Out SVI, no BD needed
        if net.category == "endpoint" and net.cidr and net.gateway:
            cidr_net = ipaddress.IPv4Network(net.cidr, strict=False)
            bd: dict = {
                "name": f"BD-{net.name}",
                "vrf": vrf_name,
                "subnets": [{"ip": f"{net.gateway}/{cidr_net.prefixlen}", "public": True, "shared": True}],
            }
            if endpoint_l3outs:
                bd["l3outs"] = endpoint_l3outs
            bridge_domains.append(bd)

    # Application profiles and EPGs — only for endpoint networks
    app_profiles = []
    for net in tenant_networks:
        if net.peering_subnet:
            continue  # Peering networks use L3Out ExtEPG, no AP/EPG needed
        if net.category == "endpoint":
            epg: dict = {"name": f"EPG-{net.name}", "bridge_domain": f"BD-{net.name}"}
            if selected_contracts:
                epg["contracts"] = {"consumers": selected_contracts}
            if net.vmm_domain:
                epg["vmware_vmm_domains"] = [{"name": net.vmm_domain}]
            app_profiles.append({
                "name": f"AP-{net.name}",
                "endpoint_groups": [epg],
            })

    l3outs = []
    all_contracts = []
    all_match_rules: list[dict] = []
    all_route_control_route_maps: list[dict] = []

    if kube_api_networks or workload_networks:
        rendered_l3outs, contracts, match_rules = _build_combined_bgp_l3out_context(
            tenant_name, kube_api_networks, workload_networks, endpoint_networks, transit_networks,
            aci_fabric, aci_defaults,
            selected_hcp_services_by_net, selected_lb_by_net, selected_eg_by_net,
        )
        l3outs.extend(rendered_l3outs)
        all_contracts.extend(contracts)
        all_match_rules.extend(match_rules)

    for net in transit_networks:
        l3out_ctx, contracts, match_rules, route_control_route_maps = _build_transit_l3out_context(
            tenant_name, net, aci_fabric, aci_defaults,
            kube_api_networks, workload_networks, endpoint_networks,
            selected_hcp_services_by_net, selected_lb_by_net, selected_eg_by_net,
        )
        l3outs.append(l3out_ctx)
        all_contracts.extend(contracts)
        all_match_rules.extend(match_rules)
        all_route_control_route_maps.extend(route_control_route_maps)

    # Deduplicate contracts
    seen: set[str] = set()
    contracts = []
    for c in all_contracts:
        if c["name"] not in seen:
            seen.add(c["name"])
            contracts.append(c)

    # Policies from ACI defaults
    policies = {
        "bfd_interface_policies": [{
            "name": aci_defaults.bfd_policy.name,
            "description": aci_defaults.bfd_policy.description,
            "subinterface_optimization": aci_defaults.bfd_policy.subinterface_optimization,
            "detection_multiplier": aci_defaults.bfd_policy.detection_multiplier,
            "echo_admin_state": aci_defaults.bfd_policy.echo_admin_state,
            "echo_rx_interval": aci_defaults.bfd_policy.echo_rx_interval,
            "min_rx_interval": aci_defaults.bfd_policy.min_rx_interval,
            "min_tx_interval": aci_defaults.bfd_policy.min_tx_interval,
        }],
        "bgp_peer_prefix_policies": [{
            "name": aci_defaults.bgp_peer_prefix_policy.name,
            "description": aci_defaults.bgp_peer_prefix_policy.description,
            "action": aci_defaults.bgp_peer_prefix_policy.action,
            "threshold": aci_defaults.bgp_peer_prefix_policy.threshold,
            "max_prefixes": aci_defaults.bgp_peer_prefix_policy.max_prefixes,
        }],
        "ospf_interface_policies": ([{
            "name": aci_defaults.ospf_interface_policy.name,
            "description": aci_defaults.ospf_interface_policy.description,
            "network_type": aci_defaults.ospf_interface_policy.network_type,
            "mtu_ignore": aci_defaults.ospf_interface_policy.mtu_ignore,
            "advertise_subnet": aci_defaults.ospf_interface_policy.advertise_subnet,
        }] if aci_defaults.ospf_interface_policy else []),
    }

    # Deduplicate match rules across L3Outs
    seen_mr: set[str] = set()
    match_rules = []
    for mr in all_match_rules:
        if mr["name"] not in seen_mr:
            seen_mr.add(mr["name"])
            match_rules.append(mr)

    seen_route_maps: set[str] = set()
    route_control_route_maps = []
    for route_map in all_route_control_route_maps:
        if route_map["name"] not in seen_route_maps:
            seen_route_maps.add(route_map["name"])
            route_control_route_maps.append(route_map)

    policies["route_control_route_maps"] = route_control_route_maps

    # Filters from ACI defaults
    filters = [
        {"name": f.name, "entries": [dict(e) for e in f.entries]}
        for f in aci_defaults.filters
    ]

    return {
        "tenant_name": tenant_name,
        "vrf_name": vrf_name,
        "bridge_domains": bridge_domains,
        "app_profiles": app_profiles,
        "l3outs": l3outs,
        "contracts": contracts,
        "filters": filters,
        "policies": policies,
        "match_rules": match_rules,
    }


def _build_access_policies_context(
    aci_fabric: ResolvedAciFabric,
    tenant_name: str,
    tenant_networks: list[ResolvedNetwork],
) -> dict:
    """Build the template context for an access-policies YAML file."""
    physical_domain = aci_fabric.physical_domain
    slug = _tenant_slug(tenant_name)

    tenant_vlans: set[int] = set()
    n5k_vlans: set[int] = set()
    routed_domain_names: list[dict] = []

    for net in tenant_networks:
        if net.category == "endpoint":
            continue  # VMM-attached endpoints use dynamic VLANs, not static pool
        if net.category == "fabricTransit":
            if not aci_fabric.transit_aaep:
                raise ValueError("spec.fabric.aci.transitAAEP is required for a fabric-transit network")
            n5k_vlans.add(net.vlan)
            routed_domain_names.append({
                "name": f"L3Out-{slug}-n5k-ospf-domain",
                "vlan_pool": f"{physical_domain}-{slug}-n5k-static",
                "vlan_pool_allocation": "static",
                "aaep": aci_fabric.transit_aaep,
            })
            continue
        tenant_vlans.add(net.vlan)
        if net.peering_subnet:
            domain_name = f"{_tenant_l3out_name(tenant_name)}-domain"
            routed_domain = {
                "name": domain_name,
                "vlan_pool": f"{physical_domain}-{slug}-static",
                "vlan_pool_allocation": "static",
                "aaep": aci_fabric.ai_pod_aaep or aci_fabric.shared_aaep,
            }
            if routed_domain not in routed_domain_names:
                routed_domain_names.append(routed_domain)

    def compact_ranges(vlans: set[int]) -> list[dict]:
        vlan_ranges = []
        sorted_vlans = sorted(vlans)
        if sorted_vlans:
            start = prev = sorted_vlans[0]
            for v in sorted_vlans[1:]:
                if v == prev + 1:
                    prev = v
                else:
                    vlan_ranges.append({"from": start, "to": prev, "role": "external"})
                    start = prev = v
            vlan_ranges.append({"from": start, "to": prev, "role": "external"})
        return vlan_ranges

    vlan_pools = [{
        "name": f"{physical_domain}-{slug}-static",
        "allocation": "static",
        "ranges": compact_ranges(tenant_vlans),
    }]
    if n5k_vlans:
        vlan_pools.append({
            "name": f"{physical_domain}-{slug}-n5k-static",
            "allocation": "static",
            "ranges": compact_ranges(n5k_vlans),
        })

    phys_domain_name = f"{physical_domain}-{slug}"

    ai_pod_aaep = aci_fabric.ai_pod_aaep or aci_fabric.shared_aaep

    return {
        "tenant_name": tenant_name,
        "shared_aaep": ai_pod_aaep,
        "vlan_pools": vlan_pools,
        "physical_domain": {
            "name": phys_domain_name,
            "vlan_pool": f"{physical_domain}-{slug}-static",
            "vlan_pool_allocation": "static",
        },
        "routed_domains": routed_domain_names,
        "extra_aaeps": [
            {
                "name": aci_fabric.transit_aaep,
                "routed_domains": [f"L3Out-{slug}-n5k-ospf-domain"],
            }
        ] if n5k_vlans else [],
    }


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------

def _load_jinja_env(root: Path) -> Environment:
    """Load Jinja2 environment from templates/aci/ directory."""
    template_dir = root / "templates" / "aci"
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )


def _management_attachments(platform: ResolvedPlatform, env) -> dict[str, str]:
    """Render selected machine-network attachment, independently of tenant BGP."""
    results = {}
    seen = set()
    cluster_names = {c.name for c in platform.clusters}
    for network in platform.all_networks.values():
        if network.aci_attachment and network.aci_attachment.get("cluster") not in cluster_names:
            raise ValueError("management ACI attachment refers to an unknown cluster")
    for cluster in platform.clusters:
        network = cluster.machine_network
        config = network.aci_attachment if network else None
        if not config or config.get("cluster") != cluster.name or network.name in seen:
            continue
        seen.add(network.name)
        if cluster.management_interface.mode != "active-backup":
            raise ValueError("managed ACI access-port example requires active-backup; LACP needs a matching declared port-channel design")
        required = {"cluster", "tenant", "vrf", "bridgeDomain", "applicationProfile", "endpointGroup", "physicalDomain", "aaep", "siteL3Out", "siteContract", "serverPorts"}
        if set(config) != required or any(not re.fullmatch(r"[A-Za-z0-9_.-]+", config[k]) for k in required - {"serverPorts"}):
            raise ValueError("management ACI attachment requires explicit names and serverPorts")
        if network.mtu is None or not 576 <= network.mtu <= 9000 or not 1 <= network.vlan <= 4094:
            raise ValueError("invalid management MTU/VLAN")
        prefix = ipaddress.IPv4Network(network.cidr, strict=False)
        if ipaddress.ip_address(network.gateway) not in prefix:
            raise ValueError("management ACI gateway outside machine network")
        sides = []
        occupied = set()
        for role, aaep, ports in [("management", config["aaep"], config["serverPorts"]),
                                  ("tenant", platform.fabric.aci.ai_pod_aaep, platform.fabric.aci.tenant_ports)]:
            if not ports or not aaep:
                raise ValueError(f"explicit {role} ACI ports and AAEP required")
            by_leaf = {}
            expected_hosts = {n.name for n in cluster.nodes}
            if {p.get("host") for p in ports} != expected_hosts:
                raise ValueError(f"{role} ACI port hosts must match selected cluster")
            for port in ports:
                if set(port) != {"host", "podId", "nodeId", "module", "port"} or any(type(port[k]) is not int or port[k] <= 0 for k in ("podId", "nodeId", "module", "port")):
                    raise ValueError("ACI port requires positive pod/node/module/port and host")
                key = tuple(port[k] for k in ("podId", "nodeId", "module", "port"))
                if key in occupied:
                    raise ValueError("duplicate management/tenant physical attachment")
                occupied.add(key)
                by_leaf.setdefault(port["nodeId"], []).append(port)
            # Routed floating paths and port attachment must use the same leaf IDs.
            if role == "tenant" and set(by_leaf) != {leaf.node_id for leaf in platform.fabric.aci.border_leaf_nodes}:
                raise ValueError("tenant port leaves differ from BGP floating-SVI leaves")
            sides.append({"role": role, "aaep": aaep, "leaves": by_leaf})
        context = dict(config=config, network=network,
                       gateway_prefix=f"{network.gateway}/{prefix.prefixlen}", sides=sides)
        for filename, template in [
            (f"{config['tenant']}/{config['tenant']}.yml", "management.yml.j2"),
            (f"access/10-{config['tenant']}-domains.yml", "management-domains.yml.j2"),
            (f"access/30-{config['tenant']}-attachments.yml", "interface-attachments.yml.j2"),
        ]:
            results[filename] = env.get_template(template).render(**context)
    return results


def write_outputs(output: Path, results: dict[str, str]) -> None:
    """Remove only stale files bearing this renderer's ownership marker."""
    for old in output.rglob("*.yml") if output.exists() else []:
        if old.relative_to(output).as_posix() not in results and old.read_text().startswith("# Generated by render-aci.py"):
            old.unlink()
    for filename, content in sorted(results.items()):
        filepath = output / filename
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
    for directory in sorted(output.rglob("*"), reverse=True) if output.exists() else []:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()


def render(platform: ResolvedPlatform, root: Path) -> dict[str, str]:
    """Render ACI NAC YAML from a resolved platform via Jinja2 templates."""
    aci_fabric = platform.fabric.aci
    aci_defaults = platform.aci_defaults

    if not aci_fabric or not aci_defaults:
        return {}

    env = _load_jinja_env(root)
    tenant_tmpl = env.get_template("tenant.yml.j2")
    access_tmpl = env.get_template("access-policies.yml.j2")

    selected = _collect_selected(platform)

    # Inject endpoint and fabric-transit networks into per-tenant selections
    # (not cluster-bound). Transit links are tenant-owned fabric adjacency, not
    # selected by HCP references.
    for net_name, net in platform.all_networks.items():
        if net.category in {"endpoint", "fabricTransit"} and net.aci_tenant:
            entry = selected.setdefault(net.aci_tenant, {
                "networks": [], "hcp_management_services": [], "lb_pools": [], "egress_pools": [],
            })
            entry["networks"].append(net)

    results: dict[str, str] = {}

    for tenant_name, sel in sorted(selected.items()):
        nets = sel["networks"]

        hcp_services_by_net: dict[str, list[ResolvedHCPManagementServicePool]] = {}
        lb_by_net: dict[str, list[ResolvedPool]] = {}
        eg_by_net: dict[str, list[ResolvedPool]] = {}

        for net_obj, pool in sel["hcp_management_services"]:
            hcp_services_by_net.setdefault(net_obj.name, []).append(pool)
        for net_obj, pool in sel["lb_pools"]:
            lb_by_net.setdefault(net_obj.name, []).append(pool)
        for net_obj, pool in sel["egress_pools"]:
            eg_by_net.setdefault(net_obj.name, []).append(pool)

        # Tenant YAML
        tenant_ctx = _build_tenant_context(
            tenant_name, nets, aci_fabric, aci_defaults,
            hcp_services_by_net, lb_by_net, eg_by_net,
        )
        results[f"{tenant_name}/{tenant_name}.yml"] = tenant_tmpl.render(**tenant_ctx)

        # Access policies YAML
        access_ctx = _build_access_policies_context(aci_fabric, tenant_name, nets)
        results[f"{tenant_name}/access-policies.yml"] = access_tmpl.render(**access_ctx)

    results.update(_management_attachments(platform, env))
    # These NAC templates contain no block scalars. Remove empty loop-only lines
    # so the generated object hierarchy stays readable without semantic changes.
    return {name: "\n".join(line for line in text.splitlines() if line.strip()) + "\n"
            for name, text in results.items()}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Render ACI-as-Code NAC YAML from platform contract")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    root = args.root
    if root is None:
        script_dir = Path(__file__).resolve().parent
        candidate = script_dir.parent
        if has_contract_inputs(candidate):
            root = candidate
        else:
            print("ERROR: Cannot find the platform root. Use --root.", file=sys.stderr)
            sys.exit(2)

    try:
        platform = resolve(root)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    errors = validate(platform)
    if errors:
        print(f"Invalid ACI input ({len(errors)}):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(2)

    results = render(platform, root)

    if args.dry_run:
        for filename, content in sorted(results.items()):
            print(f"# === {filename} ===")
            print(content)
            print()
        print(f"# Rendered {len(results)} file(s): {', '.join(sorted(results.keys()))}", file=sys.stderr)
    else:
        out = args.output or (root / "generated" / "repos" / generated_repo("aci", root) / "data")
        write_outputs(out, results)
        print(f"Written {len(results)} files to {out}/: {', '.join(sorted(results.keys()))}")


if __name__ == "__main__":
    main()
