#!/usr/bin/env python3
"""Copy SAIF generated repo-shaped artifacts into target repositories.

ACI and AI-POD generated artifacts are transported under data/. GitOps artifacts
are transported to repository root because ArgoCD consumes clusters/ and apps/
paths directly.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from site_identity import load_identity


_IDENTITY = load_identity()
ACI_REPO = _IDENTITY["generatedRepos"]["aci"]
OPENSHIFT_REPO = _IDENTITY["generatedRepos"]["openshift"]
GITOPS_REPO = _IDENTITY["generatedRepos"]["gitops"]

TARGET_REPOS = {
    ACI_REPO: _IDENTITY["sourceRepositories"]["aci"],
    OPENSHIFT_REPO: _IDENTITY["sourceRepositories"]["openshift"],
    GITOPS_REPO: _IDENTITY["sourceRepositories"]["gitops"],
}

REPO_SOURCE_SUBDIRS = {
    ACI_REPO: "data",
    OPENSHIFT_REPO: "data",
    GITOPS_REPO: ".",
}

PROTECTED_TARGET_GLOBS = {
    GITOPS_REPO: [
        "apps/hcp/management/hosted-cluster-lifecycle/overlays/*/*/registry-ca-bundle.yaml",
        "apps/hcp/management/hosted-cluster-lifecycle/overlays/*/bootstrap/secure/*.yaml",
    ],
}


def protected_target_files(repo: str, target_root: Path) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for pattern in PROTECTED_TARGET_GLOBS.get(repo, []):
        for path in sorted(target_root.glob(pattern)):
            if path.is_file():
                files[path.relative_to(target_root).as_posix()] = path.read_bytes()
    return files


def restore_protected_target_files(target_root: Path, files: dict[str, bytes]) -> None:
    for rel_path, content in files.items():
        path = target_root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def validate_protected_target_files(repo: str, source_root: Path, files: dict[str, bytes]) -> None:
    if repo != GITOPS_REPO:
        return
    errors: list[str] = []
    for kustomization_path in sorted(source_root.glob("apps/hcp/management/hosted-cluster-lifecycle/overlays/*/*/kustomization.yaml")):
        text = kustomization_path.read_text()
        if "registry-ca-bundle.yaml" not in text:
            continue
        rel_path = (kustomization_path.parent / "registry-ca-bundle.yaml").relative_to(source_root).as_posix()
        content = files.get(rel_path)
        if content is None:
            errors.append(f"missing target-owned protected file {rel_path}")
            continue
        if b"REGISTRY_CA_PLACEHOLDER" in content:
            errors.append(f"target-owned protected file still contains placeholder: {rel_path}")
        if b"BEGIN CERTIFICATE" not in content or b"END CERTIFICATE" not in content:
            errors.append(f"target-owned protected file must contain PEM certificate data: {rel_path}")
    if errors:
        print("ERROR: refusing transport because protected target-owned GitOps files are not ready", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        raise SystemExit(1)


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> str:
    result = subprocess.run(cmd, cwd=cwd, env=env, text=True, capture_output=True)
    if result.returncode != 0:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.stdout.strip()


def token() -> str:
    value = os.environ.get("WORKFLOW_TRIGGER_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not value:
        print("ERROR: WORKFLOW_TRIGGER_TOKEN, GH_TOKEN, or GITHUB_TOKEN must be set", file=sys.stderr)
        raise SystemExit(2)
    return value


def validate_openshift_drift(repo: str, source_repo_root: Path, checkout: Path, cluster: str | None) -> None:
    if repo != OPENSHIFT_REPO or not cluster:
        return
    script = Path(__file__).resolve().parent / "validate-openshift-drift.py"
    run([
        sys.executable,
        str(script),
        "--cluster",
        cluster,
        "--generated-root",
        str(source_repo_root),
        "--target-root",
        str(checkout),
    ])


def manifest_inventory(manifest: dict, repo: str) -> tuple[str, dict[str, str]]:
    targets = [item for item in manifest.get("targets", []) if item.get("repo") == repo]
    if len(targets) != 1:
        raise ValueError(f"expected one manifest target for {repo}")
    target = targets[0]
    target_root = target.get("targetRoot", "data")
    if target_root not in (".", "data"):
        raise ValueError("unsupported manifest target root")
    files = {}
    for item in target.get("files", []):
        path, digest = item["path"], item["sha256"]
        if (not path or Path(path).is_absolute() or ".." in Path(path).parts
                or path != Path(path).as_posix() or path in files
                or ".git" in Path(path).parts or path == "artifact-manifest.yaml"
                or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            raise ValueError("invalid or duplicate manifest file identity")
        files[path] = digest
    if not files:
        raise ValueError("refusing an empty generated target inventory")
    return target_root, files


def owned_path(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"generated path escapes its owner: {relative}")
    return path


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile_generated_files(repo: str, source_repo_root: Path,
                              manifest: Path, checkout: Path) -> list[str]:
    """Copy the complete checksum inventory, pruning only prior manifest owners.

    Preflight every source, retired file and collision before changing payloads.
    Independent files and runtime-owned material are never swept by directory.
    """
    document = yaml.safe_load(manifest.read_text())
    target_subdir, expected = manifest_inventory(document, repo)
    source_root = source_repo_root / target_subdir
    target_root = checkout / target_subdir
    protected = protected_target_files(repo, target_root)
    validate_protected_target_files(repo, source_root, protected)
    previous = {}
    previous_path = checkout / "artifact-manifest.yaml"
    if previous_path.is_file():
        old = yaml.safe_load(previous_path.read_text())
        if any(item.get("repo") == repo for item in old.get("targets", [])):
            old_root, previous = manifest_inventory(old, repo)
            if old_root != target_subdir:
                raise ValueError("generated target root changed; owner migration required")
    for relative, digest in expected.items():
        source = owned_path(source_root, relative)
        target = owned_path(target_root, relative)
        if not source.is_file() or file_digest(source) != digest:
            raise ValueError(f"source manifest mismatch: {relative}")
        if relative in protected:
            raise ValueError(f"manifest overlaps target runtime owner: {relative}")
        if target.exists() and not target.is_file():
            raise ValueError(f"generated target is not a regular file: {relative}")
        if (target.is_file() and relative not in previous and file_digest(target) != digest
                and not target.read_bytes().startswith(b"# Generated by ")):
            raise ValueError(f"unclaimed target collision; owner review required: {relative}")
        if (relative in previous and target.is_file()
                and file_digest(target) not in (previous[relative], digest)):
            raise ValueError(f"target changed outside generated owner: {relative}")
    retired = set(previous) - set(expected)
    for relative in retired:
        target = owned_path(target_root, relative)
        if relative in protected or (target.exists() and
                (not target.is_file() or file_digest(target) != previous[relative])):
            raise ValueError(f"retired generated path has another owner/change: {relative}")
    tracked = ["artifact-manifest.yaml"]
    for relative in sorted(retired):
        target = owned_path(target_root, relative)
        if target.is_file():
            target.unlink()
            tracked.append((Path(target_subdir) / relative).as_posix())
    for relative in expected:
        target = owned_path(target_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_root / relative, target)
        tracked.append((Path(target_subdir) / relative).as_posix())
    # Check all advertised bytes before creating a commit or copying its manifest.
    for relative, digest in expected.items():
        if file_digest(target_root / relative) != digest:
            raise ValueError(f"transported manifest mismatch: {relative}")
    shutil.copy2(manifest, previous_path)
    return tracked


def sync_repo(repo: str, source_repo_root: Path, manifest: Path, target_ref: str, workdir: Path, token_value: str, commit_message: str, cluster: str | None) -> None:
    remote = TARGET_REPOS.get(repo)
    if remote is None:
        print(f"Skipping unknown target repo {repo!r}")
        return

    source_subdir = REPO_SOURCE_SUBDIRS.get(repo, "data")
    source_root = source_repo_root if source_subdir == "." else source_repo_root / source_subdir
    if not source_root.is_dir():
        raise ValueError(f"missing selected generated source directory: {source_root}")

    checkout = workdir / repo
    # Runtime authorization stays in the child process environment, never argv,
    # persisted origin, transport receipts or generated files.
    git_env = os.environ.copy()
    index = int(git_env.get("GIT_CONFIG_COUNT", "0"))
    git_env["GIT_CONFIG_COUNT"] = str(index + 1)
    git_env[f"GIT_CONFIG_KEY_{index}"] = "http.https://github.com/.extraheader"
    encoded = base64.b64encode(("x-access-token:" + token_value).encode()).decode()
    git_env[f"GIT_CONFIG_VALUE_{index}"] = "AUTHORIZATION: basic " + encoded
    url = "https://github.com/" + remote + ".git"
    run(["git", "clone", "--branch", target_ref, "--single-branch", url, str(checkout)], env=git_env)

    tracked_paths = reconcile_generated_files(repo, source_repo_root, manifest, checkout)

    validate_openshift_drift(repo, source_root, checkout, cluster)

    status = run(["git", "status", "--porcelain", "--", *tracked_paths], cwd=checkout)
    if not status:
        print(f"{repo}: no transport changes")
        return

    run(["git", "add", *tracked_paths], cwd=checkout)
    run(["git", "commit", "-m", commit_message], cwd=checkout)
    run(["git", "push", "origin", f"HEAD:{target_ref}"], cwd=checkout, env=git_env)
    print(f"{repo}: transported generated data to {target_ref}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Transport SAIF generated artifacts into target repos")
    parser.add_argument("--source-root", type=Path, required=True, help="generated/repos root")
    parser.add_argument("--manifest", type=Path, required=True, help="generated artifact manifest")
    parser.add_argument("--target-ref", required=True, help="target branch to update; tags and SHAs are not valid because transport commits generated data")
    parser.add_argument("--workdir", type=Path, default=Path(".transport-targets"))
    parser.add_argument("--repo", action="append", choices=sorted(TARGET_REPOS), help="target repo to sync; defaults to all present repos")
    parser.add_argument("--commit-message", default="Sync SAIF generated data")
    parser.add_argument("--cluster", help="OpenShift cluster name for drift validation")
    args = parser.parse_args()

    if not args.source_root.is_dir():
        print(f"ERROR: source root not found: {args.source_root}", file=sys.stderr)
        raise SystemExit(2)
    if not args.manifest.is_file():
        print(f"ERROR: manifest not found: {args.manifest}", file=sys.stderr)
        raise SystemExit(2)

    run(["git", "config", "--global", "user.name", "github-actions[bot]"])
    run(["git", "config", "--global", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"])

    args.workdir.mkdir(parents=True, exist_ok=True)
    token_value = token()
    repos = args.repo or [item["repo"] for item in yaml.safe_load(args.manifest.read_text()).get("targets", [])]
    if not repos or any(repo not in TARGET_REPOS for repo in repos):
        raise ValueError("manifest must select known generated target repositories")
    if GITOPS_REPO in repos:
        run([sys.executable, str(Path(__file__).with_name("verify-portworx-csi-tag.py")),
             "--generated-root", str(args.source_root / GITOPS_REPO)])
    for repo in repos:
        sync_repo(repo, args.source_root / repo, args.manifest, args.target_ref, args.workdir, token_value, args.commit_message, args.cluster)


if __name__ == "__main__":
    main()
