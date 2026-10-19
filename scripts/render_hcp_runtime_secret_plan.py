#!/usr/bin/env python3
"""Emit a secret-free sys-admin handoff for selected HostedCluster runtime Secrets."""

import argparse
import json
from pathlib import Path

import yaml

from resolver import hosted_control_plane_name, resolve, validate
from site_identity import load_identity


ROOT = Path(__file__).resolve().parents[1]


def render(root: Path, cluster_name: str) -> dict:
    platform = resolve(root)
    errors = validate(platform)
    if errors:
        raise ValueError("invalid selected source: " + "; ".join(errors))
    cluster = next(item for item in platform.clusters if item.name == cluster_name)
    policy = yaml.safe_load((root / "data/clusters" / cluster_name /
                             "runtime-secrets.yaml").read_text())
    spec = policy["spec"]
    roles = ("multus",)
    if (policy["kind"] != "HCPRuntimeSecretHandoff"
            or policy["metadata"]["name"] != cluster_name
            or spec["owner"] != load_identity(root)["sourceRepositories"]["sysAdmin"].rsplit("/", 1)[-1]
            or spec["timing"] != "after-target-namespace-before-hostedcluster-sync"
            or set(spec["sourceNamespaceByRole"]) != set(roles)
            or spec["secrets"] != {
                "pull-secret": {"type": "kubernetes.io/dockerconfigjson", "keys": [".dockerconfigjson"]},
                "ssh-key": {"type": "Opaque", "keys": ["id_rsa.pub"]},
            }):
        raise ValueError("unsupported selected runtime Secret handoff")
    subjects = cluster.hosted_cluster_lifecycle["deploymentSubjects"]
    targets = []
    for role in roles:
        name = subjects[role]
        targets.append({"role": role, "cluster": name,
                        "sourceNamespace": spec["sourceNamespaceByRole"][role],
                        "targetNamespace": f"clusters-{name}",
                        "secrets": spec["secrets"]})
    return {"schemaVersion": 1, "owner": spec["owner"], "cluster": cluster_name,
            "timing": spec["timing"], "targets": targets}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--cluster", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    root = args.root.resolve()
    args.output.write_text(json.dumps(render(root, hosted_control_plane_name(root, args.cluster)),
                                      indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
