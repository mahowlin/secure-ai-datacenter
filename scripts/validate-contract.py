#!/usr/bin/env python3
"""Validate platform contract files (NetworkCatalog, OCPCluster, HCPCluster).

Uses the shared resolver to build the object graph and validate all rules
from RENDERER_IMPLEMENTATION_SPEC.md.

Usage:
  validate-contract.py [--root DIR]

Exit 0 if valid, exit 1 with all violations listed.
"""

import argparse
import sys
from pathlib import Path

from resolver import resolve, validate, has_contract_inputs


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate platform contract files")
    parser.add_argument("--root", type=Path, default=None,
                        help="Platform root (auto-detected)")
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

    # Summary
    n_nets = len(platform.all_networks)
    n_clusters = len(platform.clusters)
    n_hcp = sum(len(c.hosted_clusters) for c in platform.clusters)

    if errors:
        print(f"INVALID: {len(errors)} violation(s):", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)
    else:
        print(f"VALID: {n_nets} networks, {n_clusters} clusters, {n_hcp} hosted clusters")
        print(f"  Catalog: {platform.catalog_name}")
        print(f"  Fabric ASN: {platform.fabric.fabric_asn}")
        if platform.profile_intent:
            intent = platform.profile_intent
            print(
                f"  Profile contract: {intent.schema_version} "
                f"(legacy_adapter={str(intent.legacy_adapter_used).lower()}, "
                f"catalog_sha256={intent.catalog_digest})"
            )
            for diagnostic in intent.diagnostics:
                print(
                    f"  {diagnostic.severity.upper()} [{diagnostic.code}] "
                    f"{diagnostic.source}: {diagnostic.message}"
                )
        for cluster in platform.clusters:
            print(f"  Cluster: {cluster.name} (mode={cluster.mode}, nodes={len(cluster.nodes)}, VLANs={[n.vlan for n in cluster.vlan_union]})")
            for hcp in cluster.hosted_clusters:
                print(f"    HCP: {hcp.name} (workers={[n.name for n in hcp.resolved_worker_networks]}, pools={len(hcp.resolved_lb_pools)}lb/{len(hcp.resolved_egress_pools)}egw)")
        sys.exit(0)


if __name__ == "__main__":
    main()
