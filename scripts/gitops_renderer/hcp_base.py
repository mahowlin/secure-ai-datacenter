"""Render the declared HCP base documents.

The files under data/gitops/hcp-base hold the Application and kustomization
text. Repository URL and image registry are not part of that text: the URL's
repository name comes from spec.identity.generatedRepos.gitops, its owner
from spec.identity.sourceRepositories.gitops, and the image registry from
spec.registry.url. A management cluster name or base domain in the declared
text is rejected.
"""

from pathlib import Path
import re


_REPO_TOKEN = "__GITOPS_REPO_URL__"
_REGISTRY_TOKEN = "__IMAGE_REGISTRY__"
_EMBEDDED_REGISTRY = re.compile(r"repository:\s+\S+\.\S+")


def _identity(defaults) -> dict:
    identity = getattr(defaults, "identity", None)
    if not isinstance(identity, dict):
        raise ValueError("spec.identity is required")
    return identity


def _repo_url(defaults) -> str:
    identity = _identity(defaults)
    generated = (identity.get("generatedRepos") or {}).get("gitops")
    source = (identity.get("sourceRepositories") or {}).get("gitops")
    if not isinstance(generated, str) or not generated or "/" in generated:
        raise ValueError("spec.identity.generatedRepos.gitops is required")
    if not isinstance(source, str) or source.count("/") != 1:
        raise ValueError("spec.identity.sourceRepositories.gitops is required")
    owner, _repo = source.split("/", 1)
    if not owner or not _repo:
        raise ValueError("spec.identity.sourceRepositories.gitops is required")
    return f"https://github.com/{owner}/{generated}.git"


def _registry(defaults) -> str:
    url = getattr(defaults, "registry_url", None)
    if not isinstance(url, str) or not url or "/" in url:
        raise ValueError("spec.registry.url is required")
    return url


def _render_text(text: str, defaults, cluster_name: str | None) -> str:
    identity = getattr(defaults, "identity", None)
    if isinstance(identity, dict):
        domain = identity.get("baseDomain")
        if isinstance(domain, str) and domain and domain in text:
            raise ValueError("declared HCP base file must take its domain from spec.identity")
    if cluster_name and cluster_name in text:
        raise ValueError("declared HCP base file must not name the management cluster")
    external = text.replace("https://kubernetes.default.svc", "")
    if "https://" in external or _EMBEDDED_REGISTRY.search(text):
        raise ValueError("declared HCP base file must not embed a repository URL or registry host")
    application = "\nkind: Application\n" in f"\n{text}"
    if application and _REPO_TOKEN not in text:
        raise ValueError("declared HCP base Application must reference the generated GitOps repository")
    if _REPO_TOKEN in text:
        text = text.replace(_REPO_TOKEN, _repo_url(defaults))
    if _REGISTRY_TOKEN in text:
        text = text.replace(_REGISTRY_TOKEN, _registry(defaults))
    if _REPO_TOKEN in text or _REGISTRY_TOKEN in text:
        raise ValueError("declared HCP base file has an unresolved identity token")
    return text


def render_hcp_base(defaults, cluster_name: str | None = None) -> dict[str, str]:
    """Return the base documents with repository URL and registry filled in."""
    root = getattr(defaults, "input_root", None)
    if root is None:
        raise ValueError("selected input root is required")
    base = Path(root) / "data" / "gitops" / "hcp-base"
    if not base.is_dir():
        raise ValueError("declared HCP base directory is required")

    files: dict[str, str] = {}
    groups = [path for path in sorted(base.iterdir()) if path.is_dir()]
    if not groups:
        raise ValueError("declared HCP base directory is required")
    for group in groups:
        members = [
            path for path in sorted(group.iterdir())
            if path.is_file() and path.suffix == ".yaml"
        ]
        extras = [
            path for path in group.iterdir()
            if path not in members and not path.name.startswith(".")
        ]
        if extras or not members:
            raise ValueError(f"declared HCP base group {group.name} must contain yaml files only")
        for path in members:
            text = path.read_text(encoding="utf-8")
            if not text.endswith("\n"):
                raise ValueError(f"declared HCP base file {path.name} must end with a newline")
            files[f"clusters/_base/{group.name}/{path.name}"] = _render_text(text, defaults, cluster_name)
    return files


def merge_declared_hcp_base(files: dict[str, str], defaults, cluster_name: str | None = None) -> None:
    """Add the declared base documents, refusing to overwrite another emitter."""
    declared = render_hcp_base(defaults, cluster_name)
    overlap = sorted(set(declared) & set(files))
    if overlap:
        raise ValueError("declared HCP base overlaps an emitted path: " + ", ".join(overlap))
    files.update(declared)
