#!/usr/bin/env python3
"""Seal reviewed HCP bootstrap Secrets in memory for target GitOps custody.

This produces review files only. It never applies resources to a cluster.
"""

import argparse
import base64
import json
import hashlib
import ssl
import subprocess
import tempfile
from pathlib import Path

import yaml
from resolver import hosted_control_plane_name, resolve, validate


def run(command, *, input_bytes=None):
    result = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed: {command[0]}")
    return result.stdout


def selected_subjects(root: Path, cluster_name: str):
    platform = resolve(root)
    errors = validate(platform)
    if errors:
        raise ValueError("SAIF source invalid: " + "; ".join(errors))
    cluster = next((item for item in platform.clusters if item.name == cluster_name), None)
    if cluster is None:
        raise ValueError("selected cluster absent")
    if cluster.hosted_cluster_lifecycle.get("gitopsBootstrap") is not True:
        raise ValueError("selected cluster has no enabled GitOps bootstrap")
    policy = yaml.safe_load((root / "data/clusters" / cluster_name / "gitops-bootstrap.yaml").read_text())
    if policy.get("metadata", {}).get("name") != cluster_name or policy["spec"]["activation"] != "review-only":
        raise ValueError("selected bootstrap policy differs")
    spec = policy["spec"]["sealedSecrets"]
    if spec["scope"] != "strict" or spec["cloudAuthHostToOmit"] != "cloud.openshift.com":
        raise ValueError("bootstrap custody differs from review")
    subjects = cluster.hosted_cluster_lifecycle["deploymentSubjects"]
    selected = [(namespace, namespace, spec["pullSecretName"])
                for namespace in spec["retainedPullNamespaces"]]
    for role in ("multus",):
        namespace = "clusters-" + subjects[role]
        source = spec["sourceNamespaceByRole"][role]
        selected.extend([(source, namespace, spec["pullSecretName"]),
                         (source, namespace, spec["sshSecretName"])])
    if len(selected) != len(spec["retainedPullNamespaces"]) + 2 or len({(target, name) for _, target, name in selected}) != len(selected):
        raise ValueError("selected Secret identities differ from review")
    return spec, selected


def reviewed_secret(source: dict, target_ns: str, name: str, spec: dict) -> dict:
    key = spec["pullSecretKey"] if name == spec["pullSecretName"] else spec["sshPublicKey"]
    if source.get("metadata", {}).get("name") != name or set(source.get("data", {})) != {key}:
        raise ValueError("source Secret identity or keys differ")
    expected_type = "kubernetes.io/dockerconfigjson" if name == spec["pullSecretName"] else "Opaque"
    if source.get("type") != expected_type:
        raise ValueError("source Secret type differs")
    data = dict(source["data"])
    if name == spec["pullSecretName"]:
        docker = json.loads(base64.b64decode(data[key], validate=True))
        auths = docker.get("auths")
        if not isinstance(auths, dict) or not auths:
            raise ValueError("source registry auths absent")
        original = {host for host in auths if host != spec["cloudAuthHostToOmit"]}
        auths.pop(spec["cloudAuthHostToOmit"], None)
        if not auths or set(auths) != original:
            raise ValueError("registry auth preservation failed")
        data[key] = base64.b64encode(json.dumps(docker, sort_keys=True, separators=(",", ":")).encode()).decode()
    return {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": name, "namespace": target_ns},
            "type": expected_type, "data": data}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--kubeconfig", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cluster", default=None)
    args = parser.parse_args()
    root = args.root.resolve()
    spec, subjects = selected_subjects(root, hosted_control_plane_name(root, args.cluster))
    cert = run(["kubeseal", "--kubeconfig", str(args.kubeconfig),
                "--controller-namespace", spec["controller"].split("/")[0],
                "--controller-name", spec["controller"].split("/")[1], "--fetch-cert"])
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.decode())).hexdigest()
    if fingerprint != spec["publicCertificateFingerprintSha256"]:
        raise ValueError("SealedSecrets controller certificate changed; review and reseal required")
    with tempfile.TemporaryDirectory() as temporary:
        cert_path = Path(temporary) / "controller-public.pem"
        cert_path.write_bytes(cert)
        for source_ns, target_ns, name in subjects:
            source = json.loads(run(["oc", "--kubeconfig", str(args.kubeconfig),
                                     "-n", source_ns, "get", "secret", name,
                                     "-o", "json"]))
            secret = reviewed_secret(source, target_ns, name, spec)
            sealed = yaml.safe_load(run(["kubeseal", "--cert", str(cert_path),
                                         "--scope", "strict", "--format", "yaml"],
                                        input_bytes=json.dumps(secret).encode()))
            if (sealed.get("kind") != "SealedSecret" or
                    sealed["metadata"]["name"] != name or
                    sealed["metadata"]["namespace"] != target_ns or
                    set(sealed["spec"]["encryptedData"]) != set(secret["data"])):
                raise RuntimeError("sealed resource identity or keys differ")
            sealed["metadata"].setdefault("annotations", {})["argocd.argoproj.io/sync-wave"] = "8"
            if target_ns in spec["retainedPullNamespaces"]:
                sealed["metadata"]["annotations"]["argocd.argoproj.io/sync-options"] = "Prune=false,Delete=false"
                template_annotations = sealed["spec"].setdefault("template", {}).setdefault("metadata", {}).setdefault("annotations", {})
                template_annotations["sealedsecrets.bitnami.com/managed"] = "true"
                template_annotations["sealedsecrets.bitnami.com/skip-set-owner-references"] = "true"
                template_annotations["argocd.argoproj.io/sync-wave"] = "7"
                template_annotations["argocd.argoproj.io/sync-options"] = (
                    "ServerSideApply=true,Prune=false,Delete=false"
                )
            destination = args.output / f"{target_ns}-{name}.yaml"
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(yaml.safe_dump(sealed, sort_keys=False))
            print(f"sealed {target_ns}/{name}")


if __name__ == "__main__":
    main()
