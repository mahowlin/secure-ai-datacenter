#!/usr/bin/env python3
"""Bind the HCP naming and worker activation reviews to exact Git trees."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
from pathlib import Path

import yaml

from site_identity import api_label


ROOT = Path(__file__).resolve().parents[1]


def default_review_input(root: Path) -> str:
    matches = sorted((root / "data" / "reviews").glob("*-name-transition.yaml"))
    if len(matches) != 1:
        raise ValueError("pass --review-input; data/reviews does not contain exactly one name-transition file")
    return matches[0].relative_to(root).as_posix()


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args])


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def content(repo: Path, ref: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", str(repo), "show", f"{ref}:{path}"], capture_output=True,
    )
    return result.stdout if result.returncode == 0 else None


def change_set(repo: Path, base: str, head: str) -> dict:
    base = git(repo, "rev-parse", f"{base}^{{commit}}").decode().strip()
    head = git(repo, "rev-parse", f"{head}^{{commit}}").decode().strip()
    paths = git(repo, "diff", "--name-only", "--no-renames", base, head).decode().splitlines()
    changes = []
    for path in paths:
        old, new = content(repo, base, path), content(repo, head, path)
        changes.append({
            "path": path,
            "before": digest(old) if old is not None else None,
            "after": digest(new) if new is not None else None,
        })
    return {
        "base": base,
        "head": head,
        "baseTree": git(repo, "rev-parse", f"{base}^{{tree}}").decode().strip(),
        "headTree": git(repo, "rev-parse", f"{head}^{{tree}}").decode().strip(),
        "changes": changes,
    }


def required_content(repo: Path, ref: str, path: str) -> bytes:
    raw = content(repo, ref, path)
    if raw is None:
        raise ValueError(f"missing {path} at {ref}")
    return raw


def nodepool_counts(repo: Path, ref: str, path_template: str,
                    names: list[str]) -> dict[str, int]:
    result = {}
    for name in names:
        raw = required_content(repo, ref, path_template.format(subject=name))
        result[name] = yaml.safe_load(raw)["spec"]["replicas"]
    return result


def app_mappings(repo: Path, old_ref: str, new_ref: str, review: dict) -> list[dict]:
    old_stage = review["targetPaths"]["oldStage"]
    new_stage = review["targetPaths"]["newStage"]
    changed = review["applicationNames"]
    old_index = yaml.safe_load(required_content(repo, old_ref, f"{old_stage}/kustomization.yaml"))
    new_index = yaml.safe_load(required_content(repo, new_ref, f"{new_stage}/kustomization.yaml"))
    if len(changed) != len(old_index["resources"]) or len(set(changed.values())) != len(changed):
        raise ValueError("review Application mapping must cover each old Application exactly once")
    result = []
    for old_file in old_index["resources"]:
        old = yaml.safe_load(required_content(repo, old_ref, f"{old_stage}/{old_file}"))
        old_name = old["metadata"]["name"]
        new_name = changed[old_name]
        new_file = f"{new_name}.yaml"
        if new_file not in new_index["resources"]:
            raise ValueError(f"Application mapping absent: {old_name} -> {new_name}")
        new = yaml.safe_load(required_content(repo, new_ref, f"{new_stage}/{new_file}"))
        if (new["metadata"]["name"] != new_name or
                old["metadata"].get("namespace") != new["metadata"].get("namespace") or
                old["spec"]["destination"] != new["spec"]["destination"]):
            raise ValueError(f"Application scope changed: {old_name} -> {new_name}")
        result.append({
            "old": old_name,
            "new": new_name,
            "oldSource": old["spec"]["source"].get("path", old["spec"]["source"].get("chart")),
            "newSource": new["spec"]["source"].get("path", new["spec"]["source"].get("chart")),
        })
    if len(result) != len(new_index["resources"]):
        raise ValueError("new staged Application count changed")
    return result


def build(args: argparse.Namespace) -> dict:
    source_root = args.source_root
    source = git(source_root, "rev-parse", f"{args.source_ref}^{{commit}}").decode().strip()
    review_raw = required_content(source_root, source, args.review_input)
    review = yaml.safe_load(review_raw)
    inputs = [{"path": args.review_input, "sha256": digest(review_raw)}]
    inputs.extend({"path": path, "sha256": digest(required_content(source_root, source, path))}
                  for path in review["sourceInputs"])
    management = yaml.safe_load(required_content(source_root, source, review["managementInput"]))
    subjects = management["spec"]["day2GitOps"]["hostedClusterLifecycle"]["deploymentSubjects"]
    names = [subjects[role] for role in review["selectedRoles"]]
    if len(set(names)) != len(names) or set(review["expectedWorkerCounts"]["nameAdoption"]) != set(names) or set(review["expectedWorkerCounts"]["workerActivation"]) != set(names):
        raise ValueError("review worker subjects differ from declared deployment subjects")
    stages = {
        "prepare": change_set(args.target_root, args.main, args.prepare),
        "nameAdoption": change_set(args.target_root, args.prepare, args.adoption),
        "nameInverse": change_set(args.target_root, args.adoption, args.name_inverse),
        "workerActivation": change_set(args.target_root, args.adoption, args.activation),
        "workerInverse": change_set(args.target_root, args.activation, args.worker_inverse),
    }
    if stages["nameInverse"]["headTree"] != stages["prepare"]["headTree"]:
        raise ValueError("naming inverse does not restore the prepare tree")
    if stages["workerInverse"]["headTree"] != stages["nameAdoption"]["headTree"]:
        raise ValueError("worker inverse does not restore the zero-worker adoption tree")
    path_template = review["targetPaths"]["nodePool"]
    zero = nodepool_counts(args.target_root, args.adoption, path_template, names)
    five = nodepool_counts(args.target_root, args.activation, path_template, names)
    if zero != review["expectedWorkerCounts"]["nameAdoption"]:
        raise ValueError(f"naming adoption changes declared worker counts: {zero}")
    if five != review["expectedWorkerCounts"]["workerActivation"]:
        raise ValueError(f"worker activation changes declared worker counts: {five}")
    return {
        "apiVersion": api_label("v1alpha1", args.source_root),
        "kind": "HCPDeploymentTransitionReview",
        "metadata": {"name": review["metadata"]["name"]},
        "source": {"implementationCommit": source, "inputs": inputs},
        "targetRepo": review["targetRepo"],
        "appMappings": app_mappings(args.target_root, args.main, args.adoption, review),
        "workerCounts": {"nameAdoption": zero, "workerActivation": five},
        "stages": stages,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--review-input", default=None)
    parser.add_argument("--target-root", type=Path, required=True)
    for field in ("source-ref", "main", "prepare", "adoption", "name-inverse",
                  "activation", "worker-inverse"):
        parser.add_argument(f"--{field}", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.review_input is None:
        args.review_input = default_review_input(args.source_root)
    packet = build(args)
    if args.verify:
        if yaml.safe_load(args.output.read_text()) != packet:
            raise SystemExit("HCP transition packet differs from exact source or target state")
        print(f"VALID: {args.output}")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(packet, sort_keys=False))
        print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
