#!/usr/bin/env python3
"""Renew only the example-tenant-a ILB adapter's scoped hosted API credential.

Runs in the management cluster with named Secret GET/UPDATE permissions. No
admin credential or token is logged or stored outside private temporary files.
"""

import base64
import binascii
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ADMIN_NAMESPACE = "clusters-example-tenant-a"
ADMIN_SECRET = "example-tenant-a-admin-kubeconfig"
# The host-networked Cilium operator reads this kubeconfig and uses node DNS.
# Node DNS does not resolve Kubernetes Service names; the HCP passthrough Route
# is reachable from those nodes and uses the same hosted API CA.
HOSTED_SERVER = "https://api.example-tenant-a.example.com:443"
HOSTED_NAMESPACE = "ilb-poc"
HOSTED_ACCOUNT = "ilb-hcp-pip-remote"
TARGET_NAMESPACE = "cilium-secrets"
TARGET_SECRET = "example-tenant-a-k8sbackend-auth"
EXPECTED_IDENTITY = f"system:serviceaccount:{HOSTED_NAMESPACE}:{HOSTED_ACCOUNT}"
MIN_LIFETIME_SECONDS = 1800
ANNOTATION_PREFIX = "saif.example.com/"


class RenewalError(Exception):
    pass


def run_oc(step, args, input_bytes=None):
    result = subprocess.run(
        ["oc", "--request-timeout=10s", *args], input=input_bytes,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25,
        check=False,
    )
    if result.returncode:
        # oc stderr can contain sensitive remote response details; do not log it.
        raise RenewalError(f"{step} failed (exit {result.returncode})")
    return result.stdout


def token_expiry(token, now):
    try:
        body = token.split(".")[1]
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        expiry = int(payload["exp"])
    except (IndexError, KeyError, ValueError, TypeError, binascii.Error) as exc:
        raise RenewalError("TokenRequest returned a token without a readable expiry") from exc
    if expiry - now < MIN_LIFETIME_SECONDS:
        raise RenewalError("TokenRequest lifetime is too short for scheduled renewal")
    return expiry


def hosted_ca(config):
    try:
        context_name = config["current-context"]
        context = next(item["context"] for item in config["contexts"]
                       if item["name"] == context_name)
        cluster = next(item["cluster"] for item in config["clusters"]
                       if item["name"] == context["cluster"])
        ca_data = cluster["certificate-authority-data"]
        if cluster.get("insecure-skip-tls-verify") or not ca_data:
            raise RenewalError("admin kubeconfig does not supply a verified CA")
        base64.b64decode(ca_data, validate=True)
        return ca_data
    except (KeyError, StopIteration, ValueError, binascii.Error) as exc:
        raise RenewalError("admin kubeconfig lacks its hosted API CA") from exc


def scoped_kubeconfig(ca_data, token):
    return json.dumps({
        "apiVersion": "v1", "kind": "Config",
        "clusters": [{"name": "example-tenant-a-internal", "cluster": {
            "server": HOSTED_SERVER, "certificate-authority-data": ca_data}}],
        "contexts": [{"name": "example-tenant-a-ilb", "context": {
            "cluster": "example-tenant-a-internal", "user": HOSTED_ACCOUNT}}],
        "current-context": "example-tenant-a-ilb",
        "users": [{"name": HOSTED_ACCOUNT, "user": {"token": token}}],
    }, separators=(",", ":")).encode()


def replacement_secret(existing, kubeconfig, expiry, now):
    meta = existing.get("metadata", {})
    if (meta.get("namespace") != TARGET_NAMESPACE
            or meta.get("name") != TARGET_SECRET
            or not meta.get("resourceVersion")):
        raise RenewalError("named target Secret is absent or lacks resourceVersion")
    updated = copy.deepcopy(existing)
    updated.pop("status", None)
    updated["metadata"].pop("managedFields", None)
    updated.setdefault("data", {})["kubeconfig"] = base64.b64encode(kubeconfig).decode()
    annotations = updated["metadata"].setdefault("annotations", {})
    annotations[ANNOTATION_PREFIX + "token-expires-at"] = datetime.fromtimestamp(
        expiry, timezone.utc).isoformat().replace("+00:00", "Z")
    annotations[ANNOTATION_PREFIX + "token-rotated-at"] = datetime.fromtimestamp(
        now, timezone.utc).isoformat().replace("+00:00", "Z")
    annotations[ANNOTATION_PREFIX + "credential-source"] = "hosted-serviceaccount-tokenrequest"
    return updated


def rotate(oc=run_oc, now=None):
    now = int(time.time()) if now is None else int(now)
    os.umask(0o077)
    with tempfile.TemporaryDirectory(prefix="example-tenant-a-ilb-renew-", dir="/tmp") as temp:
        admin_response = json.loads(oc("read named HCP admin Secret", [
            "-n", ADMIN_NAMESPACE, "get", "secret", ADMIN_SECRET, "-o", "json"]))
        encoded = admin_response.get("data", {}).get("kubeconfig")
        if not encoded:
            raise RenewalError("named HCP admin Secret lacks kubeconfig")
        try:
            admin_bytes = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise RenewalError("named HCP admin kubeconfig is invalid") from exc
        admin_path = Path(temp) / "admin.json"
        admin_path.write_bytes(admin_bytes)
        admin_path.chmod(0o600)
        config = json.loads(oc("parse HCP admin kubeconfig", [
            "--kubeconfig", str(admin_path), "config", "view", "--raw", "-o", "json"]))
        ca_data = hosted_ca(config)
        token = oc("hosted TokenRequest", [
            "--kubeconfig", str(admin_path), "--server", HOSTED_SERVER,
            "-n", HOSTED_NAMESPACE, "create", "token", HOSTED_ACCOUNT,
            "--duration=1h",
        ]).decode().strip()
        expiry = token_expiry(token, now)
        scoped_bytes = scoped_kubeconfig(ca_data, token)
        scoped_path = Path(temp) / "scoped.json"
        scoped_path.write_bytes(scoped_bytes)
        scoped_path.chmod(0o600)
        identity = oc("verify scoped API identity and TLS", [
            "--kubeconfig", str(scoped_path), "whoami",
        ]).decode().strip()
        if identity != EXPECTED_IDENTITY:
            raise RenewalError("scoped credential returned unexpected identity")
        service = oc("verify hosted fixture access", [
            "--kubeconfig", str(scoped_path), "-n", HOSTED_NAMESPACE,
            "get", "service", "echo", "-o", "name",
        ]).decode().strip()
        if service != "service/echo":
            raise RenewalError("scoped credential cannot read hosted echo Service")
        existing = json.loads(oc("read named target Secret", [
            "-n", TARGET_NAMESPACE, "get", "secret", TARGET_SECRET, "-o", "json"]))
        replacement = replacement_secret(existing, scoped_bytes, expiry, now)
        oc("replace named target Secret", ["-n", TARGET_NAMESPACE, "replace", "-f", "-"],
           json.dumps(replacement, separators=(",", ":")).encode())
    return datetime.fromtimestamp(expiry, timezone.utc).isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    try:
        expiry_text = rotate()
    except (RenewalError, subprocess.TimeoutExpired, OSError, KeyError, ValueError) as exc:
        # Never include command stdout/stderr or credential-bearing objects.
        print(f"example-tenant-a ILB credential renewal failed: {type(exc).__name__}", file=sys.stderr)
        sys.exit(1)
    print(f"example-tenant-a ILB credential renewed; expires {expiry_text}")
