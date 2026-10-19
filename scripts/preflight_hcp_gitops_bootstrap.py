#!/usr/bin/env python3
"""Read-only service CA drift gate before manual GitOps bootstrap sync."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import ssl
import subprocess
from pathlib import Path

import yaml


def fingerprints(pem: str) -> set[str]:
    certs = re.findall("-----BEGIN " "CERTIFICATE-----.*?-----END " "CERTIFICATE-----", pem, re.S)
    if not certs or "PRIVATE KEY" in pem or len(certs) != pem.count("BEGIN CERTIFICATE"):
        raise ValueError("expected public certificate bundle")
    found = {hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest() for cert in certs}
    if len(found) != len(certs):
        raise ValueError("duplicate public certificate")
    return found


def verify_snapshot(policy: dict, source: dict, target: dict, snapshot: str) -> None:
    trust = policy["spec"]["managementRegistryTrust"]
    internal = trust["internalPublicCA"]
    source_cert = source.get("data", {}).get(internal["sourceKey"])
    key = trust["internalRegistry"].replace(":", "..", 1)
    target_cert = target.get("data", {}).get(key)
    expected = set(internal["fingerprintsSha256"])
    source_expected = {internal["sourceFingerprintSha256"]}
    target_expected = set(internal["allowedCurrentTargetFingerprintsSha256"])
    if (not source_cert or not target_cert or fingerprints(snapshot) != expected or
            fingerprints(source_cert) != source_expected or fingerprints(target_cert) != target_expected or
            not source_expected.issubset(expected) or not target_expected.issubset(expected)):
        raise ValueError("service CA drift: refresh reviewed snapshot and target plan before sync")
    if target.get("metadata", {}).get("name") != trust["configMapName"]:
        raise ValueError("target ConfigMap identity changed")


def get_configmap(kubeconfig: Path, namespace: str, name: str) -> dict:
    result = subprocess.run(["oc", "--kubeconfig", str(kubeconfig), "-n", namespace,
                             "get", "configmap", name, "-o", "json"],
                            capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(f"read-only ConfigMap lookup failed: {namespace}/{name}")
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--kubeconfig", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    policy = yaml.safe_load((root / "data/clusters" / args.cluster / "gitops-bootstrap.yaml").read_text())
    if policy.get("metadata", {}).get("name") != args.cluster:
        raise ValueError("selected bootstrap policy identity differs")
    trust = policy["spec"]["managementRegistryTrust"]
    internal = trust["internalPublicCA"]
    source_namespace, source_name = internal["sourceConfigMap"].split("/", 1)
    source = get_configmap(args.kubeconfig, source_namespace, source_name)
    target = get_configmap(args.kubeconfig, trust["namespace"], trust["configMapName"])
    snapshot = (root / internal["file"]).read_text()
    verify_snapshot(policy, source, target, snapshot)
    print("PASS: selected service CA snapshot equals source and target live certificates")


if __name__ == "__main__":
    main()
