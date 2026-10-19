#!/usr/bin/env python3
"""Render OCP install artifacts from platform contract via visible Jinja templates.

Produces:
  generated/repos/<generatedRepos.openshift>/data/openshift/<cluster>/
    install-config.yaml
    agent-config.yaml
    metadata.yaml
    manifests/ciliumconfig.yaml  # Cilium Day 1 clusters only
    manifests/cluster-network-02-config-local.yml  # Cilium KPR contract

Templates live in templates/<templateRoots.clusterInstall>/openshift/ — visible, reviewable, diffable.

Declared hardware contract:
  agentConfig.inventoryMode=declared uses node interfaces/rootDeviceHints and
  explicit management bond mode/options; no UCS discovery is needed. Named
  rendezvousNode selects the bootstrap host. Private pull-secret/SSH/registry-CA
  files may be enriched only with --executable and external --output.

Legacy runtime MAC enrichment contract:
  The renderer emits MAC_PLACEHOLDER tokens for the Day-1 install NICs. The
  deploy pipeline substitutes real MACs from runtime UCS/Intersight facts before
  ISO generation. Day-1 OpenShift installation uses eth0/eth1 on bond0 only.

Root device enrichment contract:
  The renderer emits BOOT_DISK_MODEL_PLACEHOLDER for rootDeviceHints.model.
  The deploy pipeline must substitute the actual boot disk model string per
  node, discovered live from Intersight by serial number.  Pipeline validation
  must hard-fail if any placeholder survives into an executable AgentConfig.

Registry CA enrichment contract:
  The renderer emits REGISTRY_CA_PLACEHOLDER in the additionalTrustBundle
  field.  The deploy pipeline must substitute the PEM-encoded registry CA
  certificate (from REGISTRY_CA_CERT secret) before ISO generation.  This
  is required because the configured registry uses a private certificate.
  Pipeline validation must hard-fail if the placeholder survives into an
  executable install-config.  The additionalTrustBundlePolicy is set to
  "Always" (required when no HTTP proxy is configured).

Day 1 / Day 2 split:
  Day 1 (agent-config): bond0 with the machine-network IP only.
  UCS may provision an optional tenant vNIC pair (eth2/eth3) for later use, but
  tenant VLAN/bond configuration is owned by Day 2/GitOps after install.

Usage:
  render-ocp.py [--root DIR] [--cluster NAME] [--output DIR] [--dry-run]
"""

import argparse
import copy
import json
import os
import ipaddress
import sys
from pathlib import Path

import yaml

from resolver import (
    resolve, validate, ResolvedOCPCluster, ResolvedDefaults,
    generated_header, render_template, has_contract_inputs,
    resolved_management_cilium_config_spec,
    resolved_clife_image_content_sources,
)


# ---------------------------------------------------------------------------
# Constants — SNO proven patterns
# ---------------------------------------------------------------------------

# Bond configuration proven on the single-node install path
BOND_MODE = "active-backup"
BOND_MIIMON = "100"

# Management NIC names (maps to UCS mgmt vNIC pair: Fabric A + Fabric B)
MGMT_NIC_A = "eth0"
MGMT_NIC_B = "eth1"

# Runtime enrichment placeholder — deploy pipeline must substitute the actual
# boot disk model per node (discovered live from Intersight by serial number)
# before ISO generation.  The placeholder must NOT survive into an executable
# AgentConfig; pipeline validation must hard-fail on any unresolved placeholder.
BOOT_DISK_MODEL_PLACEHOLDER = "BOOT_DISK_MODEL_PLACEHOLDER"

# ---------------------------------------------------------------------------
# Renderer (template-based)
# ---------------------------------------------------------------------------

def render_ocp_install(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults, *, root=None, runtime=None) -> dict[str, str]:
    """Render OCP install artifacts via Jinja templates. Returns {filepath: content}."""
    header = generated_header("render-ocp.py")
    mn = cluster.machine_network
    mn_cidr = mn.cidr if mn else ""
    mn_prefix = ipaddress.IPv4Network(mn_cidr, strict=False).prefixlen if mn_cidr else 24
    mn_gateway = mn.gateway if mn else ""
    mgmt_bond = cluster.management_interface.bond if cluster.management_interface else "bond0"
    mgmt_members = cluster.management_interface.members if cluster.management_interface else [MGMT_NIC_A, MGMT_NIC_B]

    common_ctx = {
        "cluster": cluster,
        "defaults": defaults,
        "mgmt_bond": mgmt_bond,
        "management_mtu": mn.mtu if mn else None,
        "MGMT_NIC_A": mgmt_members[0],
        "MGMT_NIC_B": mgmt_members[1],
        "BOND_MODE": cluster.management_interface.mode or BOND_MODE,
        "bond_options_yaml": yaml.safe_dump(cluster.management_interface.options, sort_keys=False).rstrip() if cluster.management_interface.options else "",
        "BOND_MIIMON": BOND_MIIMON,
        "rendezvous_ip": next((n.ip for n in cluster.nodes if n.name == cluster.rendezvous_node), cluster.nodes[0].ip),
        "image_digest_sources": resolved_clife_image_content_sources(
            defaults.image_digest_sources,
            cluster.release.clife_package if cluster.release else None,
        ),
    }

    # --- install-config.yaml ---
    roots = defaults.identity.get("templateRoots") if isinstance(defaults.identity, dict) else None
    install_root = roots.get("clusterInstall") if isinstance(roots, dict) else None
    if not isinstance(install_root, str) or not install_root:
        raise ValueError("selected spec.identity.templateRoots.clusterInstall is required")
    ic_body = render_template(f"{install_root}/openshift/install-config.yaml.j2", {
        **common_ctx,
        "mn_cidr": mn_cidr,
    }, root=root)
    ic_content = header + "---\n" + ic_body

    # --- agent-config.yaml ---
    ac_body = render_template(f"{install_root}/openshift/agent-config.yaml.j2", {
        **common_ctx,
        "mn_prefix": mn_prefix,
        "mn_gateway": mn_gateway,
    }, root=root)
    ac_content = header + "---\n" + ac_body

    # --- metadata.yaml ---
    md_body = render_template(f"{install_root}/openshift/metadata.yaml.j2", {
        **common_ctx,
    }, root=root)
    md_content = header + "---\n" + md_body

    files = {
        "install-config.yaml": ic_content,
        "agent-config.yaml": ac_content,
        "metadata.yaml": md_content,
    }

    if cluster.network_type == "Cilium":
        package = cluster.release.clife_package if cluster.release else None
        cilium_config_spec_yaml = ""
        if cluster.day1_cilium_spec:
            values = copy.deepcopy(cluster.day1_cilium_spec)
            values.update({
                "cluster": {"name": cluster.name},
                "version": cluster.release.cilium_day1_version,
                "image": {"cilium": cluster.release.cilium_image, "operator": cluster.release.cilium_operator_image, "clife": cluster.release.clife_image},
                "devices": [mgmt_bond],
                "nodePort": {"directRoutingDevice": mgmt_bond},
                "k8sServiceHost": f"api-int.{cluster.name}.{cluster.base_domain}",
                "k8sServicePort": 6443,
                "ipam": {"mode": "cluster-pool", "operator": {"clusterPoolIPv4PodCIDRList": [n["cidr"] for n in defaults.ocp_cluster_network], "clusterPoolIPv4MaskSize": defaults.ocp_cluster_network[0]["hostPrefix"]}},
            })
            cilium_config_spec_yaml = yaml.safe_dump(values, sort_keys=False).rstrip()
        elif package:
            cilium_config_spec_yaml = yaml.safe_dump(
                resolved_management_cilium_config_spec(cluster), sort_keys=False
            ).rstrip()
        cc_body = render_template(f"{install_root}/openshift/ciliumconfig.yaml.j2", {
            **common_ctx,
            "cilium_package": package,
            "cilium_config_profile": cluster.in_cluster_load_balancer.cilium_config_profile,
            "cilium_config_spec_yaml": cilium_config_spec_yaml,
        }, root=root)
        files["manifests/ciliumconfig.yaml"] = header + "---\n" + cc_body
        network_body = render_template(
            f"{install_root}/openshift/cluster-network-02-config-local.yml.j2",
            {**common_ctx},
            root=root,
        )
        files["manifests/cluster-network-02-config-local.yml"] = header + "---\n" + network_body

    if runtime:
        config = yaml.safe_load(files["install-config.yaml"])
        config["pullSecret"] = json.dumps(runtime["pullSecret"], separators=(",", ":"))
        config["sshKey"] = runtime["sshKey"]
        if runtime.get("registryCA") is not None:
            config["additionalTrustBundle"] = runtime["registryCA"]
        files["install-config.yaml"] = header + yaml.safe_dump(config, sort_keys=False)
    return files


def write_ocp_outputs(out: Path, files: dict[str, str], private: bool = False) -> None:
    out.mkdir(parents=True, exist_ok=True)
    for obsolete in ("manifests/ciliumconfig.yaml", "manifests/cluster-network-02-config-local.yml"):
        if obsolete not in files:
            stale = out / obsolete
            if stale.exists() or stale.is_symlink():
                if stale.is_symlink() or not stale.is_file() or not stale.read_bytes().startswith(b"# Generated by render-ocp.py"):
                    raise ValueError(f"obsolete CNI path has another owner: {obsolete}")
                stale.unlink()
    for fname, content in files.items():
        path = out / fname
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if private:
            path.chmod(0o600)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Render OCP install artifacts from platform contract")
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--cluster", type=str, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--executable", action="store_true", help="Require declared hardware and private runtime files; never print enriched output")
    parser.add_argument("--pull-secret-file", type=Path)
    parser.add_argument("--ssh-key-file", type=Path)
    parser.add_argument("--registry-ca-file", type=Path)
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

    clusters = platform.clusters
    if args.cluster:
        clusters = [c for c in clusters if c.name == args.cluster]
        if not clusters:
            print(f"ERROR: cluster {args.cluster!r} not found", file=sys.stderr)
            sys.exit(2)

    if not platform.defaults:
        print("ERROR: No defaults file found in data/defaults/. Cannot render.", file=sys.stderr)
        sys.exit(2)

    runtime = None
    private_paths = [args.pull_secret_file, args.ssh_key_file]
    ca_required = any(c.registry_trust_policy == "additional-ca" for c in clusters)
    if args.executable:
        if args.dry_run or not args.cluster or not args.output or not all(private_paths):
            parser.error("--executable requires --cluster, external --output, pull-secret and SSH public-key files; --dry-run is forbidden")
        if ca_required and args.registry_ca_file is None:
            parser.error("declared additional-ca trust policy requires --registry-ca-file")
        if not ca_required and args.registry_ca_file is not None:
            parser.error("declared public trust policy must not add an unrelated registry CA")
        if args.output.resolve().is_relative_to(root.resolve()):
            parser.error("secret-bearing --output must be outside the source repository")
        try:
            runtime = {"pullSecret": json.loads(args.pull_secret_file.read_text()), "sshKey": args.ssh_key_file.read_text().strip(), "registryCA": args.registry_ca_file.read_text().strip() if args.registry_ca_file else None}
            if not runtime["pullSecret"].get("auths") or not runtime["sshKey"].startswith(("ssh-", "ecdsa-")) or (ca_required and "-----BEGIN CERTIFICATE-----" not in runtime["registryCA"]):
                raise ValueError("invalid runtime input")
        except (OSError, ValueError, TypeError, AttributeError):
            parser.error("runtime inputs must contain registry auths, an SSH public key and trusted registry CA PEM; values suppressed")
        if any(c.inventory_mode != "declared" for c in clusters):
            parser.error("--executable requires agentConfig.inventoryMode=declared")
        os.umask(0o077)
    elif any(private_paths) or args.registry_ca_file:
        parser.error("private runtime files require --executable")
    for cluster in clusters:
        files = render_ocp_install(cluster, platform.defaults, root=root, runtime=runtime)

        if args.dry_run:
            for fname, content in files.items():
                print(f"# === {fname} ===")
                print(content)
            print(f"\n# Rendered {len(files)} files for {cluster.name}", file=sys.stderr)
        else:
            out = args.output or (
                root / "generated" / "repos" / platform.defaults.identity["generatedRepos"]["openshift"]
                / "data" / "openshift" / cluster.name
            )
            write_ocp_outputs(out, files, private=bool(runtime))
            print(f"Written to {out}/: {', '.join(files.keys())}")


if __name__ == "__main__":
    main()
