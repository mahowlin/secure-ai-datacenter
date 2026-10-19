#!/usr/bin/env python3
"""Write a checksum manifest for SAIF generated target-repo artifacts.

ACI and AI-POD generated artifacts are transported under data/. GitOps artifacts
are transported to the repository root because ArgoCD consumes clusters/ and apps/
paths directly.
"""

from site_identity import load_identity

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

from resolver import resolve


def platform_day2_target_revision(profile: str) -> str:
    return f"0.0.0-platform-day2.{profile}"


def has_contract_inputs(root: Path) -> bool:
    return (root / "data" / "networks").is_dir() or (root / "networks").is_dir()


def repo_roots(identity: dict) -> dict[str, dict[str, str]]:
    aci = identity["generatedRepos"]["aci"]
    openshift = identity["generatedRepos"]["openshift"]
    gitops = identity["generatedRepos"]["gitops"]
    return {
        aci: {
            "source_root": f"generated/repos/{aci}/data",
            "target_root": "data",
        },
        openshift: {
            "source_root": f"generated/repos/{openshift}/data",
            "target_root": "data",
        },
        gitops: {
            "source_root": f"generated/repos/{gitops}",
            "target_root": ".",
        },
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


CONTRACT_INPUTS = (
    "platform-release.yaml",
    "data/networks/",
    "data/clusters/",
    "data/defaults/",
    "data/traffic/",
    "data/cilium-config-profiles/",
    "data/package-inputs/",
    "data/package-contracts/",
    "data/gitops/",
    "schemas/cilium-config-profile-v1.schema.json",
    "schemas/gitops-exception-register-v1.schema.json",
)


def git_commit(root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"


def collect_files(root: Path, data_root: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    if not data_root.exists():
        return files
    for path in sorted(data_root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(data_root).as_posix()
        files.append({"path": rel, "sha256": sha256_file(path)})
    return files


def release_contracts(root: Path) -> list[dict]:
    platform = resolve(root)
    contracts = []
    for cluster in sorted(platform.clusters, key=lambda item: item.name):
        if not cluster.release:
            continue
        bundles = {
            "ocpInstallCore": cluster.release.ocp_install_core_bundle,
            "gitopsBootstrap": cluster.release.gitops_bootstrap_bundle,
            "platformDay2": {
                "profile": cluster.release.platform_day2_profile,
                "targetRevision": platform_day2_target_revision(cluster.release.platform_day2_profile),
            },
        }
        if cluster.network_type == "Cilium":
            bundles["ciliumDay1"] = cluster.release.cilium_day1_bundle
        contracts.append({
            "cluster": cluster.name,
            "releaseRef": cluster.release_ref,
            "currentReleaseRef": cluster.current_release_ref,
            "targetReleaseRef": cluster.target_release_ref,
            "ocpVersion": cluster.ocp_version,
            "networkType": cluster.network_type,
            "imagePolicyBundles": bundles,
        })
    return contracts


def main() -> None:
    parser = argparse.ArgumentParser(description="Write SAIF generated artifact manifest")
    parser.add_argument("--root", type=Path, default=None, help="platform root")
    parser.add_argument("--output", type=Path, default=None, help="manifest path")
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
            sys.exit(2)

    output = args.output or (root / "generated" / "artifact-manifest.yaml")
    identity = load_identity(root)

    targets = []
    for repo, config in repo_roots(identity).items():
        source_root = root / config["source_root"]
        files = collect_files(root, source_root)
        targets.append({
            "repo": repo,
            "sourceRoot": config["source_root"],
            "targetRoot": config["target_root"],
            "files": files,
        })

    manifest = {
        "apiVersion": f"{identity['apiGroup']}/v1alpha1",
        "kind": "GeneratedArtifactManifest",
        "metadata": {"name": "saif-generated-artifacts"},
        "source": {
            "repo": identity["sourceRepositories"]["platform"].rsplit("/", 1)[-1],
            "commit": git_commit(root),
            "contractInputs": list(CONTRACT_INPUTS),
            "releaseContracts": release_contracts(root),
            "rendererVersions": {
                "aci": "scripts/render-aci.py",
                "ucs": "scripts/render-ucs.py",
                "ocp": "scripts/render-ocp.py",
                "gitops": "scripts/render-gitops.py",
            },
        },
        "targets": targets,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(yaml.safe_dump(manifest, sort_keys=False))
    total = sum(len(t["files"]) for t in targets)
    print(f"Wrote {output} with {total} file checksums")


if __name__ == "__main__":
    main()
