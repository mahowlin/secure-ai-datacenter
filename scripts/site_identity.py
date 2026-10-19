"""Declared platform identity.

Every value here is loaded from PlatformDefaults. Missing keys fail.
This module does not contain environment-specific defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REQUIRED = (
    "apiGroup",
    "baseDomain",
    "chartRepository",
    "sourceRepositories",
    "templateRoots",
    "generatedRepos",
    "labelDomains",
    "derivationOwner",
    "publicImageRegistries",

)

OPTIONAL_REQUIRED = (
    "ilb",
    "managementNodes",
    "excludedNodeSubstrings",
    "canaryNode",
    "currentRouteNode",
    "preflightNode",
    "diagnosticOverlay",
    "ilbAdapterImage",
    "ilbAdapterDigest",
    "mutatorAnnotationDomain",
    "mutatorWebhookName",
    "productionEgressCIDR",
    "conformance",
)

NESTED = {
    "sourceRepositories": ("sysAdmin", "gitops", "platform", "aci", "openshift"),
    "templateRoots": ("clusterInstall", "gitops"),
    "generatedRepos": ("aci", "openshift", "gitops"),
    "labelDomains": ("poc", "scaffold"),
    "ilb": (
        "source",
        "stagedKustomization",
        "stagedResource",
        "stagedRoot",
        "hostedOverlay",
        "edgeOverlay",
        "authSecret",
        "renewSourceName",
        "controllerNamespace",
        "hostedDestinationName",
        "hostedDestinationNamespace",
        "applicationNames",
    ),
    "conformance": (
        "ilbNamespace",
        "ilbVip",
        "ilbPort",
        "routeNamespace",
        "routeHosts",
        "consoleHost",
        "consoleAddress",
        "hostedService",
    ),
}


def repo_root() -> Path:
    """Directory that contains this module. Renderers pass a selected root instead."""
    return Path(__file__).resolve().parents[1]


def platform_document(root: Path | None = None) -> tuple[Path, dict[str, Any]]:
    root = root or repo_root()
    defaults_dir = root / "data" / "defaults"
    if not defaults_dir.is_dir():
        raise ValueError(f"{defaults_dir}: PlatformDefaults directory is required")
    chosen: tuple[Path, dict[str, Any]] | None = None
    for path in sorted(defaults_dir.glob("*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        if not isinstance(data, dict) or data.get("kind") != "PlatformDefaults":
            continue
        if chosen is not None:
            raise ValueError(
                f"{defaults_dir}: multiple PlatformDefaults files; keep one"
            )
        chosen = (path, data)
    if chosen is None:
        raise ValueError(f"{defaults_dir}: PlatformDefaults is required")
    return chosen


def platform_spec(root: Path | None = None) -> dict[str, Any]:
    path, data = platform_document(root)
    spec = data.get("spec")
    if not isinstance(spec, dict):
        raise ValueError(f"{path}: spec is required")
    return spec


def identity_errors(identity: object, *, features: tuple[str, ...] = ()) -> list[str]:
    """Return errors for a spec.identity mapping. Empty means the mapping is usable."""
    if not isinstance(identity, dict):
        return ["spec.identity is required"]
    errors: list[str] = []
    required = REQUIRED
    if "conformance" in features:
        required += OPTIONAL_REQUIRED
    elif "ilb" in features:
        required += ("ilb", "ilbAdapterImage", "ilbAdapterDigest")
    missing = [key for key in required if identity.get(key) in (None, "", [], {})]
    if missing:
        errors.append(f"spec.identity missing {', '.join(missing)}")
        return errors
    for key, children in NESTED.items():
        if key not in required and key not in identity:
            continue
        block = identity[key]
        if not isinstance(block, dict):
            errors.append(f"spec.identity.{key} must be a mapping")
            continue
        absent = [child for child in children
                  if block.get(child) in (None, "", [], {})
                  and not (key == "traffic" and child == "managementCa")]
        if absent:
            errors.append(f"spec.identity.{key} missing {', '.join(absent)}")
    nodes = identity.get("managementNodes")
    banned = identity.get("excludedNodeSubstrings")
    hosts = identity.get("conformance", {}).get("routeHosts") if isinstance(identity.get("conformance"), dict) else None
    if ("conformance" in features or nodes is not None) and (not isinstance(nodes, list) or not all(isinstance(item, str) and item for item in nodes)):
        errors.append("spec.identity.managementNodes must list node names")
    if ("conformance" in features or banned is not None) and (not isinstance(banned, list) or not all(isinstance(item, str) and item for item in banned)):
        errors.append("spec.identity.excludedNodeSubstrings must list substrings")
    if ("conformance" in features or "conformance" in identity) and (not isinstance(hosts, dict) or not hosts):
        errors.append("spec.identity.conformance.routeHosts must map route names")
    registries = identity.get("publicImageRegistries")
    if not isinstance(registries, list) or not all(isinstance(item, str) and item for item in registries):
        errors.append("spec.identity.publicImageRegistries must list registry prefixes")
    return errors


def load_identity(root: Path | None = None) -> dict[str, Any]:
    path, data = platform_document(root)
    spec = data.get("spec")
    identity = spec.get("identity") if isinstance(spec, dict) else None
    problems = identity_errors(identity)
    if problems:
        raise ValueError(f"{path}: " + "; ".join(problems))
    return identity


def api_group(root: Path | None = None) -> str:
    return str(load_identity(root)["apiGroup"])


def api_label(suffix: str, root: Path | None = None) -> str:
    return f"{api_group(root)}/{suffix}"


def template_root(name: str, root: Path | None = None) -> str:
    return str(load_identity(root)["templateRoots"][name])


def generated_repo(name: str, root: Path | None = None) -> str:
    return str(load_identity(root)["generatedRepos"][name])


def platform_defaults_file(root: Path | None = None) -> Path:
    return platform_document(root)[0]


def network_catalog_file(root: Path | None = None) -> Path:
    root = root or repo_root()
    files = sorted((root / "data" / "networks").glob("*.yaml"))
    if len(files) != 1:
        raise ValueError(f"{root / 'data' / 'networks'}: exactly one network catalog is required")
    return files[0]


def gitops_worker_value(key: str, root: Path | None = None) -> str:
    gitops = platform_spec(root).get("gitops") or {}
    worker = gitops.get("workerIpam") if isinstance(gitops, dict) else None
    value = worker.get(key) if isinstance(worker, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError(f"spec.gitops.workerIpam.{key} is required")
    return value


def public_image_registries(root: Path | None = None) -> tuple[str, ...]:
    values = load_identity(root)["publicImageRegistries"]
    return tuple(values)


def registry_url(root: Path | None = None) -> str:
    registry = platform_spec(root).get("registry") or {}
    url = registry.get("url") if isinstance(registry, dict) else None
    if not isinstance(url, str) or not url:
        raise ValueError("PlatformDefaults spec.registry.url is required")
    return url


def gitops_repo_url(root: Path | None = None) -> str:
    gitops = platform_spec(root).get("gitops") or {}
    url = gitops.get("repoURL") if isinstance(gitops, dict) else None
    if not isinstance(url, str) or not url:
        raise ValueError("PlatformDefaults spec.gitops.repoURL is required")
    return url
