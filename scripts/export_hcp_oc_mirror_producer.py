#!/usr/bin/env python3
"""Export the non-secret site-registry bootstrap input from the selected inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml


def producer_manifest(root: Path, cluster: str) -> dict:
    source = (root / "data/clusters" / cluster / "local-registry.yaml").resolve()
    if not source.is_relative_to(root.resolve()):
        raise ValueError("registry input is outside selected root")
    policy = yaml.safe_load(source.read_text())["spec"]["ocMirror"]
    # Reuse the renderer's pinned CSV/binding and full plugin-closure checks.
    # A standalone producer export must not accept inputs the target rejects.
    from resolver import resolve
    from gitops_renderer.oc_mirror_trial import render_oc_mirror_trial
    platform = resolve(root)
    selected = next(item for item in platform.clusters if item.name == cluster)
    render_oc_mirror_trial(selected, platform.defaults)
    if policy.get("publish") is not True:
        raise ValueError("selected operator integration is not enabled")
    release = policy["release"]
    images = release["bootstrapImages"]
    registries = set()
    roles = set()
    for image in images:
        role, source, destination, digest = (image[key] for key in ("role", "source", "destination", "digest"))
        registry, destination_path = destination.split("/", 1)
        source_repo, source_digest = source.rsplit("@", 1)
        if role in roles or source_digest != digest or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError("operator bootstrap roles must be unique and digest pinned")
        if destination_path.rsplit(":", 1)[0] != source_repo:
            raise ValueError("operator bootstrap destination must preserve upstream repository")
        roles.add(role)
        registries.add(registry)
    if len(registries) != 1 or not {"controller", "manager", "worker", "bundle", "catalog"} <= roles:
        raise ValueError("operator bootstrap closure or preceding registry is incomplete")
    trust = policy["producerTrust"]
    if trust["mode"] not in ("system", "bundle"):
        raise ValueError("producerTrust.mode must be system or bundle")
    trust = dict(trust)
    if trust["mode"] == "bundle":
        ca_path = (root / trust["sourcePath"]).resolve()
        if not ca_path.is_relative_to(root.resolve()) or not ca_path.is_file():
            raise ValueError("site CA source is outside selected root or missing")
        trust = {"mode": "bundle", "caBundle": trust["producerPath"],
                 "caSha256": hashlib.sha256(ca_path.read_bytes()).hexdigest()}
    return {"schemaVersion": "hcp-oc-mirror-producer/v3",
            "release": {"version": release["version"], "commit": release["commit"]},
            "destinationRegistry": registries.pop(),
            "trust": trust,
            "images": images,
            "installation": {"method": policy["installation"]["method"],
                             "package": policy["installation"]["package"],
                             "channel": policy["installation"]["channel"],
                             "startingCSV": policy["installation"]["startingCSV"]}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    data = json.dumps(producer_manifest(args.root, args.cluster), indent=2) + "\n"
    if args.output:
        args.output.write_text(data)
    else:
        print(data, end="")


if __name__ == "__main__":
    main()
