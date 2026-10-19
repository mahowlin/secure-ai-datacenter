#!/usr/bin/env python3
"""Render a secret-free remote-health opt-out plan from canonical HCP inputs.

This emits review data only. Runtime pull-secret transformation belongs to the
declared owner after registry maintenance and a separate release.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from resolver import resolve, validate


def render_plan(root: Path, cluster_name: str) -> dict:
    root = root.resolve()
    platform = resolve(root)
    errors = validate(platform)
    if errors:
        raise ValueError("invalid platform inputs: " + "; ".join(errors))
    cluster = next((item for item in platform.clusters if item.name == cluster_name), None)
    if cluster is None:
        raise ValueError(f"unknown management cluster: {cluster_name}")
    policy_path = root / "data/clusters" / cluster.name / "remote-health.yaml"
    policy = yaml.safe_load(policy_path.read_text())
    spec = policy.get("spec") if isinstance(policy, dict) else None
    if (not isinstance(policy, dict)
            or policy.get("kind") != "RemoteHealthPolicy"
            or policy.get("metadata", {}).get("name") != cluster.name
            or not isinstance(spec, dict)
            or spec.get("reporting") != "disabled"
            or spec.get("supportedMethod") != "remove-cloud-auth-from-global-pull-secret"
            or spec.get("cloudAuthHost") != "cloud.openshift.com"
            or not isinstance(spec.get("runtimeOwner"), str)
            or not spec["runtimeOwner"]
            or not isinstance(spec.get("activation"), str)
            or not spec["activation"]
            or spec.get("verification") != {
                "preserveOtherRegistryAuth": True,
                "confirmNoNewUploads": True,
                "preserveLocalMonitoring": True,
            }):
        raise ValueError("remote health policy is incomplete or unsupported")
    management_secret = spec.get("managementPullSecret") or {}
    hosted_secret = spec.get("hostedPullSecret") or {}
    if (not isinstance(management_secret, dict)
            or not isinstance(hosted_secret, dict)
            or not all(isinstance(management_secret.get(key), str) and management_secret[key]
                for key in ("namespace", "name"))
            or not isinstance(hosted_secret.get("name"), str)
            or not hosted_secret["name"]):
        raise ValueError("remote health policy pull-secret references are incomplete")
    rendered = {hcp.name for hcp in cluster.hosted_clusters}
    retained = set(cluster.retained_hosted_network_claims)
    names = sorted(rendered | retained)
    existing = spec.get("existingHostedClusters")
    if (not isinstance(existing, list) or len(existing) != len(set(existing))
            or any(not isinstance(name, str) for name in existing)
            or not set(existing) <= set(names)):
        raise ValueError("remote health existingHostedClusters must name distinct in-scope clusters")
    targets = [{"cluster": cluster.name, "kind": "management",
                "namespace": management_secret["namespace"],
                "secret": management_secret["name"], "presence": "required"}]
    targets.extend({"cluster": name, "kind": "hosted",
                    "namespace": f"clusters-{name}", "secret": hosted_secret["name"],
                    "presence": "required" if name in existing else "when-present"}
                   for name in names)
    return {
        "source": str(policy_path.relative_to(root)),
        "reporting": spec["reporting"],
        "method": spec["supportedMethod"],
        "cloudAuthHost": spec["cloudAuthHost"],
        "runtimeOwner": spec["runtimeOwner"],
        "activation": spec["activation"],
        "targets": targets,
        "verification": spec["verification"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    print(json.dumps(render_plan(args.root, args.cluster), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
