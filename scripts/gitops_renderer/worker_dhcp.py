import ipaddress
import json

from resolver import render_template
from .selected_inputs import template_root

from .common import *


def _template_root() -> str:
    return f"{template_root('gitops')}/apps/hcp/management/parent-network/worker-dhcp"


def _indent(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "".join(f"{prefix}{line}\n" if line else "\n" for line in text.splitlines())


def _render_template_file(name: str, context: dict) -> str:
    return HEADER + "---\n" + render_template(f"{_template_root()}/{name}.j2", context)


def _worker_dhcp_defaults(defaults: ResolvedDefaults) -> dict:
    config = defaults.gitops_worker_dhcp
    required = [
        "serverName",
        "cloudInitSecretName",
        "observedLeaseFile",
        "serverInterfaceNameTemplate",
        "leaseSeconds",
        "image",
        "leaseStorage",
    ]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise ValueError(f"PlatformDefaults spec.gitops.workerDhcp missing required values: {', '.join(missing)}")
    return dict(config)


def _worker_dhcp_networks(
    cluster: ResolvedOCPCluster,
    hcp_names: set[str] | None = None,
) -> list[object]:
    """Return unique resolved DHCP scopes without transport rediscovery."""
    scopes: dict[str, object] = {}
    for hcp in cluster.hosted_clusters:
        if hcp_names is not None and hcp.name not in hcp_names:
            continue
        for scope in hcp.worker_dhcp_scopes:
            existing = scopes.get(scope.scope_id)
            if existing is not None and existing != scope:
                raise ValueError(f"conflicting resolved DHCP scope {scope.scope_id}")
            scopes[scope.scope_id] = scope
    return [scopes[name] for name in sorted(scopes)]


def _reservation_plan(cluster: ResolvedOCPCluster,
                      defaults: ResolvedDefaults,
                      dhcp_defaults: dict,
                      hcp_names: set[str] | None = None) -> list[dict]:
    """Multus external DHCP is dynamic and owns no admission reservations."""
    del cluster, defaults, dhcp_defaults, hcp_names
    return []


def _server_networks(cluster: ResolvedOCPCluster,
                     dhcp_defaults: dict,
                     reservations: list[dict],
                     hcp_names: set[str] | None = None) -> list[dict]:
    reservations_by_scope: dict[str, list[dict]] = {}
    for attachment in reservations:
        for slot in attachment["slots"]:
            reservations_by_scope.setdefault(attachment["scopeId"], []).append({
                "mac": slot["mac"],
                "ip": slot["ipv4"],
                "router": slot["router"],
                "dns": slot["dns"],
                "hostname": slot["slot"],
                "namespace": attachment["namespace"],
                "hcp": attachment["hcp"],
                "interface": attachment["interface"],
                "defaultRoute": slot["defaultRoute"],
            })

    networks = []
    seen_endpoint_names: set[str] = set()
    for index, scope in enumerate(_worker_dhcp_networks(cluster, hcp_names)):
        endpoint_name = slugify(scope.attachment_name)
        if endpoint_name in seen_endpoint_names:
            raise ValueError(f"duplicate DHCP service attachment name {endpoint_name}")
        seen_endpoint_names.add(endpoint_name)
        subnet = ipaddress.IPv4Network(scope.address_authority, strict=False)
        networks.append({
            "interface": str(dhcp_defaults["serverInterfaceNameTemplate"]).format(index=index),
            "name": endpoint_name,
            "nad": scope.attachment_name,
            "network": scope.network_name,
            "subnet": scope.subnet_name,
            "scope_id": scope.scope_id,
            "attachment_kind": scope.attachment_kind,
            "transport": scope.delivery,
            "mode": scope.allocation,
            "server": scope.server_address,
            "prefix": subnet.prefixlen,
            "subnet_mask": str(subnet.netmask),
            "address_authority": str(subnet),
            "lease_seconds": int(dhcp_defaults["leaseSeconds"]),
            "range_start": scope.pool_start,
            "range_end": scope.pool_end,
            "router": scope.router,
            "dns": list(scope.dns_servers),
            "mtu": scope.mtu,
            "emit_options": list(scope.emitted_options),
            "exposure": scope.exposure,
            "reservations": sorted(
                reservations_by_scope.get(scope.scope_id, []),
                key=lambda item: ipaddress.IPv4Address(item["ip"]),
            ),
        })

    return networks


def _render_worker_dhcp_component(
    cluster: ResolvedOCPCluster,
    defaults: ResolvedDefaults,
    hcp_name: str,
) -> dict[str, str]:
    dhcp_defaults = _worker_dhcp_defaults(defaults)
    component = slugify(hcp_name)
    server_name = f"{dhcp_defaults['serverName']}-{component}"
    cloud_init_secret_name = f"{dhcp_defaults['cloudInitSecretName']}-{component}"
    storage = dhcp_defaults["leaseStorage"]
    lease_claim_name = f"{server_name}{storage['claimNameSuffix']}"
    if len(lease_claim_name) > 63:
        raise ValueError(f"DHCP lease claim name exceeds the DNS label limit: {lease_claim_name}")
    hcp_names = {hcp_name}
    reservations = _reservation_plan(
        cluster, defaults, dhcp_defaults, hcp_names
    )
    networks = _server_networks(
        cluster,
        dhcp_defaults,
        reservations,
        hcp_names,
    )
    if not networks:
        return {}

    namespace = defaults.namespace("hcpWorkerDhcp")
    authority_id = f"{namespace}/{server_name}"
    claim_bytes = int(storage["size"].removesuffix("Gi")) * 1024**3
    minimum_bytes = claim_bytes * int(storage["minUsablePercent"]) // 100
    server_interface_config = json.dumps(networks, separators=(",", ":"))
    server_script = "#!/usr/bin/env python3\n" + render_template(
        f"{_template_root()}/server-script.py.j2",
        {
            "interface_config_json_literal": json.dumps(server_interface_config),
            "observed_lease_file_json": json.dumps(dhcp_defaults["observedLeaseFile"]),
            "lease_mount_path_json": json.dumps(storage["mountPath"]),
            "authority_id_json": json.dumps(authority_id),
            "lease_claim_name_json": json.dumps(lease_claim_name),
            "clock_skew_seconds": int(storage["clockSkewSeconds"]),
        },
    )
    lease_mount_script = "#!/bin/sh\n" + render_template(
        f"{_template_root()}/lease-disk.sh.j2",
        {
            "lease_disk_serial": storage["diskSerial"],
            "lease_filesystem_label": storage["filesystemLabel"],
            "lease_mount_path": storage["mountPath"],
            "lease_claim_name": lease_claim_name,
            "lease_authority_id": authority_id,
            "lease_minimum_bytes": minimum_bytes,
            "lease_maximum_bytes": claim_bytes,
        },
    )
    cloud_init = render_template(f"{_template_root()}/cloud-init.yaml.j2", {
        "server_script_indented": _indent(server_script, 4).rstrip(),
        "lease_mount_script_indented": _indent(lease_mount_script, 4).rstrip(),
        "lease_mount_path": storage["mountPath"],
    })
    network_data = yaml.safe_dump({
        "version": 2,
        "ethernets": {
            network["interface"]: {
                "dhcp4": False,
                "addresses": [f"{network['server']}/{network['prefix']}"],
                **({"mtu": network["mtu"]} if network.get("mtu") else {}),
            }
            for network in networks
        },
    }, sort_keys=False)

    common = {
        "namespace": namespace,
        "namespace_sync_wave": defaults.sync_wave("namespace"),
        "network_attachment_sync_wave": defaults.sync_wave("networkAttachmentDefinition"),
        "worker_dhcp_sync_wave": defaults.sync_wave("workerDhcp"),
        "server_name": server_name,
        "vm_run_strategy": "Halted" if authority_id in cluster.maintenance_vm_holds else storage["runStrategy"],
        "cloud_init_secret_name": cloud_init_secret_name,
        "dhcp_image": dhcp_defaults["image"],
        "lease_claim_name": lease_claim_name,
        "lease_storage_class": storage["storageClassName"],
        "lease_storage_size": storage["size"],
        "lease_access_mode": storage["accessMode"],
        "lease_volume_mode": storage["volumeMode"],
        "api_group": defaults.api_group,
        "lease_disk_serial": storage["diskSerial"],
        "dns_servers": defaults.dns_servers,
        "networks": networks,
        "cloud_init_indented": _indent(cloud_init, 4).rstrip(),
        "network_data_indented": _indent(network_data, 12).rstrip(),
    }

    resources = [
        *(["network-attachments.yaml"] if any(
            n["attachment_kind"] == "multus-l2" for n in networks
        ) else []),
        "persistentvolumeclaim.yaml",
        "cloud-init-config.yaml",
        "virtualmachine.yaml",
    ]
    base = (
        f"apps/hcp/management/parent-network/overlays/{cluster.name}/"
        f"worker-dhcp/{component}"
    )
    files = {
        f"{base}/{name}": _render_template_file(name, common)
        for name in resources
        if name != "network-attachments.yaml"
    }
    files[f"{base}/kustomization.yaml"] = _render_template_file(
        "kustomization.yaml", {"resources": resources}
    )
    return files


def _render_worker_dhcp(cluster: ResolvedOCPCluster,
                        defaults: ResolvedDefaults) -> dict[str, str]:
    """Render one independently publishable DHCP component per HCP consumer."""
    dhcp_hcps = sorted(
        (
            hcp for hcp in cluster.hosted_clusters
            if hcp.worker_dhcp_scopes and hcp.publish_worker_dhcp
        ),
        key=lambda item: item.name,
    )
    if not dhcp_hcps:
        return {}

    dhcp_defaults = _worker_dhcp_defaults(defaults)
    base = f"apps/hcp/management/parent-network/overlays/{cluster.name}/worker-dhcp"
    namespace_context = {
        "namespace": defaults.namespace("hcpWorkerDhcp"),
        "namespace_sync_wave": defaults.sync_wave("namespace"),
        "server_name": dhcp_defaults["serverName"],
    }
    files = {
        f"{base}/namespace.yaml": _render_template_file(
            "namespace.yaml", namespace_context
        ),
        f"{base}/kustomization.yaml": _render_template_file(
            "kustomization.yaml",
            {"resources": ["namespace.yaml", *[slugify(hcp.name) for hcp in dhcp_hcps]]},
        ),
    }
    for hcp in dhcp_hcps:
        files.update(_render_worker_dhcp_component(cluster, defaults, hcp.name))
    return files
