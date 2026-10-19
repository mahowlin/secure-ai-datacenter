#!/usr/bin/env python3
"""Render target-native UCS Easy-IMM EZI files from platform contract.

Produces full-replacement EZI files for the SAIF-varying UCS policies:
  generated/repos/<generatedRepos.openshift>/data/ucs/
    vlan.ezi.yaml                   — Domain + server VLAN policies
    ethernet_network_group.ezi.yaml — Management + tenant ENG policies
    lan-connectivity.ezi.yaml       — Dual vNIC LAN connectivity policy

All target-repo policy names come from data/defaults/ucs.yaml (loaded by resolver).
Templates live in templates/<templateRoots.clusterInstall>/ucs/ — visible, reviewable, diffable.

Usage:
  render-ucs.py [--root DIR] [--cluster NAME] [--output DIR] [--dry-run]
"""

import argparse
import sys
from pathlib import Path

from resolver import resolve, validate, generated_header, render_template, has_contract_inputs


_TEMPLATES = [
    "vlan.ezi.yaml.j2",
    "ethernet_network_group.ezi.yaml.j2",
    "lan-connectivity.ezi.yaml.j2",
]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------

def _unique_networks(networks):
    by_vlan = {}
    for net in networks:
        by_vlan.setdefault(net.vlan, net)
    return sorted(by_vlan.values(), key=lambda n: n.vlan)


def render_ucs(clusters, ucs_defaults, *, root=None, template_root_name=None):
    """Render target-native EZI files. Returns {filename: content}."""
    clusters = [cluster for cluster in clusters if cluster.ucs_host_networking is not None]
    header = generated_header("render-ucs.py")
    vlans = _unique_networks(net for cluster in clusters for net in cluster.vlan_union)
    domain_vlans = _unique_networks(net for cluster in clusters for net in cluster.domain_vlans)

    lan_policies = []
    mgmt_vlans = []
    tenant_vlans = []
    for cluster in clusters:
        if not cluster.ucs_host_networking:
            continue
        mgmt_pair = cluster.ucs_host_networking.management
        tenant_pair = cluster.ucs_host_networking.tenant
        if mgmt_pair.native_vlan:
            mgmt_vlans.append(mgmt_pair.native_vlan)
        if tenant_pair:
            tenant_vlans.extend(tenant_pair.tagged_vlans)
        for node in cluster.nodes:
            lan_policies.append({
                "node": node,
                "mgmt_pair": mgmt_pair,
                "tenant_pair": tenant_pair,
            })

    ctx = {
        "target": ucs_defaults,
        "vlans": vlans,
        "domain_vlans": domain_vlans,
        "mgmt_vlans": _unique_networks(mgmt_vlans),
        "tenant_vlans": _unique_networks(tenant_vlans),
        "lan_policies": lan_policies,
    }

    files = {}
    for tmpl in _TEMPLATES:
        out_name = tmpl.removesuffix(".j2")
        if not isinstance(template_root_name, str) or not template_root_name:
            raise ValueError("selected spec.identity.templateRoots.clusterInstall is required")
        body = render_template(f"{template_root_name}/ucs/{tmpl}", ctx, root=root)
        files[out_name] = header + body

    return files


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Render UCS Easy-IMM EZI files from platform contract")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--cluster", type=str, default=None, help="Cluster name filter")
    parser.add_argument("--output", type=Path, default=None, help="Output directory (default: generated/repos/<generatedRepos.openshift>/data/ucs/)")
    parser.add_argument("--dry-run", action="store_true", help="Print to stdout instead of writing")
    args = parser.parse_args()

    root = args.root
    if root is None:
        script_dir = Path(__file__).resolve().parent
        candidate = script_dir.parent
        if has_contract_inputs(candidate):
            root = candidate
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
        print(f"Validation failed ({len(errors)} errors):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    if not platform.ucs_defaults:
        print("ERROR: data/defaults/ucs.yaml not found or invalid", file=sys.stderr)
        sys.exit(2)

    clusters = [c for c in platform.clusters if c.ucs_host_networking is not None]
    if args.cluster:
        clusters = [c for c in clusters if c.name == args.cluster]
        if not clusters:
            print(f"ERROR: cluster {args.cluster!r} not found", file=sys.stderr)
            sys.exit(2)

    install_root = platform.defaults.identity["templateRoots"]["clusterInstall"]
    files = render_ucs(clusters, platform.ucs_defaults, root=root, template_root_name=install_root)
    domain_ids = sorted({n.vlan for cluster in clusters for n in cluster.domain_vlans})
    vlan_ids = sorted({n.vlan for cluster in clusters for n in cluster.vlan_union})

    if args.dry_run:
        for fname, content in files.items():
            print(f"# === {fname} ===")
            print(content)
            print()
        print(f"# Clusters: {[cluster.name for cluster in clusters]}", file=sys.stderr)
        print(f"# Domain VLANs: {domain_ids}, Server VLANs: {vlan_ids}", file=sys.stderr)
    else:
        openshift_repo = platform.defaults.identity["generatedRepos"]["openshift"]
        out = args.output or (root / "generated" / "repos" / openshift_repo / "data" / "ucs")
        out.mkdir(parents=True, exist_ok=True)
        for fname, content in files.items():
            (out / fname).write_text(content)
        print(f"Written to {out}/: {', '.join(files.keys())}")
        print(f"Clusters: {[cluster.name for cluster in clusters]}")
        print(f"Domain VLANs: {domain_ids}, Server VLANs: {vlan_ids}")


if __name__ == "__main__":
    main()
