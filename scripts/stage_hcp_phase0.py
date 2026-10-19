#!/usr/bin/env python3
"""Compose an additive selected HCP phase-0 review in an isolated target checkout.

The SAIF render owns successor resources. The target owns its public registry CA
ConfigMaps and retained predecessor resources. This script joins those inputs;
it never contacts a cluster or publishes the checkout.
"""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import yaml

from site_identity import api_label


ROOT = Path(__file__).resolve().parents[1]
HEADER = "# Composed from SAIF generated phase-0 and retained target ownership.\n---\n"
HCP_ROOT = Path("apps/hcp/management/hosted-cluster-lifecycle/overlays")
PARENT_ROOT = Path("apps/hcp/management/parent-network/overlays")


def _ilb_hosted_namespace(root: Path) -> str:
    defaults = root / "data" / "defaults"
    for path in sorted(defaults.glob("*.yaml")):
        document = read(path)
        if not isinstance(document, dict) or document.get("kind") != "PlatformDefaults":
            continue
        namespace = (
            (document.get("spec") or {}).get("identity") or {}
        ).get("ilb", {}).get("hostedDestinationNamespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("ILB hosted destination namespace is required")
        return namespace
    raise ValueError("PlatformDefaults are required")


def read(path: Path):
    if not path.is_file():
        raise ValueError(f"required target dependency absent: {path}")
    return yaml.safe_load(path.read_text())


def write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + yaml.safe_dump(value, sort_keys=False))


def copy_rendered(source: Path, target: Path, relative: Path) -> None:
    original = source / relative
    if not original.is_file():
        raise ValueError(f"generated phase-0 resource absent: {relative}")
    destination = target / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(original, destination)


def manual_app(name: str, path: str, destination: dict, phase: str,
               repo_url: str, revision: str, gitops_namespace: str) -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1", "kind": "Application",
        "metadata": {"name": name, "namespace": gitops_namespace, "annotations": {
            api_label("activation"): "manual-review-required",
            api_label("cutover-stage"): phase,
        }},
        "spec": {"project": "default", "source": {"repoURL": repo_url,
            "targetRevision": revision, "path": path}, "destination": destination,
            "syncPolicy": {"syncOptions": ["SkipDryRunOnMissingResource=true"]}},
    }


def stage_console_login(root: Path, source: Path, target: Path,
                        cluster_name: str) -> None:
    """Consume prepared login resources inside the existing held HC owner only.

    Provider rendering and sealed credential custody remain upstream owners.
    Retained target bytes supply all fields outside configuration.oauth.
    Validate the whole selected set before writing any candidate resource.
    """
    cluster_document = read(root / "data/clusters" / cluster_name / "cluster.yaml")
    if (cluster_document.get("kind") != "OCPCluster"
            or cluster_document.get("metadata", {}).get("name") != cluster_name):
        raise ValueError("selected management cluster input identity differs")
    lifecycle = cluster_document.get("spec", {}).get("day2GitOps", {}).get(
        "hostedClusterLifecycle", {})
    policy_document = read(root / "data/clusters" / cluster_name / "login.yaml")
    policy = policy_document.get("spec", {})
    if (policy_document.get("kind") != "ConsoleLogin"
            or policy.get("hostedTargets") != "deployment-subjects"
            or not policy.get("secretName") or not policy.get("secretKey")):
        raise ValueError("declared hosted console login references are required")
    subjects = lifecycle.get("deploymentSubjects", {})
    names = [
        value for key, value in subjects.items()
        if key != "stagedOnly" and isinstance(value, str) and value
    ]
    if not all(isinstance(name, str) and name for name in names):
        raise ValueError("declared deployment subjects are required")
    if len(set(names)) != len(names) or set(names) & set(subjects.get("stagedOnly", [])):
        raise ValueError("selected login subjects must be distinct deployed subjects")
    base = Path("apps/hcp/management/hosted-cluster-lifecycle/overlays") / cluster_name
    staged = base / "phase0"
    retained = read(target / base / "kustomization.yaml")
    selected = read(target / staged / "kustomization.yaml")
    if (any(name in retained["resources"] for name in names)
            or selected["resources"] != names):
        raise ValueError("selected login resources require the existing phase0 owner")
    app_path = (target / "clusters/_staged" / f"{cluster_name}-services-phase0"
                / f"{cluster_name}-hosted-clusters.yaml")
    app = read(app_path)
    if (app["spec"]["source"]["path"] != str(staged)
            or "automated" in app["spec"].get("syncPolicy", {})):
        raise ValueError("selected HostedCluster Application must remain held")
    updates = []
    for name in names:
        original = source / base / name
        destination = target / staged / name
        rendered = read(original / "hostedcluster.yaml")
        path = destination / "hostedcluster.yaml"
        existing = read(path)
        if (rendered.get("kind") != "HostedCluster"
                or existing.get("kind") != "HostedCluster"
                or rendered.get("apiVersion") != existing.get("apiVersion")
                or rendered.get("metadata", {}).get("name") != name
                or any(rendered["metadata"].get(key) != existing["metadata"].get(key)
                       for key in ("name", "namespace"))):
            raise ValueError(f"{name} rendered and retained HostedCluster identity differs")
        oauth = rendered.get("spec", {}).get("configuration", {}).get("oauth")
        if not isinstance(oauth, dict) or not oauth.get("identityProviders"):
            raise ValueError(f"{name} prepared hosted OAuth configuration is absent")
        # Consume, never regenerate, the upstream provider policy.
        providers = oauth["identityProviders"]
        if (len(providers) != 1
                or providers[0].get("name") != policy.get("providerName")
                or providers[0].get("type") != policy.get("type")
                or providers[0].get("mappingMethod") != policy.get("mappingMethod")
                or providers[0].get("htpasswd", {}).get("fileData", {}).get("name")
                   != policy["secretName"]):
            raise ValueError(f"{name} rendered OAuth differs from declared login inputs")
        secret_name = "console-login-sealedsecret.yaml"
        prepared = original / secret_name
        sealed = read(prepared)
        metadata = sealed.get("metadata", {})
        if (sealed.get("kind") != "SealedSecret"
                or sealed.get("apiVersion") != "bitnami.com/v1alpha1"
                or metadata.get("name") != policy["secretName"]
                or metadata.get("namespace") != existing["metadata"]["namespace"]
                or set(sealed.get("spec", {}).get("encryptedData", {})) != {policy["secretKey"]}
                or "data" in sealed or "stringData" in sealed):
            raise ValueError(f"{name} prepared sealed reference differs from hosted ownership")
        rendered_resources = read(original / "kustomization.yaml")["resources"]
        if secret_name not in rendered_resources:
            raise ValueError(f"{name} prepared sealed resource is not referenced")
        overlay_path = destination / "kustomization.yaml"
        overlay = read(overlay_path)
        if "namespace.yaml" in overlay["resources"]:
            raise ValueError(f"{name} namespace must retain its separate owner")
        existing_configuration = existing.get("spec", {}).get("configuration")
        if not isinstance(existing_configuration, dict):
            raise ValueError(f"{name} retained HostedCluster configuration must be a mapping")
        if overlay["resources"].count(secret_name) > 1:
            raise ValueError(f"{name} sealed resource has duplicate references")
        old_oauth = existing_configuration.get("oauth")
        if old_oauth is not None and old_oauth != oauth:
            raise ValueError(f"{name} existing OAuth differs; provider replacement is not a consumer refresh")
        text = path.read_text()
        if old_oauth is None:
            document = yaml.compose(text)
            def child(node, key):
                if not isinstance(node, yaml.MappingNode) or node.flow_style:
                    raise ValueError(f"{name} retained YAML requires a block mapping")
                entries = [value for field, value in node.value if field.value == key]
                if len(entries) != 1:
                    raise ValueError(f"{name} retained YAML has absent/duplicate {key}")
                return entries[0]
            configuration = child(child(document, "spec"), "configuration")
            if not isinstance(configuration, yaml.MappingNode) or configuration.flow_style:
                raise ValueError(f"{name} retained configuration must be a block mapping")
            indent = configuration.start_mark.column
            addition = yaml.safe_dump({"oauth": oauth}, sort_keys=False)
            addition = "".join(" " * indent + line + "\n" for line in addition.splitlines())
            # The end mark sits at the next key column; insert at its line start.
            at = text.rfind("\n", 0, configuration.end_mark.index) + 1
            text = text[:at] + addition + text[at:]
            expected = copy.deepcopy(existing)
            expected["spec"]["configuration"]["oauth"] = copy.deepcopy(oauth)
            if yaml.safe_load(text) != expected:
                raise ValueError(f"{name} retained YAML cannot accept a scoped OAuth addition")
        if secret_name not in overlay["resources"]:
            overlay["resources"].append(secret_name)
        updates.append((path, text, prepared, destination / secret_name,
                        overlay_path, overlay))
    for path, text, prepared, sealed_target, overlay_path, overlay in updates:
        if path.read_text() != text:
            path.write_text(text)
        # Already prepared ciphertext is transported opaquely, without resealing.
        shutil.copy2(prepared, sealed_target)
        if read(overlay_path) != overlay:
            write(overlay_path, overlay)
