"""GitOps renderer package for the declared GitOps repository."""

from .orchestrator import render_gitops

__all__ = ["render_gitops"]
