#!/usr/bin/env python3
"""Compare SAIF-generated OpenShift data with target repo OpenShift data."""

from __future__ import annotations

import argparse
import difflib
import filecmp
import sys
from pathlib import Path


DEFAULT_FILES = ("metadata.yaml", "install-config.yaml", "agent-config.yaml")


def read_lines(path: Path) -> list[str]:
    return path.read_text().splitlines(keepends=True)


def file_diff(source: Path, target: Path) -> list[str]:
    if source.exists() and target.exists() and filecmp.cmp(source, target, shallow=False):
        return []
    if not source.exists():
        return [f"missing generated source file: {source}\n"]
    if not target.exists():
        return [f"missing target repo file: {target}\n"]
    return list(difflib.unified_diff(
        read_lines(source),
        read_lines(target),
        fromfile=str(source),
        tofile=str(target),
    ))


def selected_files(source_cluster: Path) -> list[Path]:
    paths = [source_cluster / name for name in DEFAULT_FILES]
    for path in sorted(source_cluster.rglob("*")):
        if path.is_file() and path.name not in DEFAULT_FILES:
            paths.append(path)
    return paths


def compare_cluster(source_cluster: Path, target_cluster: Path) -> list[str]:
    errors: list[str] = []
    if not source_cluster.is_dir():
        return [f"generated OpenShift cluster directory not found: {source_cluster}\n"]
    if not target_cluster.is_dir():
        return [f"target OpenShift cluster directory not found: {target_cluster}\n"]

    source_rel_paths = {path.relative_to(source_cluster) for path in selected_files(source_cluster)}
    target_rel_paths = {path.relative_to(target_cluster) for path in target_cluster.rglob("*") if path.is_file()}

    for rel_path in sorted(source_rel_paths | target_rel_paths):
        diff = file_diff(source_cluster / rel_path, target_cluster / rel_path)
        if diff:
            errors.append(f"drift detected for {rel_path.as_posix()}:\n")
            errors.extend(diff)
            if not diff[-1].endswith("\n"):
                errors.append("\n")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate generated OpenShift data matches target repo data")
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--generated-root", type=Path, required=True, help="Path to the generated OpenShift data directory")
    parser.add_argument("--target-root", type=Path, required=True, help="Path to target repo data/openshift or target repo root")
    args = parser.parse_args()

    generated_root = args.generated_root
    if (generated_root / "openshift").is_dir():
        generated_root = generated_root / "openshift"

    target_root = args.target_root
    if (target_root / "data" / "openshift").is_dir():
        target_root = target_root / "data" / "openshift"
    elif (target_root / "openshift").is_dir():
        target_root = target_root / "openshift"

    errors = compare_cluster(generated_root / args.cluster, target_root / args.cluster)
    if errors:
        print(f"INVALID: generated OpenShift output drifts from target repo data for {args.cluster}", file=sys.stderr)
        print("", file=sys.stderr)
        print("".join(errors), file=sys.stderr)
        sys.exit(1)

    print(f"VALID: generated OpenShift output matches target repo data for {args.cluster}")


if __name__ == "__main__":
    main()
