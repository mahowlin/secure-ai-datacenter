"""GitOps cluster directories derived from canonical SAIF cluster data.

Management and hosted cluster names come from data/clusters. Structural GitOps
prefixes (apps/hcp, clusters/_types) are repository layout, not cluster names.
"""

from __future__ import annotations

from pathlib import Path

import yaml


def _load(path: Path) -> dict:
    doc = yaml.safe_load(path.read_text())
    if not isinstance(doc, dict):
        raise ValueError(f"{path} is not a YAML mapping")
    return doc


def management_cluster_names(saif_root: Path) -> list[str]:
    names: list[str] = []
    for path in sorted((saif_root / "data" / "clusters").glob("*/cluster.yaml")):
        doc = _load(path)
        if doc.get("kind") != "OCPCluster":
            continue
        name = str((doc.get("metadata") or {}).get("name") or "")
        if not name:
            raise ValueError(f"OCPCluster {path} has no metadata.name")
        names.append(name)
    if not names:
        raise ValueError(f"no OCPCluster names under {saif_root / 'data' / 'clusters'}")
    return names


def hosted_cluster_names(saif_root: Path) -> list[str]:
    names: list[str] = []
    for path in sorted((saif_root / "data" / "clusters").glob("*/hcpclusters/*.yaml")):
        doc = _load(path)
        if doc.get("kind") != "HCPCluster":
            continue
        name = str((doc.get("metadata") or {}).get("name") or "")
        if not name:
            raise ValueError(f"HCPCluster {path} has no metadata.name")
        names.append(name)
    if not names:
        raise ValueError(f"no HCPCluster names under {saif_root / 'data' / 'clusters'}")
    return names


def gitops_cluster_dirs(saif_root: Path) -> list[str]:
    """Directory names transported into the declared GitOps repository."""
    structural = ["apps/hcp", "clusters/_types"]
    cluster_dirs = [
        f"clusters/{name}"
        for name in management_cluster_names(saif_root) + hosted_cluster_names(saif_root)
    ]
    return structural + cluster_dirs


def gitops_kustomize_prefixes(saif_root: Path) -> tuple[str, ...]:
    """Prefixes the golden harness builds. _types overlays stay structural."""
    prefixes = [
        f"clusters/{name}/"
        for name in management_cluster_names(saif_root) + hosted_cluster_names(saif_root)
    ]
    prefixes.extend([
        "clusters/_types/hcp-management/",
        "clusters/_types/hcp-tenant/",
        "apps/hcp/",
    ])
    return tuple(prefixes)
