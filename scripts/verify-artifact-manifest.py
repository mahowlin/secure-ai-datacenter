#!/usr/bin/env python3
"""Verify target repo data files against a SAIF generated artifact manifest."""

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

import yaml

from hosted_cluster_paths import gitops_cluster_dirs
from site_identity import load_identity


EXPECTED_IMAGE_POLICY_BUNDLES = {
    "ocpInstallCore": "ocp-install-core",
    "ciliumDay1": "cilium-day1",
    "gitopsBootstrap": "gitops-bootstrap",
    "platformDay2": "platform-day2",
}

NON_DATA_TARGET_FILES = {"clusters/foundation/core/platform-idms.yaml"}
PROTECTED_TARGET_SUFFIXES = ("/registry-ca-bundle.yaml",)
PROTECTED_TARGET_PREFIXES = (
    "apps/hcp/management/hosted-cluster-lifecycle/overlays/",
)


def is_non_data_target_file(rel: str) -> bool:
    return rel in NON_DATA_TARGET_FILES or (
        rel.startswith(PROTECTED_TARGET_PREFIXES)
        and rel.endswith(PROTECTED_TARGET_SUFFIXES)
    )


_IDENTITY = load_identity()
_OPENSHIFT_REPO = _IDENTITY["generatedRepos"]["openshift"]
_GITOPS_REPO = _IDENTITY["generatedRepos"]["gitops"]
GENERATED_TARGET_PREFIXES = {
    _GITOPS_REPO: tuple(
        f"{path}/" for path in gitops_cluster_dirs(Path(__file__).resolve().parents[1])
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def expected_network_types(data_root: Path) -> dict[str, str]:
    expected: dict[str, str] = {}
    for metadata_path in sorted((data_root / "openshift").glob("*/metadata.yaml")):
        try:
            metadata = yaml.safe_load(metadata_path.read_text())
        except yaml.YAMLError:
            continue
        if isinstance(metadata, dict) and metadata.get("networkType"):
            expected[metadata_path.parent.name] = metadata["networkType"]
    return expected


def platform_day2_target_revision(profile: str) -> str:
    return f"0.0.0-platform-day2.{profile}"


def require_bundle_value(errors: list[str], bundles: dict, bundle_key: str, path: Path, description: str) -> None:
    expected_bundle = EXPECTED_IMAGE_POLICY_BUNDLES[bundle_key]
    actual = bundles.get(bundle_key)
    if bundle_key == "platformDay2" and isinstance(actual, dict):
        profile = actual.get("profile")
        target_revision = actual.get("targetRevision")
        if not profile:
            errors.append(f"{description}.{bundle_key}.profile is required in {path}")
        elif target_revision != platform_day2_target_revision(profile):
            errors.append(f"{description}.{bundle_key}.targetRevision must be {platform_day2_target_revision(profile)!r} in {path}")
        return
    if actual != expected_bundle:
        errors.append(f"{description}.{bundle_key} must be {expected_bundle!r} in {path}")


def ai_pod_cluster_folder_errors(data_root: Path, expected_paths: set[str]) -> list[str]:
    errors: list[str] = []
    for metadata_path in sorted((data_root / "openshift").glob("*/metadata.yaml")):
        cluster_dir = metadata_path.parent
        cluster_name = cluster_dir.name
        try:
            metadata = yaml.safe_load(metadata_path.read_text())
        except yaml.YAMLError as exc:
            errors.append(f"invalid metadata YAML in {metadata_path}: {exc}")
            continue
        if not isinstance(metadata, dict):
            errors.append(f"invalid metadata document in {metadata_path}")
            continue

        required = ["install-config.yaml", "agent-config.yaml", "metadata.yaml"]
        bundles = metadata.get("imagePolicyBundles", {}) or {}
        if not metadata.get("releaseRef"):
            errors.append(f"missing releaseRef metadata in {metadata_path}")
        for bundle_key in ("ocpInstallCore", "gitopsBootstrap", "platformDay2"):
            require_bundle_value(errors, bundles, bundle_key, metadata_path, "imagePolicyBundles")
        if metadata.get("networkType") == "Cilium":
            required.append("manifests/ciliumconfig.yaml")
            required.append("manifests/cluster-network-02-config-local.yml")
            if not metadata.get("ciliumDay1", {}).get("version"):
                errors.append(f"missing ciliumDay1.version metadata in {metadata_path}")
            if bundles.get("ciliumDay1") != EXPECTED_IMAGE_POLICY_BUNDLES["ciliumDay1"]:
                errors.append(f"imagePolicyBundles.ciliumDay1 must be 'cilium-day1' in {metadata_path}")

        for rel_name in required:
            path = cluster_dir / rel_name
            manifest_rel = f"openshift/{cluster_name}/{rel_name}"
            if not path.exists():
                errors.append(f"missing selected cluster artifact {path}")
            if manifest_rel not in expected_paths:
                errors.append(f"selected cluster artifact {manifest_rel} is not covered by artifact manifest")
    return errors


def stale_ocp_errors(path: Path, document: Any, expected_network_type: str | None) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return errors

    rel = path.as_posix()
    if rel.endswith("agent-config.yaml"):
        api_version = document.get("apiVersion")
        if document.get("kind") == "AgentConfig" and api_version and (api_version.endswith("/v1alpha1") or api_version == "v1alpha1"):
            errors.append(f"stale AgentConfig apiVersion in {path}")
        if document.get("kind") == "AgentConfig" and not document.get("additionalNTPSources"):
            errors.append(f"missing AgentConfig additionalNTPSources in {path}")

    if rel.endswith("install-config.yaml") and expected_network_type:
        network_type = document.get("networking", {}).get("networkType")
        if network_type != expected_network_type:
            errors.append(f"install-config networkType {network_type!r} does not match metadata networkType {expected_network_type!r} in {path}")

    if rel.endswith("metadata.yaml"):
        if document.get("ocpVersion") == "4.19":
            errors.append(f"stale broad ocpVersion 4.19 in {path}")
        if not document.get("releaseRef"):
            errors.append(f"missing rendered releaseRef metadata in {path}")
        if not document.get("ntpServers"):
            errors.append(f"missing rendered ntpServers metadata in {path}")
        if not document.get("networkType"):
            errors.append(f"missing rendered networkType metadata in {path}")
        bundles = document.get("imagePolicyBundles", {}) or {}
        for bundle_key in ("ocpInstallCore", "gitopsBootstrap", "platformDay2"):
            require_bundle_value(errors, bundles, bundle_key, path, "rendered imagePolicyBundles")

    if rel.endswith("release-contract.yaml"):
        if document.get("kind") != "ClusterReleaseContract":
            errors.append(f"expected ClusterReleaseContract in {path}")
        spec = document.get("spec", {}) if isinstance(document, dict) else {}
        if not spec.get("releaseRef"):
            errors.append(f"missing ClusterReleaseContract spec.releaseRef in {path}")
        bundles = spec.get("imagePolicyBundles", {}) or {}
        require_bundle_value(errors, bundles, "platformDay2", path, "ClusterReleaseContract spec.imagePolicyBundles")

    if (expected_network_type and expected_network_type != "Cilium"
            and (rel.endswith("manifests/ciliumconfig.yaml")
                 or rel.endswith("manifests/cluster-network-02-config-local.yml"))):
        errors.append(f"forbidden Cilium Day1 artifact for {expected_network_type}: {path}")

    if rel.endswith("ciliumconfig.yaml"):
        if document.get("kind") != "CiliumConfig":
            errors.append(f"expected CiliumConfig in {path}")
        spec = document.get("spec", {}) if isinstance(document, dict) else {}
        if not spec.get("version"):
            errors.append(f"missing CiliumConfig spec.version in {path}")
        if not spec.get("image", {}).get("cilium"):
            errors.append(f"missing CiliumConfig spec.image.cilium in {path}")

    if rel.endswith("cluster-network-02-config-local.yml"):
        if document.get("apiVersion") != "operator.openshift.io/v1" or document.get("kind") != "Network":
            errors.append(f"expected OpenShift Network operator manifest in {path}")
        if document.get("metadata", {}).get("name") != "cluster":
            errors.append(f"expected Network.operator/cluster in {path}")
        spec = document.get("spec", {}) if isinstance(document, dict) else {}
        if spec.get("networkType") != "Cilium":
            errors.append(f"Network.operator/cluster spec.networkType must be 'Cilium' in {path}")
        if spec.get("deployKubeProxy") is not False:
            errors.append(f"Network.operator/cluster spec.deployKubeProxy must be false for Cilium KPR in {path}")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify SAIF artifact manifest for one target repo")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--repo", required=True, help="target repo name from spec.identity.generatedRepos")
    parser.add_argument("--target-root", type=Path, required=True, help="target repo checkout root")
    parser.add_argument("--fail-on-extra", action="store_true", help="fail if target data contains files not listed in manifest")
    args = parser.parse_args()

    manifest = yaml.safe_load(args.manifest.read_text())
    target = None
    for candidate in manifest.get("targets", []):
        if candidate.get("repo") == args.repo:
            target = candidate
            break

    if target is None:
        print(f"ERROR: repo {args.repo!r} not found in manifest", file=sys.stderr)
        sys.exit(1)

    target_data_root = args.target_root / target.get("targetRoot", "data")
    errors: list[str] = []
    expected_paths = {entry["path"] for entry in target.get("files", [])}
    network_types = expected_network_types(target_data_root) if args.repo == _OPENSHIFT_REPO else {}
    if args.repo == _OPENSHIFT_REPO:
        errors.extend(ai_pod_cluster_folder_errors(target_data_root, expected_paths))
    if args.repo == _GITOPS_REPO:
        for contract_path in sorted((target_data_root / "clusters").glob("*/release-contract.yaml")):
            cluster_name = contract_path.parent.name
            manifest_rel = f"clusters/{cluster_name}/release-contract.yaml"
            if manifest_rel not in expected_paths:
                errors.append(f"GitOps release contract {manifest_rel} is not covered by artifact manifest")
            kustomization_path = contract_path.parent / "kustomization.yaml"
            if not kustomization_path.is_file():
                errors.append(f"GitOps release contract has no sibling kustomization.yaml in {contract_path.parent}")
            else:
                kustomization = yaml.safe_load(kustomization_path.read_text())
                resources = kustomization.get("resources", []) if isinstance(kustomization, dict) else []
                if "release-contract.yaml" in resources:
                    errors.append(f"GitOps release contract must not be consumed by {kustomization_path}")

    for entry in target.get("files", []):
        path = target_data_root / entry["path"]
        if not path.exists():
            errors.append(f"missing {path}")
            continue
        actual = sha256_file(path)
        if actual != entry["sha256"]:
            errors.append(f"checksum mismatch {path}: expected {entry['sha256']} got {actual}")
        if args.repo == _OPENSHIFT_REPO and entry["path"].startswith("openshift/") and path.suffix in {".yaml", ".yml"}:
            try:
                cluster_name = Path(entry["path"]).parts[1]
                for document in yaml.safe_load_all(path.read_text()):
                    errors.extend(stale_ocp_errors(path, document, network_types.get(cluster_name)))
            except yaml.YAMLError as exc:
                errors.append(f"invalid YAML in generated OpenShift artifact {path}: {exc}")

    if args.fail_on_extra and target_data_root.exists():
        prefixes = GENERATED_TARGET_PREFIXES.get(args.repo)
        paths = []
        if prefixes:
            for prefix in prefixes:
                prefix_path = target_data_root / prefix
                if prefix_path.exists():
                    paths.extend(path for path in prefix_path.rglob("*") if path.is_file())
        else:
            paths = [path for path in target_data_root.rglob("*") if path.is_file()]
        for path in sorted(paths):
            rel = path.relative_to(target_data_root).as_posix()
            if is_non_data_target_file(rel):
                continue
            if rel not in expected_paths:
                errors.append(f"extra unmanaged data file {path}")

    if errors:
        print(f"INVALID: {len(errors)} artifact mismatch(es):", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        sys.exit(1)

    print(f"VALID: {args.repo} data matches {args.manifest}")


if __name__ == "__main__":
    main()
