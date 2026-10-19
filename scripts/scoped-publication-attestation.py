#!/usr/bin/env python3
"""Write or verify a scoped GitOps publication attestation.

The repository-wide artifact manifest remains the checksum authority. This
tool proves only the declared stage scope and rejects target diffs outside its
ordered allowlist; it never claims whole-repository parity.
"""

from __future__ import annotations
from site_identity import api_label

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

from storage_transition_composer import compose_root_bytes


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def run_git(root: Path, *args: str, binary: bool = False) -> str | bytes:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=not binary,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode() if binary else result.stderr
        raise ValueError(stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def committed_file(root: Path, commit: str, path: str) -> bytes | None:
    if Path(path).is_absolute() or ".." in Path(path).parts or path != Path(path).as_posix():
        raise ValueError("attested paths must be repository-relative files")
    listing = str(run_git(root, "ls-tree", "-z", commit, "--", path))
    if not listing:
        return None
    metadata, name = listing.rstrip("\0").split("\t", 1)
    mode, kind, _oid = metadata.split()
    if name != path or kind != "blob" or mode not in {"100644", "100755"}:
        raise ValueError(f"attested path is not a committed regular file: {path}")
    return bytes(run_git(root, "show", f"{commit}:{path}", binary=True))


def resolve_commit(root: Path, ref: str) -> str:
    return str(run_git(root, "rev-parse", "--verify", f"{ref}^{{commit}}")).strip()


def manifest_target(manifest: dict, repo: str) -> dict:
    target = next(
        (item for item in manifest.get("targets", []) if item.get("repo") == repo),
        None,
    )
    if target is None:
        raise ValueError(f"artifact manifest has no target repo {repo!r}")
    return target


def _allowlist_item(item: object) -> tuple[str, str, str | None, str | None]:
    if isinstance(item, str) and item:
        return item, "present", None, None
    if isinstance(item, dict):
        path = item.get("path")
        state = item.get("state", "present")
        activation = item.get("activation")
        source_path = item.get("sourcePath")
        if source_path is not None and (not isinstance(source_path, str) or not source_path):
            raise ValueError("publication sourcePath must be a nonempty path")
        if isinstance(path, str) and path and state in {"present", "absent"}:
            return path, state, None, source_path
        if (isinstance(path, str) and path and state == "composed"
                and activation in {"consumer-hold", "provider-cutover", "guest-cni-apps"}):
            return path, state, activation, source_path
    raise ValueError(
        "publication allowlist entries must be paths or supported path/state mappings"
    )


def stage_definition(stage_set: dict, name: str) -> tuple[dict, dict]:
    spec = stage_set.get("spec", {}) if isinstance(stage_set, dict) else {}
    stages = spec.get("stages", []) if isinstance(spec, dict) else []
    stage = next((item for item in stages if item.get("name") == name), None)
    if stage is None:
        raise ValueError(f"publication stage {name!r} not found")
    allowlist = stage.get("allowlist")
    if not isinstance(allowlist, list) or not allowlist:
        raise ValueError(f"publication stage {name!r} has no ordered allowlist")
    normalized = [_allowlist_item(item) for item in allowlist]
    paths = [path for path, _state, _activation, _source_path in normalized]
    if len(paths) != len(set(paths)):
        raise ValueError(f"publication stage {name!r} allowlist contains duplicates")
    stage = {**stage, "allowlist": normalized}
    return spec, stage


def allowlist_entries(manifest: dict, repo: str,
                      items: list[tuple[str, str, str | None, str | None]]) -> list[dict[str, object]]:
    target = manifest_target(manifest, repo)
    checksums = {item["path"]: item["sha256"] for item in target.get("files", [])}
    missing = [source_path or path for path, state, _activation, source_path in items
               if state in {"present", "composed"} and (source_path or path) not in checksums]
    if missing:
        raise ValueError(
            "publication allowlist path(s) absent from authoritative artifact manifest: "
            + ", ".join(missing)
        )
    unexpected = [path for path, state, _activation, _source_path in items
                  if state == "absent" and path in checksums]
    if unexpected:
        raise ValueError(
            "publication allowlist path(s) declared absent but present in authoritative artifact manifest: "
            + ", ".join(unexpected)
        )
    entries = []
    for path, state, activation, source_path in items:
        entry = ({"path": path, "state": "present", "sha256": checksums[source_path or path]}
                 if state == "present" else
                 {"path": path, "state": "composed", "activation": activation,
                  "canonicalSha256": checksums[source_path or path]}
                 if state == "composed" else {"path": path, "state": "absent"})
        if source_path:
            entry["sourcePath"] = source_path
        entries.append(entry)
    return entries


def allowlist_digest(entries: list[dict[str, str]]) -> str:
    payload = b"".join(
        str(entry["path"]).encode() + b"\0" + str(entry["state"]).encode() + b"\0"
        + str(entry.get("sha256", entry.get("canonicalSha256", ""))).encode() + b"\0"
        + str(entry.get("activation", "")).encode()
        + (b"\0" + str(entry["sourcePath"]).encode() if "sourcePath" in entry else b"")
        + (b"\0" + b"\0".join(path.encode() for path in entry["addedApplicationLeaves"])
           if "addedApplicationLeaves" in entry else b"")
        + b"\n"
        for entry in entries
    )
    return sha256_bytes(payload)


def compose_guest_cni_root(content: bytes, root_path: str,
                           application_leaves: list[str]) -> bytes:
    """Add declared Application leaves while preserving all existing root bytes."""
    marker = b"patches:\n"
    root_dir = Path(root_path).parent
    if (not application_leaves or len(application_leaves) != len(set(application_leaves))
            or any(Path(path).parent != root_dir or Path(path).suffix != ".yaml"
                   for path in application_leaves)):
        raise ValueError("guest CNI composition requires distinct sibling Application leaves")
    names = [Path(path).name for path in application_leaves]
    refs = b"".join(f"- {name}\n".encode() for name in names)
    if content.count(marker) != 1:
        raise ValueError("target root has unexpected guest CNI composition state")
    document = yaml.safe_load(content)
    if document.get("kind") != "Kustomization" or not isinstance(document.get("resources"), list):
        raise ValueError("guest CNI composition requires a Kustomization root")
    if any(name in document["resources"] for name in names):
        raise ValueError("target root has unexpected guest CNI composition state")
    return content.replace(marker, refs + marker, 1)


def target_diff(target_root: Path, base: str, head: str) -> tuple[list[dict[str, str]], str]:
    name_status = str(run_git(
        target_root, "diff", "--name-status", "--no-renames", base, head, "--"
    ))
    entries = []
    for line in name_status.splitlines():
        if not line:
            continue
        status, path = line.split("\t", 1)
        entries.append({"status": status, "path": path})
    patch = bytes(run_git(
        target_root,
        "diff",
        "--binary",
        "--full-index",
        "--no-renames",
        base,
        head,
        "--",
        binary=True,
    ))
    return entries, sha256_bytes(patch)


def build_attestation(
    manifest_path: Path,
    stage_set_path: Path,
    stage_name: str,
    target_root: Path,
    target_revision: str | None = None,
    target_head: str = "HEAD",
    target_mode: str = "offline-scratch",
) -> dict:
    manifest_bytes = manifest_path.read_bytes()
    manifest = yaml.safe_load(manifest_bytes)
    stage_set = yaml.safe_load(stage_set_path.read_text())
    spec, stage = stage_definition(stage_set, stage_name)
    repo = spec.get("targetRepo")
    if not isinstance(repo, str) or not repo:
        raise ValueError("publication stage set spec.targetRepo is required")

    configured_root = spec.get("targetBase")
    if not isinstance(configured_root, str) or not configured_root:
        raise ValueError("publication stage set spec.targetBase is required")
    selected_root = target_revision or configured_root
    resolved_reference = resolve_commit(target_root, configured_root)
    resolved_root = resolve_commit(target_root, selected_root)
    resolved_head = resolve_commit(target_root, target_head)

    source_commit = manifest.get("source", {}).get("commit")
    if not isinstance(source_commit, str) or not source_commit or source_commit == "unknown":
        raise ValueError("artifact manifest source.commit must bind a concrete commit")
    entries = allowlist_entries(manifest, repo, stage["allowlist"])
    allowed_paths = {entry["path"] for entry in entries}
    root_additions = stage.get("rootAdditions", {})
    composed_roots = {entry["path"] for entry in entries
                      if entry["state"] == "composed" and entry["activation"] == "guest-cni-apps"}
    if not isinstance(root_additions, dict) or set(root_additions) != composed_roots:
        raise ValueError("rootAdditions must exactly name composed guest CNI roots")
    for root_path, leaves in root_additions.items():
        if not isinstance(leaves, list) or not leaves or len(leaves) != len(set(leaves)):
            raise ValueError("rootAdditions must be a nonempty unique ordered leaf list")
        if any(not isinstance(leaf, str) or Path(leaf).parent != Path(root_path).parent
               or Path(leaf).suffix != ".yaml" for leaf in leaves):
            raise ValueError("root additions must be sibling YAML Application leaves")
        for leaf in leaves:
            selected = next((entry for entry in entries
                             if entry["path"] == leaf and entry["state"] == "present"), None)
            if selected is None:
                raise ValueError(f"root addition is not an attested Application leaf: {leaf}")
            leaf_content = committed_file(target_root, resolved_head, leaf)
            if leaf_content is None or (yaml.safe_load(leaf_content) or {}).get("kind") != "Application":
                raise ValueError(f"root addition is not an Application: {leaf}")
    diff_entries, patch_digest = target_diff(target_root, resolved_root, resolved_head)
    outside = [item["path"] for item in diff_entries if item["path"] not in allowed_paths]
    if outside:
        raise ValueError(
            "target diff contains path(s) outside scoped publication allowlist: "
            + ", ".join(outside)
        )

    retained_paths = stage.get("retainedTargetOnly", [])
    if not isinstance(retained_paths, list) or any(
        not isinstance(path, str) or not path for path in retained_paths
    ) or len(retained_paths) != len(set(retained_paths)):
        raise ValueError("retainedTargetOnly must be a unique path list")
    canonical_paths = {item["path"] for item in manifest_target(manifest, repo)["files"]}
    retained = []
    for path in retained_paths:
        if path in canonical_paths or path in allowed_paths:
            raise ValueError(f"retained target-only path overlaps candidate scope: {path}")
        base_content = bytes(run_git(target_root, "show", f"{resolved_root}:{path}", binary=True))
        head_content = bytes(run_git(target_root, "show", f"{resolved_head}:{path}", binary=True))
        if base_content != head_content:
            raise ValueError(f"retained target-only path changed: {path}")
        retained.append({"path": path, "sha256": sha256_bytes(head_content)})

    checksum_errors = []
    for entry in entries:
        content = committed_file(target_root, resolved_head, entry["path"])
        if entry["state"] == "absent":
            if content is not None:
                checksum_errors.append(f"expected absent {entry['path']}")
        elif content is None:
            checksum_errors.append(f"missing {entry['path']}")
        elif entry["state"] == "composed":
            base_content = bytes(run_git(
                target_root, "show", f"{resolved_root}:{entry['path']}", binary=True
            ))
            if entry["activation"] == "guest-cni-apps":
                entry["addedApplicationLeaves"] = root_additions[entry["path"]]
            expected = (compose_guest_cni_root(base_content, entry["path"],
                                               root_additions[entry["path"]])
                        if entry["activation"] == "guest-cni-apps"
                        else compose_root_bytes(base_content, str(entry["activation"])))
            actual = content
            entry["baseSha256"] = sha256_bytes(base_content)
            entry["sha256"] = sha256_bytes(actual)
            entry["preservesExistingOwners" if entry["activation"] == "guest-cni-apps"
                  else "preservesNonStorageOwners"] = True
            if actual != expected:
                checksum_errors.append(
                    f"composed storage root mismatch {entry['path']}: "
                    f"{sha256_bytes(actual)} != {sha256_bytes(expected)}"
                )
        else:
            actual = sha256_bytes(content)
            if actual != entry["sha256"]:
                checksum_errors.append(
                    f"checksum mismatch {entry['path']}: {actual} != {entry['sha256']}"
                )
    if checksum_errors:
        raise ValueError("; ".join(checksum_errors))

    diff_payload = b"".join(
        item["status"].encode() + b"\0" + item["path"].encode() + b"\n"
        for item in diff_entries
    )
    return {
        "apiVersion": api_label("v1alpha1"),
        "kind": "ScopedPublicationAttestation",
        "metadata": {"name": stage_name},
        "claim": {
            "type": "scoped-publication",
            "wholeRepositoryParity": False,
        },
        "source": {
            "base": spec.get("sourceBase", ""),
            "commit": source_commit,
            "fullManifest": {
                "path": manifest_path.as_posix(),
                "sha256": sha256_bytes(manifest_bytes),
            },
        },
        "scope": {
            "targetRepo": repo,
            "order": stage.get("order"),
            "purpose": stage.get("purpose", ""),
            "orderedAllowlist": entries,
            "allowlistDigest": allowlist_digest(entries),
            "retainedTargetOnly": retained,
        },
        "target": {
            "mode": target_mode,
            "baseline": resolved_reference,
            "base": resolved_root,
            "head": resolved_head,
            "diff": {
                "entries": diff_entries,
                "entriesDigest": sha256_bytes(diff_payload),
                "patchSha256": patch_digest,
            },
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--stage-set", type=Path, required=True)
    parser.add_argument("--stage", required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    parser.add_argument("--target-base", dest="target_revision")
    parser.add_argument("--target-head", default="HEAD")
    parser.add_argument(
        "--target-mode",
        choices=("offline-scratch", "review", "publication"),
        default="offline-scratch",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path, help="verify an existing attestation")
    args = parser.parse_args()

    try:
        expected = build_attestation(
            args.manifest,
            args.stage_set,
            args.stage,
            args.target_root,
            args.target_revision,
            args.target_head,
            args.target_mode,
        )
        if args.verify:
            actual = yaml.safe_load(args.verify.read_text())
            if actual != expected:
                raise ValueError(f"attestation does not match verified source and target state: {args.verify}")
            print(f"VALID: scoped publication attestation {args.verify}")
            return
        if not args.output:
            raise ValueError("--output is required when not using --verify")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(yaml.safe_dump(expected, sort_keys=False))
        print(f"Wrote scoped publication attestation {args.output}")
    except (OSError, ValueError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
