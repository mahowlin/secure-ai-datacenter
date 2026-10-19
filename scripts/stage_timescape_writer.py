#!/usr/bin/env python3
"""Compose one reviewed hosted writer artifact with its source-owned child App.

Local only: preserves retained target composition and refuses enrollment until
an encrypted artifact and independently verified identity receipt are present.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
from pathlib import Path
import yaml

KEYS = {"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"}
NAME = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")


def compose(source: Path, target: Path, artifact: Path, cluster: str,
            verified_identity: dict) -> list[str]:
    intent_path = Path("apps/sealed-secrets/hosted-writer-intent.json")
    manifest = json.loads((source / intent_path).read_text())
    if json.loads((target / intent_path).read_text()) != manifest:
        raise ValueError("target writer intent differs from source")
    if not NAME.fullmatch(cluster):
        raise ValueError("invalid cluster")
    rows = [row for row in manifest["targets"] if row["cluster"] == cluster]
    selected = [row for row in manifest["enrollments"] if row["cluster"] == cluster
                and row["enrollment"] == "selected" and row["centralIngest"] is True]
    if len(rows) != 1 or len(selected) != 1:
        raise ValueError("cluster is not uniquely selected")
    row = rows[0]
    if not NAME.fullmatch(row["namespace"]) or not NAME.fullmatch(row["secretName"]):
        raise ValueError("invalid secret namespace/name")
    parent = manifest["managementCluster"]
    if not NAME.fullmatch(parent):
        raise ValueError("invalid management cluster")
    relative = Path("apps/sealed-secrets") / cluster / row["namespace"] / (row["secretName"] + ".yaml")
    if row["sealedPath"] != relative.as_posix() or set(row["keys"]) != KEYS:
        raise ValueError("invalid selected secret path or keys")
    receipt = json.loads((artifact / "identity.json").read_text())
    if receipt.get("cluster") != cluster or receipt.get("sealedPath") != relative.as_posix():
        raise ValueError("artifact guest/path differs")
    for key in ("hostedClusterUid", "controllerUid", "writerNamespaceUid", "certificateSha256"):
        if not verified_identity.get(key) or receipt.get(key) != verified_identity[key]:
            raise ValueError("artifact identity differs from independent verification")
    payload = (artifact / relative).read_bytes()
    if receipt.get("sealedSha256") != hashlib.sha256(payload).hexdigest():
        raise ValueError("artifact digest differs")
    documents = [doc for doc in yaml.safe_load_all(payload) if doc is not None]
    if len(documents) != 1 or not isinstance(documents[0], dict):
        raise ValueError("artifact must contain exactly one Secret document")
    secret = documents[0]
    metadata = secret.get("metadata", {})
    spec = secret.get("spec", {})
    template = spec.get("template", {}).get("metadata", {})
    annotations = metadata.get("annotations", {})
    template_annotations = template.get("annotations", {})
    if (secret.get("apiVersion") != "bitnami.com/v1alpha1" or secret.get("kind") != "SealedSecret"
            or metadata.get("name") != row["secretName"] or metadata.get("namespace") != row["namespace"]
            or template.get("name") != row["secretName"] or template.get("namespace") != row["namespace"]
            or any(str(item.get("sealedsecrets.bitnami.com/" + scope)).lower() == "true"
                   for item in (annotations, template_annotations)
                   for scope in ("cluster-wide", "namespace-wide"))
            or set(spec.get("encryptedData", {})) != KEYS
            or not all(isinstance(value, str) and value for value in spec["encryptedData"].values())
            or "data" in secret or "stringData" in secret
            or "data" in spec.get("template", {}) or "stringData" in spec.get("template", {})):
        raise ValueError("artifact secret contract or strict scope differs")
    child_name = cluster + "-sealed-secrets.yaml"
    app_path = Path("clusters") / parent / child_name
    app_bytes = (source / app_path).read_bytes()
    app = yaml.safe_load(app_bytes)
    if (app.get("kind") != "Application" or app["metadata"]["name"] != cluster + "-sealed-secrets"
            or app["spec"]["source"]["path"] != relative.parent.as_posix()
            or app["spec"]["destination"].get("name") != cluster):
        raise ValueError("generated child App differs from selected writer")
    source_index = yaml.safe_load((source / "clusters" / parent / "kustomization.yaml").read_text())
    if child_name not in source_index["resources"]:
        raise ValueError("source has not enrolled selected child")
    index_path = Path("clusters") / parent / "kustomization.yaml"
    text = (target / index_path).read_text()
    before = yaml.safe_load(text)
    if len(before["resources"]) != len(set(before["resources"])):
        raise ValueError("target resources are not unique")
    # Insert into the existing top-level resource list without rewriting patches
    # or replacing the target-owned retained resource set.
    if child_name not in before["resources"]:
        match = re.search(r"(?m)^resources:\s*\n((?:[ \t]*- [^\n]*\n)+)", text)
        if not match:
            raise ValueError("unsupported target resources layout")
        indent = re.match(r"[ \t]*", match.group(1)).group()
        text = text[:match.end()] + indent + "- " + child_name + "\n" + text[match.end():]
    after = yaml.safe_load(text)
    expected = dict(before)
    expected["resources"] = list(before["resources"])
    if child_name not in expected["resources"]:
        expected["resources"].append(child_name)
    if after != expected:
        raise ValueError("composition changed unrelated target state")
    if (target / relative).exists() and (target / relative).read_bytes() != payload:
        raise ValueError("existing writer artifact differs; explicit rotation required")
    # All checks precede writes: no empty child can be published on a failed check.
    outputs = {relative: payload, app_path: app_bytes, index_path: text.encode()}
    for path, content in outputs.items():
        (target / path).parent.mkdir(parents=True, exist_ok=True)
        (target / path).write_bytes(content)
    return [path.as_posix() for path in outputs]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "target", "artifact", "verified-identity"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cluster", required=True)
    args = parser.parse_args()
    paths = compose(args.source, args.target, args.artifact, args.cluster,
                    json.loads(args.verified_identity.read_text()))
    print(json.dumps({"cluster": args.cluster, "changedPaths": paths}))


if __name__ == "__main__":
    main()
