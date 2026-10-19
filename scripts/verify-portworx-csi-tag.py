#!/usr/bin/env python3
"""Verify the sole Portworx tag-only operator input against its locked digest."""

import argparse
import hashlib
import os
import stat
import subprocess
from pathlib import Path

import yaml

from resolver import hosted_control_plane_name
from site_identity import platform_defaults_file


def fetch_manifest(reference: str, authfile: Path | None, cert_dir: Path | None) -> bytes:
    command = ["skopeo", "inspect", "--raw", "--tls-verify=true"]
    if authfile:
        if not authfile.is_file() or stat.S_IMODE(authfile.stat().st_mode) & 0o077:
            raise ValueError("registry auth file must exist with private permissions")
        command += ["--authfile", str(authfile)]
    if cert_dir:
        if not cert_dir.is_dir():
            raise ValueError("registry certificate directory does not exist")
        command += ["--cert-dir", str(cert_dir)]
    result = subprocess.run([*command, "docker://" + reference], capture_output=True, timeout=60)
    if result.returncode or not result.stdout:
        raise ValueError("authenticated TLS registry manifest lookup failed")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-root", type=Path, required=True)
    parser.add_argument("--authfile", type=Path, default=os.environ.get("REGISTRY_AUTH_FILE"))
    parser.add_argument("--cert-dir", type=Path, default=os.environ.get("REGISTRY_CERT_DIR"))
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    defaults = yaml.safe_load(platform_defaults_file(root).read_text())
    px = defaults["spec"]["gitops"]["storage"]["portworx"]
    if px.get("csiProvisionerTagOnlyCompatibility") is not True:
        return

    locked = px["images"]["csi-provisioner"]
    source, digest = locked.rsplit("@", 1)
    registry = defaults["spec"]["registry"]["url"]
    expected_tag = f"{registry}/{source.split('/', 1)[1]}"
    cluster = hosted_control_plane_name(root)
    manifest_path = args.generated_root / "apps/storage/portworx-local" / cluster / "px-versions.yaml"
    configmap = yaml.safe_load(manifest_path.read_text())
    actual_tag = yaml.safe_load(configmap["data"]["versions.yaml"])["components"]["csiProvisioner"]
    if actual_tag != expected_tag:
        raise SystemExit(f"CSI provisioner reference differs from locked inventory: {actual_tag}")

    observed = "sha256:" + hashlib.sha256(fetch_manifest(actual_tag, args.authfile, args.cert_dir)).hexdigest()
    if observed != digest:
        raise SystemExit(f"CSI provisioner tag digest mismatch: observed {observed}, locked {digest}")
    print(f"CSI provisioner tag verified: {actual_tag} -> {observed}")


if __name__ == "__main__":
    main()
