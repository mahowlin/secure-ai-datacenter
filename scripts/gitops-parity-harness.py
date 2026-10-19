#!/usr/bin/env python3
"""Golden/parity harness for generated HCP GitOps output.

This intentionally does not refactor render-gitops.py or migrate paths. It renders the
current GitOps tree to a temporary directory, inventories generated files and Argo CD
Applications, runs Kustomize builds for generated HCP entrypoints/overlays, and compares
that evidence to a checked-in machine-readable baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

from hosted_cluster_paths import gitops_kustomize_prefixes




def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_yaml_documents(path: Path) -> list[Any]:
    try:
        return list(yaml.safe_load_all(path.read_text()))
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc


def app_role(app: dict[str, Any], rel_path: str) -> str:
    spec = app.get("spec", {}) if isinstance(app, dict) else {}
    source = spec.get("source", {}) if isinstance(spec, dict) else {}
    dest = spec.get("destination", {}) if isinstance(spec, dict) else {}
    metadata = app.get("metadata", {}) if isinstance(app, dict) else {}
    name = metadata.get("name", "")
    source_path = source.get("path") or source.get("chart") or ""
    platform_idms_app = (
        name == "platform-idms"
        or name.endswith("-platform-idms")
        or "platform-idms" in source_path
    )

    if dest.get("name"):
        if platform_idms_app:
            return "hosted-remote:platform-idms"
        if "guest-cni" in source_path:
            return "hosted-remote:guest-cni"
        return "hosted-remote:other"
    if dest.get("server") == "https://kubernetes.default.svc":
        if rel_path.startswith("clusters/foundation/"):
            return "base-local"
        if "parent-network" in source_path:
            return "parent-local:parent-network"
        if "hosted-clusters" in source_path:
            return "parent-local:hosted-cluster-lifecycle"
        if "operators" in source_path:
            return "parent-local:operators"
        if "operands" in source_path:
            return "parent-local:operands"
        if "tenants/" in source_path:
            return "parent-local:tenant-payload"
        if platform_idms_app:
            return "parent-local:platform-idms"
        return "parent-local:other"
    return "unknown"


def inventory(root: Path, cluster: str) -> dict[str, Any]:
    files: list[dict[str, str]] = []
    applications: list[dict[str, Any]] = []

    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        files.append({"path": rel, "sha256": sha256(path)})
        if path.suffix not in {".yaml", ".yml"}:
            continue
        if rel.startswith("charts/") and "{{" in path.read_text():
            # Helm templates are source files, not YAML documents until Helm
            # evaluates them. Keep their hashes in parity evidence but do not
            # parse them as Kubernetes resources here.
            continue
        for doc in load_yaml_documents(path):
            if not isinstance(doc, dict) or doc.get("kind") != "Application":
                continue
            metadata = doc.get("metadata", {}) or {}
            spec = doc.get("spec", {}) or {}
            source = spec.get("source", {}) or {}
            destination = spec.get("destination", {}) or {}
            applications.append({
                "file": rel,
                "name": metadata.get("name", ""),
                "source_path": source.get("path"),
                "source_chart": source.get("chart"),
                "destination_server": destination.get("server"),
                "destination_name": destination.get("name"),
                "destination_namespace": destination.get("namespace"),
                "sync_wave": str((metadata.get("annotations", {}) or {}).get("argocd.argoproj.io/sync-wave", "0")),
                "role": app_role(doc, rel),
            })

    return {
        "cluster": cluster,
        "file_count": len(files),
        "application_count": len(applications),
        "files": files,
        "applications": sorted(applications, key=lambda item: (item["file"], item["name"])),
    }


def generated_kustomize_dirs(root: Path) -> list[str]:
    prefixes = gitops_kustomize_prefixes(Path(__file__).resolve().parents[1])
    dirs = []
    for path in sorted(root.rglob("kustomization.yaml")):
        rel = path.parent.relative_to(root).as_posix()
        if any(f"{rel}/".startswith(prefix) for prefix in prefixes):
            dirs.append(rel)
    return dirs


def normalize_stderr(stderr: str) -> str:
    """Remove run-specific temporary paths from Kustomize diagnostics."""
    return re.sub(r"/[^\s'\"]*/gitops-parity-[^\s'\"]+/rendered", "<rendered>", stderr.strip())


def run_kustomize(root: Path, dirs: list[str]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for rel in dirs:
        proc = run(["kustomize", "build", rel], cwd=root)
        results.append({
            "path": rel,
            "ok": proc.returncode == 0,
            "stdout_sha256": hashlib.sha256(proc.stdout.encode()).hexdigest() if proc.returncode == 0 else None,
            "stderr": normalize_stderr(proc.stderr),
        })
    return results


def compare(expected: Any, actual: Any) -> list[str]:
    if expected == actual:
        return []
    expected_json = json.dumps(expected, indent=2, sort_keys=True).splitlines()
    actual_json = json.dumps(actual, indent=2, sort_keys=True).splitlines()
    # Keep diagnostics concise; developers can rerun with --update after review.
    errors = ["generated GitOps inventory differs from golden baseline"]
    if len(expected_json) != len(actual_json):
        errors.append(f"line count differs: expected {len(expected_json)} json lines, got {len(actual_json)}")
    for index, (left, right) in enumerate(zip(expected_json, actual_json), start=1):
        if left != right:
            errors.append(f"first difference at json line {index}: expected {left!r}, got {right!r}")
            break
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Render and verify HCP GitOps golden parity")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--cluster", default=None)
    parser.add_argument("--baseline", type=Path, default=None)
    parser.add_argument("--update", action="store_true", help="replace the golden baseline with current rendered evidence")
    parser.add_argument("--keep-render", type=Path, default=None, help="copy rendered tree here for inspection")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.cluster is None:
        from resolver import hosted_control_plane_name
        args.cluster = hosted_control_plane_name(root)
    if args.baseline is None:
        args.baseline = Path("tests/golden/gitops") / args.cluster / "inventory.json"
    baseline = args.baseline if args.baseline.is_absolute() else root / args.baseline
    renderer = root / "scripts" / "render-gitops.py"

    with tempfile.TemporaryDirectory(prefix="gitops-parity-") as tmp:
        render_root = Path(tmp) / "rendered"
        proc = run([sys.executable, str(renderer), "--root", str(root), "--cluster", args.cluster, "--output", str(render_root)], cwd=root)
        if proc.returncode != 0:
            print(proc.stdout, end="")
            print(proc.stderr, end="", file=sys.stderr)
            sys.exit(proc.returncode)

        evidence = inventory(render_root, args.cluster)
        kustomize_dirs = generated_kustomize_dirs(render_root)
        kustomize_results = run_kustomize(render_root, kustomize_dirs)
        evidence["kustomize"] = {
            "paths": kustomize_dirs,
            "results": kustomize_results,
            "failed_paths": [item["path"] for item in kustomize_results if not item["ok"]],
        }

        if args.keep_render:
            if args.keep_render.exists():
                shutil.rmtree(args.keep_render)
            shutil.copytree(render_root, args.keep_render)

        if args.update:
            baseline.parent.mkdir(parents=True, exist_ok=True)
            baseline.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
            print(f"updated {baseline}")
            return

        if not baseline.is_file():
            print(f"ERROR: baseline not found: {baseline}; run with --update after reviewing current output", file=sys.stderr)
            sys.exit(1)

        expected = json.loads(baseline.read_text())
        errors = compare(expected, evidence)
        if errors:
            for error in errors:
                print(f"ERROR: {error}", file=sys.stderr)
            sys.exit(1)

        print(
            f"VALID: {args.cluster} GitOps parity matches {baseline} "
            f"({evidence['file_count']} files, {evidence['application_count']} Applications, "
            f"{len(kustomize_dirs)} kustomize builds)"
        )


if __name__ == "__main__":
    main()
