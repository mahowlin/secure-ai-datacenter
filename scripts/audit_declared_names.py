#!/usr/bin/env python3
"""Report inventory names that still appear as literals under scripts/ and templates/.

The token set is derived from data/ and the experiment inventory: cluster and
hosted-cluster names, node pools, namespaces, tenants, networks, VLAN IDs,
addresses, domains, service accounts, and file stems. Tests and the off-render
tools are excluded. A hit is functional until this module classifies it.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PREFIXES = (
    "data/clusters/",
    "data/networks/",
    "data/defaults/",
    "data/ilb/",
    "data/package-inputs/",
    "data/traffic/",
    "data/reviews/",
    "experiments/",
)
SCAN_SUFFIXES = {".py", ".j2", ".yml", ".yaml", ".sh"}

# Experimental helpers the GitOps orchestrator does not call. Their literals
# stay a notes follow-up and are outside this render-path sweep.
OFF_RENDER_TOOLS = {
    "render_peer_route_preflight.py",
}

SITE_KEYS = {
    "namespace": "namespace",
    "serviceAccount": "service-account",
    "serviceAccountName": "service-account",
    "tenant": "tenant",
    "hostedCluster": "hosted-cluster",
    "clusterName": "cluster",
    "nodePool": "node-pool",
    "baseDomain": "domain",
    "apiGroup": "domain",
    "serverName": "name",
    "installerNamespace": "namespace",
    "workloadNamespace": "namespace",
    "targetNamespace": "namespace",
    "subjectHostedCluster": "hosted-cluster",
    "hostedDestinationName": "hosted-cluster",
    "backendCluster": "hosted-cluster",
    "suffix": "node-pool",
}
NETWORK_SECTIONS = {
    "managementNetworks",
    "kubeAPINetworks",
    "endpointNetworks",
    "tenantNetworks",
    "fabricNetworks",
    "domainVLANs",
}
SKIP_STEMS = {
    "cluster",
    "kustomization",
    "metadata",
    "values",
    "release",
    "activation",
    "defaults",
    "catalog",
    "schema",
    "networks",
    "clusters",
}
GENERIC_TOKENS = {
    "name",
    "data",
    "spec",
    "apps",
    "base",
    "true",
    "false",
    "null",
    "main",
    "test",
    "node",
    "port",
    "host",
    "path",
    "file",
    "type",
    "default",
    "none",
    "master",
    "worker",
    "management",
    "hosted",
    "kustomization.yaml",
    "cluster.yaml",
}
ADDRESS = re.compile(r"(?<![0-9])(?:\d{1,3}\.){3}\d{1,3}(?:/\d{1,2})?(?![0-9])")
TOKEN_EDGE = r"[A-Za-z0-9._-]"
SITE_DOMAIN = re.compile(r"([a-z0-9][a-z0-9.-]*\.(?:example\.com|isovalent\.com))", re.IGNORECASE)


def _distinctive(text: str) -> bool:
    if len(text) < 6 or text.lower() in GENERIC_TOKENS or any(ch.isspace() for ch in text):
        return False
    if text.endswith((".yaml", ".yml", ".json", ".pem", ".py", ".md", ".txt")):
        return False
    if text.startswith(("data/", "scripts/", "templates/", "experiments/", "/")):
        return False
    return "-" in text or "." in text or any(ch.isdigit() for ch in text)


def _add(tokens: dict[str, set[str]], category: str, value: object, *, distinctive: bool = False) -> None:
    if not isinstance(value, str):
        return
    text = value.strip().strip('"').strip("'")
    if text.lower() in GENERIC_TOKENS or any(ch.isspace() for ch in text):
        return
    if text.startswith(("http://", "https://")):
        host = text.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0]
        if host and _distinctive(host):
            tokens.setdefault("domain", set()).add(host.lower())
        return
    if distinctive and not _distinctive(text):
        return
    if len(text) < 4:
        return
    tokens.setdefault(category, set()).add(text)


def _walk(node: object, tokens: dict[str, set[str]], key: str | None = None) -> None:
    if isinstance(node, dict):
        if key in NETWORK_SECTIONS:
            for net_name, body in node.items():
                _add(tokens, "network", str(net_name), distinctive=True)
                _walk(body, tokens, str(net_name))
            return
        for child_key, child in node.items():
            if child_key == "namespaces" and isinstance(child, dict):
                for item in child.values():
                    _add(tokens, "namespace", item)
            category = SITE_KEYS.get(str(child_key))
            if category and isinstance(child, str):
                _add(tokens, category, child)
            elif category and isinstance(child, list):
                for item in child:
                    _add(tokens, category, item)
            elif child_key == "name":
                if isinstance(child, str):
                    _add(tokens, "name", child, distinctive=True)
                elif isinstance(child, list):
                    for item in child:
                        _add(tokens, "name", item, distinctive=True)
            elif child_key in {"serviceAccount", "serviceAccountName"} and isinstance(child, str):
                _add(tokens, "service-account", child)
            if child_key in {"vlan", "vlans"} and isinstance(child, int) and child >= 100:
                tokens.setdefault("vlan", set()).add(str(child))
            _walk(child, tokens, str(child_key))
        return
    if isinstance(node, list):
        for item in node:
            if key in {"stagedOnly", "maintenanceVmHolds", "deploymentSubjects"}:
                _add(tokens, "hosted-cluster", item, distinctive=True)
            _walk(item, tokens, key)
        return
    if isinstance(node, str):
        for address in ADDRESS.findall(node):
            tokens.setdefault("address", set()).add(address)
        for domain in SITE_DOMAIN.findall(node):
            if len(domain) >= 8:
                tokens.setdefault("domain", set()).add(domain.lower())
    elif isinstance(node, int) and key in {"vlan", "vlans"} and node >= 100:
        tokens.setdefault("vlan", set()).add(str(node))


def _source_selected(path: Path, root: Path) -> bool:
    relative = path.relative_to(root).as_posix()
    return relative.startswith(SOURCE_PREFIXES)


def collect_tokens(root: Path = ROOT) -> dict[str, set[str]]:
    tokens: dict[str, set[str]] = {}
    for prefix in SOURCE_PREFIXES:
        source_root = root / prefix
        if not source_root.is_dir():
            continue
        for path in source_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".yaml", ".yml"}:
                continue
            if not _source_selected(path, root):
                continue
            stem = path.stem
            if stem not in SKIP_STEMS and (_distinctive(stem) or stem in {"example"}):
                tokens.setdefault("file-stem", set()).add(stem)
            try:
                document = yaml.safe_load(path.read_text())
            except yaml.YAMLError:
                continue
            _walk(document, tokens)
    clusters = root / "data" / "clusters"
    if clusters.is_dir():
        for cluster_dir in clusters.iterdir():
            if not cluster_dir.is_dir():
                continue
            _add(tokens, "cluster", cluster_dir.name, distinctive=True)
            hcp_dir = cluster_dir / "hcpclusters"
            if hcp_dir.is_dir():
                for hcp in hcp_dir.glob("*.yaml"):
                    _add(tokens, "hosted-cluster", hcp.stem, distinctive=True)
    return {category: values for category, values in tokens.items() if values}


def scan_files(root: Path = ROOT) -> list[Path]:
    found: list[Path] = []
    for name in ("scripts", "templates"):
        scan_root = root / name
        if not scan_root.is_dir():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SCAN_SUFFIXES:
                continue
            if path.name in OFF_RENDER_TOOLS or path.name == Path(__file__).name:
                continue
            if "tests" in path.parts:
                continue
            found.append(path)
    return sorted(found)


def iter_hits(root: Path = ROOT):
    tokens = collect_tokens(root)
    ordered = sorted(
        ((category, token) for category, values in tokens.items() for token in values),
        key=lambda item: len(item[1]),
        reverse=True,
    )
    patterns = [
        (category, token, re.compile(rf"(?<!{TOKEN_EDGE}){re.escape(token)}(?!{TOKEN_EDGE})", re.IGNORECASE))
        for category, token in ordered
    ]
    for path in scan_files(root):
        try:
            lines = path.read_text().splitlines()
        except UnicodeDecodeError:
            continue
        comment_block = False
        for number, line in enumerate(lines, start=1):
            if path.suffix == ".j2" and "{#" in line:
                comment_block = True
            covered: list[tuple[int, int]] = []
            seen: set[str] = set()
            for category, token, pattern in patterns:
                if token.lower() in seen:
                    continue
                match = pattern.search(line)
                if match is None:
                    continue
                span = match.span()
                if any(start <= span[0] and span[1] <= end for start, end in covered):
                    continue
                seen.add(token.lower())
                covered.append(span)
                yield path.relative_to(root).as_posix(), number, token, category, line, comment_block
            if path.suffix == ".j2" and "#}" in line:
                comment_block = False


# Product, platform, and protocol identifiers. The inventory repeats them
# because this lab deploys those components. They are not site selectors.
ACCEPTED_TOKENS = {
    "cilium": "Cilium component and controller operand namespace, independent of site/tenant identity",
    "clife-controller-manager": "CLIFE upstream controller deployment and service account",
    "ciliumconfigs.cilium.io": "CLIFE upstream CiliumConfig CRD name",
    "clife-manager-role": "CLIFE upstream controller RBAC role",
    "portworx": "Portworx storage component and profile",
    "provider-cutover": "storage activation state of the Portworx profile",
    "consumer-hold": "storage activation state of the Portworx profile",
    "nvme-data": "SNO mdadm array name in the storage profile",
    "hypershift": "HyperShift component",
    "hubble-timescape": "Hubble Timescape component",
    "local-registry": "management local-registry component",
    "openshift-ingress": "OpenShift platform-managed ingress operand namespace, independent of site/tenant identity",
    "openshift-ingress-operator": "OpenShift platform-managed Ingress Operator namespace, independent of site/tenant identity",
    "openshift-gitops": "OpenShift GitOps namespace",
    "openshift-cnv": "OpenShift Virtualization namespace",
    "multicluster-engine": "multicluster engine component",
    "px-csi-replicated-default": "Portworx CSI storage class",
    "pull-secret": "OpenShift pull-secret object",
    "ssh-key": "OpenShift ssh-key object",
    "gitops-bootstrap": "GitOps bootstrap image-policy bundle",
    "registry-ca-bundle": "registry CA bundle object",
    "remote-health": "remote health component",
    "operator-generic": "operator catalog component",
    "cilium-cni": "Cilium CNI plugin type",
    "0.0.0.0/0": "default route",
    "192.0.2.0/24": "RFC 5737 TEST-NET-1 documentation prefix",
    "192.0.2.11": "RFC 5737 TEST-NET-1 documentation address",
}


def _documentation_line(path: str, line: str, token: str, comment: bool) -> bool:
    if comment:
        return True
    stripped = line.strip()
    if stripped.startswith("#"):
        return True
    if "#" in line:
        code, _, tail = line.partition("#")
        if token.lower() not in code.lower() and token.lower() in tail.lower():
            return True
    if path.endswith(".j2") and stripped.startswith(("{#", "#}")):
        return True
    return False


def classify(path: str, line: str, token: str, *, comment: bool = False) -> str:
    """Return documentation, accepted, or functional for one sweep hit."""
    if _documentation_line(path, line, token, comment):
        return "documentation"
    if token.lower() in {"isovalent.com"} and "isovalent.com/v1" in line.lower():
        return "accepted"
    if token.lower() in ACCEPTED_TOKENS:
        return "accepted"
    return "functional"


def functional_hits(root: Path = ROOT) -> list[tuple[str, int, str, str]]:
    hits = []
    for path, number, token, _category, line, comment in iter_hits(root):
        kind = classify(path, line, token, comment=comment)
        if kind == "functional":
            hits.append((path, number, token, line.strip()))
    return hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", action="store_true")
    parser.add_argument("--functional", action="store_true")
    args = parser.parse_args()
    tokens = collect_tokens()
    if args.counts:
        for category, values in sorted(tokens.items()):
            print(f"{category}: {len(values)}")
        print(f"files: {len(scan_files())}")
        return 0
    functional = 0
    other = 0
    for path, number, token, _category, line, comment in iter_hits():
        kind = classify(path, line, token, comment=comment)
        if args.functional and kind != "functional":
            continue
        if kind == "functional":
            functional += 1
        else:
            other += 1
        print(f"{kind}\t{path}:{number}\t{token}")
    print(f"# functional {functional}; other {other}", file=sys.stderr)
    return 1 if functional else 0


if __name__ == "__main__":
    raise SystemExit(main())
