#!/usr/bin/env python3
"""Seal the declared console-login password for each target namespace.

The password is read from the login keychain and piped to htpasswd. This
script never prints it, never writes it unencrypted, and commits nothing
itself. It writes only SealedSecret documents.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import json
import ssl
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml
from gitops_renderer.console_login import (
    HTPASSWD_KEY,
    sealed_path,
    secret_namespace,
)
from resolver import resolve


def _run(command: list[str], input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(command, input=input_bytes, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, check=False)
    if result.returncode:
        raise RuntimeError(f"command failed ({command[0]} exit {result.returncode})")
    return result.stdout


def _password_hash(policy: dict) -> bytes:
    credential = policy["credentialRef"]
    lookup = subprocess.Popen(
        ["security", "find-generic-password", "-s", credential["keychainService"],
         "-a", credential["keychainAccount"], "-w"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    hashed = subprocess.Popen(
        ["htpasswd", "-B", "-i", "-n", policy["username"]],
        stdin=lookup.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    if lookup.stdout is not None:
        lookup.stdout.close()
    line, _ = hashed.communicate(timeout=30)
    lookup.wait(timeout=30)
    if lookup.returncode or hashed.returncode or b":" not in line or b"\n" in line.strip():
        raise RuntimeError("htpasswd sealing input was not produced")
    if not line.startswith(policy["username"].encode() + b":$2"):
        raise RuntimeError("htpasswd did not produce a bcrypt entry")
    return line if line.endswith(b"\n") else line + b"\n"


def _certificate_status(cert_pem: bytes) -> str:
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / "controller.pem"
        path.write_bytes(cert_pem)
        text = subprocess.run(
            ["openssl", "x509", "-in", str(path), "-noout", "-text", "-dates", "-fingerprint", "-sha256"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False,
        )
    if text.returncode:
        raise RuntimeError("controller certificate could not be read")
    lines = text.stdout.decode().splitlines()
    selected = [line.strip() for line in lines if any(
        marker in line for marker in (
            "Not Before", "Not After", "Public-Key", "Signature Algorithm",
            "Issuer:", "Subject:", "SHA256 Fingerprint",
        )
    )]
    issuer = next((line.split(":", 1)[1].strip() for line in selected if line.startswith("Issuer:")), "")
    subject = next((line.split(":", 1)[1].strip() for line in selected if line.startswith("Subject:")), "")
    after = next((line.split(":", 1)[1].strip() for line in selected if "Not After" in line or line.startswith("notAfter")), "")
    key = next((line for line in selected if "Public-Key" in line), "")
    signature = next((line for line in selected if "Signature Algorithm" in line), "")
    algorithm = next((line for line in selected if "Public Key Algorithm" in line), "")
    if not after:
        raise RuntimeError("controller certificate has no expiry")
    cleaned = " ".join(after.replace("GMT", "").replace("UTC", "").split())
    expires = datetime.strptime(cleaned, "%b %d %H:%M:%S %Y").replace(tzinfo=timezone.utc)
    if expires <= datetime.now(timezone.utc):
        raise RuntimeError(f"controller certificate expired on {expires.date().isoformat()}")
    bits = "".join(ch for ch in key if ch.isdigit())
    elliptic = "id-ec" in algorithm.lower() or "ecds" in signature.lower()
    if bits and int(bits) < (256 if elliptic else 2048):
        raise RuntimeError(f"controller certificate key is too small ({bits})")
    weak_signature = any(name in signature.lower() for name in ("md5", "sha1"))
    if weak_signature:
        raise RuntimeError("controller certificate uses a weak signature")
    self_signed = "yes" if issuer and issuer == subject else "no"
    return (
        f"notAfter={expires.date().isoformat()} keyBits={bits or 'unknown'} "
        f"selfSigned={self_signed}"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--kubeconfig", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    cluster_dir = root / "data" / "clusters" / args.cluster
    cluster = next((item for item in resolve(root).clusters if item.name == args.cluster), None)
    policy = getattr(cluster, "console_login", None) or {}
    if not policy:
        raise RuntimeError("console login policy is absent")
    bootstrap = yaml.safe_load((cluster_dir / "gitops-bootstrap.yaml").read_text())
    controller = bootstrap["spec"]["sealedSecrets"]
    namespace, name = controller["controller"].split("/", 1)
    cert = _run(["kubeseal", "--kubeconfig", str(args.kubeconfig),
                 "--controller-namespace", namespace, "--controller-name", name, "--fetch-cert"])
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.decode())).hexdigest()
    if fingerprint != controller["publicCertificateFingerprintSha256"]:
        raise RuntimeError("SealedSecrets controller certificate changed; review and reseal required")
    status = _certificate_status(cert)
    password_hash = _password_hash(policy)
    try:
        with tempfile.TemporaryDirectory() as temporary:
            cert_path = Path(temporary) / "controller.pem"
            cert_path.write_bytes(cert)
            for target in policy["targets"]:
                namespace = secret_namespace(target)
                secret = {
                    "apiVersion": "v1",
                    "kind": "Secret",
                    "metadata": {"name": policy["secretName"], "namespace": namespace},
                    "type": "Opaque",
                    "stringData": {HTPASSWD_KEY: password_hash.decode()},
                }
                sealed_bytes = _run(
                    ["kubeseal", "--cert", str(cert_path), "--scope", "strict", "--format", "yaml"],
                    input_bytes=json.dumps(secret).encode(),
                )
                if password_hash in sealed_bytes or b"$2a$" in sealed_bytes or b"$2b$" in sealed_bytes or b"$2y$" in sealed_bytes:
                    raise RuntimeError("sealed output still contains the password hash")
                destination = sealed_path(root, args.cluster, target["name"])
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(sealed_bytes)
                print(f"sealed {namespace}/{policy['secretName']}")
    finally:
        password_hash = b""
    print(f"controller-cert fingerprint-match {status}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"seal failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
