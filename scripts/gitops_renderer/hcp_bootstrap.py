"""Public management trust and metadata-only GitOps bootstrap manifests."""

from __future__ import annotations

import hashlib
import re
import ssl
from pathlib import Path

import yaml

from .common import HEADER, _k8s_yaml, _kustomization


def _public_ca(root: Path, relative: str, expected: str | list[str]) -> str:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("public CA input path escapes or is absent")
    pem = path.read_text()
    matches = re.findall("-----BEGIN " "CERTIFICATE-----.*?-----END " "CERTIFICATE-----", pem, re.S)
    wanted = [expected] if isinstance(expected, str) else expected
    fingerprints = [hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert)).hexdigest() for cert in matches]
    if (not matches or "PRIVATE KEY" in pem or len(matches) != pem.count("BEGIN CERTIFICATE") or
            len(set(fingerprints)) != len(fingerprints) or set(fingerprints) != set(wanted) or
            len(fingerprints) != len(wanted)):
        raise ValueError("public CA fingerprint differs from selected policy")
    return "\n".join(matches) + "\n"


def _registry_key(address: str) -> str:
    if not re.fullmatch(r"[a-z0-9.-]+:[0-9]+", address):
        raise ValueError("registry endpoint must be explicit host:port")
    return address.replace(":", "..", 1)


def render_hcp_bootstrap(root: Path, cluster_name: str,
                               subjects: dict[str, str], registry_address: str,
                               release_repository: str) -> dict[str, str]:
    policy_path = root / f"data/clusters/{cluster_name}/gitops-bootstrap.yaml"
    if not policy_path.is_file():
        raise ValueError("explicitly enabled GitOps bootstrap policy absent")
    policy = yaml.safe_load(policy_path.read_text())
    if (policy.get("kind") != "GitOpsBootstrap" or
            policy.get("metadata", {}).get("name") != cluster_name or
            policy.get("spec", {}).get("activation") != "review-only"):
        raise ValueError("selected GitOps bootstrap policy identity differs")
    spec = policy["spec"]
    secret = spec["sealedSecrets"]
    roles = ("multus",)
    if (secret["scope"] != "strict" or secret["cloudAuthHostToOmit"] != "cloud.openshift.com" or
            set(secret["sourceNamespaceByRole"]) != set(roles) or
            not all(re.fullmatch(r"clusters-[a-z0-9-]+", ns) for ns in secret["sourceNamespaceByRole"].values()) or
            not secret["retainedPullNamespaces"] or
            not all(re.fullmatch(r"clusters-[a-z0-9-]+", ns) for ns in secret["retainedPullNamespaces"]) or
            len(set(secret["retainedPullNamespaces"])) != len(secret["retainedPullNamespaces"]) or
            not re.fullmatch(r"[a-z0-9-]+", secret["pullSecretName"]) or
            not re.fullmatch(r"[a-z0-9-]+", secret["sshSecretName"]) or
            secret["pullSecretName"] == secret["sshSecretName"] or
            secret["existingSecretAdoption"] != "managed-skip-owner-reference" or
            secret["insightsReporting"] != "disabled" or
            secret["preserveOtherRegistryAuth"] is not True or
            secret["preserveLocalMonitoring"] is not True or
            secret["managementGlobalPullSecretAlreadyOptedOut"] is not True or
            len({subjects[role] for role in roles}) != len(roles)):
        raise ValueError("selected sealed Secret custody differs from selected policy")
    trust = spec["managementRegistryTrust"]
    if (trust["externalPublicCA"]["addressRef"] != "defaults.registry.url" or
            not release_repository.startswith(registry_address + "/") or
            trust["adoption"] != "server-side-apply-owned-keys"):
        raise ValueError("selected release, registry and trust adoption disagree")
    internal = trust["internalPublicCA"]
    external = trust["externalPublicCA"]
    base = (f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{cluster_name}/"
            "bootstrap")
    protected = "ServerSideApply=true,Prune=false,Delete=false"
    files = {}
    names = []
    internal_file = (root / internal["file"]).resolve()
    external_file = (root / external["file"]).resolve()
    if internal_file.is_file() or external_file.is_file():
        internal_pem = _public_ca(root, internal["file"], internal["fingerprintsSha256"])
        external_pem = _public_ca(root, external["file"], external["fingerprintSha256"])
        cm = {
            "apiVersion": "v1", "kind": "ConfigMap",
            "metadata": {"name": trust["configMapName"], "namespace": trust["namespace"],
                         "annotations": {"argocd.argoproj.io/sync-wave": "8",
                                         "argocd.argoproj.io/sync-options": protected}},
            "data": {_registry_key(trust["internalRegistry"]): internal_pem,
                     _registry_key(registry_address): external_pem},
        }
        image = {
            "apiVersion": "config.openshift.io/v1", "kind": "Image",
            "metadata": {"name": trust["imageConfigName"],
                         "annotations": {"argocd.argoproj.io/sync-wave": "9",
                                         "argocd.argoproj.io/sync-options": protected}},
            "spec": {"additionalTrustedCA": {"name": trust["configMapName"]}},
        }
        files[f"{base}/management-registry-trust.yaml"] = HEADER + _k8s_yaml(cm)
        files[f"{base}/management-image-config.yaml"] = HEADER + _k8s_yaml(image)
        names.extend(["management-registry-trust.yaml", "management-image-config.yaml"])
    for namespace in secret["retainedPullNamespaces"]:
        name = namespace.removeprefix("clusters-")
        adoption = {
            "apiVersion": "v1", "kind": "Secret",
            "metadata": {"name": secret["pullSecretName"], "namespace": namespace,
                         "annotations": {
                             "argocd.argoproj.io/sync-wave": "7",
                             "argocd.argoproj.io/sync-options": protected,
                             "sealedsecrets.bitnami.com/managed": "true",
                             "sealedsecrets.bitnami.com/skip-set-owner-references": "true",
                         }},
            "type": "kubernetes.io/dockerconfigjson",
        }
        files[f"{base}/adopt-{name}-{secret['pullSecretName']}.yaml"] = HEADER + _k8s_yaml(adoption)
    names.extend(f"adopt-{ns.removeprefix('clusters-')}-{secret['pullSecretName']}.yaml"
                 for ns in secret["retainedPullNamespaces"])
    files[f"{base}/kustomization.yaml"] = _kustomization(names)
    return files
