#!/usr/bin/env python3
"""Validate promoted sys-admin image-policy bundles for a selected SAIF release."""

from __future__ import annotations
from site_identity import api_label, registry_url

import argparse
import json
import hashlib
import re
import shutil
import sys
from pathlib import Path

import yaml

from resolver import ResolvedOCPCluster, has_contract_inputs, resolve, validate

BUNDLE_KEYS = {
    "ocpInstallCore": "ocp_install_core_bundle",
    "ciliumDay1": "cilium_day1_bundle",
    "gitopsBootstrap": "gitops_bootstrap_bundle",
    "platformDay2": "platform_day2_bundle",
}


def selected_cluster(root: Path, cluster_name: str) -> ResolvedOCPCluster:
    platform = resolve(root)
    errors = validate(platform)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    for cluster in platform.clusters:
        if cluster.name == cluster_name:
            return cluster
    print(f"ERROR: cluster {cluster_name!r} not found", file=sys.stderr)
    raise SystemExit(2)


def platform_day2_target_revision(profile: str) -> str:
    return f"0.0.0-platform-day2.{profile}"


def required_scopes(cluster: ResolvedOCPCluster) -> dict[str, str]:
    release = cluster.release
    if not release:
        print(f"ERROR: cluster {cluster.name!r} has no resolved release", file=sys.stderr)
        raise SystemExit(2)

    if cluster.image_delivery_mode == "connected":
        return {}
    return {key: getattr(release, attribute) for key, attribute in BUNDLE_KEYS.items()
            if getattr(release, attribute) in cluster.image_delivery_scopes
            and (key != "ciliumDay1" or cluster.network_type == "Cilium")}


def load_current_ref(sys_admin_root: Path, scope: str) -> tuple[dict, Path]:
    current = sys_admin_root / "mirror" / "bundles" / scope / "current.json"
    if not current.is_file():
        print(f"ERROR: missing promoted bundle ref {current}", file=sys.stderr)
        raise SystemExit(1)
    try:
        data = json.loads(current.read_text())
    except json.JSONDecodeError as exc:
        print(f"ERROR: invalid JSON in {current}: {exc}", file=sys.stderr)
        raise SystemExit(1)

    for field in ("scope", "specHash", "contentHash", "version", "path"):
        if not data.get(field):
            print(f"ERROR: {current} missing {field!r}", file=sys.stderr)
            raise SystemExit(1)
    if data["scope"] != scope:
        print(f"ERROR: {current} scope {data['scope']!r} does not match {scope!r}", file=sys.stderr)
        raise SystemExit(1)

    bundle_root = sys_admin_root / data["path"]
    if not bundle_root.is_dir():
        print(f"ERROR: promoted bundle path does not exist: {bundle_root}", file=sys.stderr)
        raise SystemExit(1)
    if not (bundle_root / "bundle.yaml").is_file():
        print(f"ERROR: promoted bundle metadata missing: {bundle_root / 'bundle.yaml'}", file=sys.stderr)
        raise SystemExit(1)
    if not any((bundle_root / "manifests").glob("*.yaml")):
        print(f"ERROR: promoted bundle has no manifests: {bundle_root / 'manifests'}", file=sys.stderr)
        raise SystemExit(1)
    bundle = yaml.safe_load((bundle_root / "bundle.yaml").read_text())
    meta = bundle.get("metadata", {})
    if meta.get("name") != scope or any(not re.fullmatch(r"[0-9a-f]{64}", str(data[key])) for key in ("specHash", "contentHash")):
        raise ValueError(f"{current}: bundle name/hash fields are invalid")
    if any(meta.get(key) != data[key] for key in ("scope", "version", "specHash", "contentHash")):
        raise ValueError(f"{current}: current ref and bundle metadata disagree")
    digest = hashlib.sha256()
    for path in sorted(p for p in (bundle_root / "manifests").rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(bundle_root / "manifests")).encode())
        digest.update(b"\0"); digest.update(path.read_bytes()); digest.update(b"\0")
    if digest.hexdigest() != data["contentHash"]:
        raise ValueError(f"{bundle_root}: bundle manifest contentHash mismatch")
    return data, bundle_root


def copy_ai_pod_bundle(bundle_root: Path, output_root: Path, cluster_name: str, scope: str) -> None:
    destination = output_root / "openshift" / cluster_name / "image-policy-bundles" / scope
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(bundle_root, destination)


def validate_ai_pod_bundle_output(output_root: Path, cluster_name: str, scope: str) -> None:
    bundle_root = output_root / "openshift" / cluster_name / "image-policy-bundles" / scope
    if not (bundle_root / "bundle.yaml").is_file():
        print(f"ERROR: copied bundle metadata missing: {bundle_root / 'bundle.yaml'}", file=sys.stderr)
        raise SystemExit(1)
    manifests_dir = bundle_root / "manifests"
    if not manifests_dir.is_dir() or not any(manifests_dir.glob("*.yaml")):
        print(f"ERROR: copied bundle has no manifests: {manifests_dir}", file=sys.stderr)
        raise SystemExit(1)


def write_bundle_payloads(output_root: Path | None, cluster_name: str, bundle_roots: dict[str, Path]) -> None:
    if output_root is None:
        return
    for scope in ("ocp-install-core", "cilium-day1", "gitops-bootstrap"):
        bundle_root = bundle_roots.get(scope)
        if bundle_root is None:
            continue
        copy_ai_pod_bundle(bundle_root, output_root, cluster_name, scope)
        validate_ai_pod_bundle_output(output_root, cluster_name, scope)


def load_bundle_manifests(bundle_root: Path, kinds: set[str]) -> dict[str, dict]:
    manifests: dict[str, dict] = {}
    for path in sorted((bundle_root / "manifests").glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not isinstance(doc, dict) or doc.get("kind") not in kinds:
                continue
            name = (doc.get("metadata", {}) or {}).get("name")
            if name:
                manifests[f"{doc['kind']}-{name}"] = doc
    return manifests


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate sys-admin image-policy bundle refs for a SAIF cluster release")
    parser.add_argument("--root", type=Path, default=None, help="platform root")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--sys-admin-root", type=Path)
    parser.add_argument("--output", type=Path, help="Optional YAML contract output path")
    parser.add_argument(
        "--ai-pod-output-root",
        type=Path,
        help="Optional generated OpenShift data root to populate with selected bundle payloads",
    )
    args = parser.parse_args()

    root = args.root
    if root is None:
        candidate = Path(__file__).resolve().parent.parent
        if has_contract_inputs(candidate):
            root = candidate
        elif has_contract_inputs(Path.cwd()):
            root = Path.cwd()
        else:
            print("ERROR: Cannot find platform root. Use --root.", file=sys.stderr)
            raise SystemExit(2)

    cluster = selected_cluster(root, args.cluster)
    if required_scopes(cluster) and args.sys_admin_root is None:
        parser.error("mirrored delivery requires --sys-admin-root with actual promoted receipts")
    refs = {}
    bundle_roots = {}
    for key, scope in required_scopes(cluster).items():
        current, bundle_root = load_current_ref(args.sys_admin_root, scope)
        bundle = yaml.safe_load((bundle_root / "bundle.yaml").read_text())
        if bundle.get("spec", {}).get("registry", "").rstrip("/") != registry_url(root).rstrip("/"):
            raise ValueError(f"{scope}: receipt registry differs from declared spec.registry.url")
        required_images = bundle.get("spec", {}).get("requiredImages", [])
        if key == "ocpInstallCore":
            expected = f"platform:stable-{cluster.release.ocp_minor}:{cluster.ocp_version}:{cluster.ocp_version}"
            if expected not in required_images:
                raise ValueError(f"{scope}: selected exact OpenShift release {expected} absent from receipt")
        if key == "ciliumDay1" and any(image not in required_images for image in (
                cluster.release.cilium_image, cluster.release.cilium_operator_image, cluster.release.clife_image)):
            raise ValueError(f"{scope}: selected exact Cilium image closure absent from receipt")
        bundle_roots[scope] = bundle_root
        refs[key] = {
            "scope": scope,
            "version": current["version"],
            "specHash": current["specHash"],
            "contentHash": current["contentHash"],
            "path": current["path"],
        }
        if key == "platformDay2":
            refs[key]["profile"] = cluster.release.platform_day2_profile
            refs[key]["targetRevision"] = platform_day2_target_revision(cluster.release.platform_day2_profile)
            if current.get("profile") and current["profile"] != cluster.release.platform_day2_profile:
                print(f"ERROR: promoted platform-day2 profile {current['profile']!r} does not match selected profile {cluster.release.platform_day2_profile!r}", file=sys.stderr)
                raise SystemExit(1)

    write_bundle_payloads(args.ai_pod_output_root, args.cluster, bundle_roots)

    contract = {
        "apiVersion": api_label("v1alpha1"),
        "kind": "ImagePolicyBundleContract",
        "metadata": {"name": args.cluster},
        "spec": {
            "releaseRef": cluster.release_ref,
            "ocpVersion": cluster.ocp_version,
            "networkType": cluster.network_type,
            "imageDelivery": {"mode": cluster.image_delivery_mode, "scopes": cluster.image_delivery_scopes},
            "bundles": refs,
        },
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(contract, sort_keys=False))
    if refs:
        print(f"VALID: {args.cluster} selected image-policy bundle refs: {', '.join(sorted(refs))}")
    else:
        print(f"VALID: {args.cluster} connected delivery selects no mirrored scopes; verify raw artifact hashes separately")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
