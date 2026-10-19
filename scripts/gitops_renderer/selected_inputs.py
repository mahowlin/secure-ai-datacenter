"""Selected input set for one render.

Callers bind the resolved defaults they were given. Label, template, and
repository lookups read that object. They do not open PlatformDefaults from
the checkout that contains this module.
"""

from __future__ import annotations

import functools
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any


_CURRENT: ContextVar[Any] = ContextVar("saif_selected_defaults", default=None)


def current() -> Any:
    defaults = _CURRENT.get()
    if defaults is None:
        raise ValueError("renderer identity must come from the selected input set")
    return defaults


@contextmanager
def selected(defaults: Any) -> Iterator[Any]:
    identity = getattr(defaults, "identity", None)
    group = identity.get("apiGroup") if isinstance(identity, dict) else None
    if not isinstance(group, str) or not group:
        raise ValueError("selected PlatformDefaults spec.identity.apiGroup is required")
    token = _CURRENT.set(defaults)
    try:
        yield defaults
    finally:
        _CURRENT.reset(token)


def bind_defaults(func):
    """Bind the defaults argument for the duration of a renderer call."""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        defaults = kwargs.get("defaults")
        if defaults is None:
            for arg in args:
                identity = getattr(arg, "identity", None)
                group = identity.get("apiGroup") if isinstance(identity, dict) else None
                if isinstance(group, str) and group:
                    defaults = arg
                    break
        if defaults is None:
            raise ValueError("selected PlatformDefaults spec.identity.apiGroup is required")
        with selected(defaults):
            return func(*args, **kwargs)

    return wrapper


def api_group() -> str:
    identity = getattr(current(), "identity", None)
    group = identity.get("apiGroup") if isinstance(identity, dict) else None
    if not isinstance(group, str) or not group:
        raise ValueError("selected spec.identity.apiGroup is required")
    return group


def api_label(suffix: str) -> str:
    defaults = current()
    method = getattr(defaults, "api_label", None)
    if callable(method) and method is not api_label:
        return method(suffix)
    return f"{api_group()}/{suffix}"


API_GROUP_TOKEN = "__SAIF_API_GROUP__"


def render_selected_text(text: str) -> str:
    """Substitute the selected API group into a helper that names it with a token."""
    if API_GROUP_TOKEN not in text:
        return text
    return text.replace(API_GROUP_TOKEN, api_group())


def label_domain(name: str) -> str:
    value = _mapping("labelDomains").get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"selected spec.identity.labelDomains.{name} is required")
    return value


def domain_label(name: str, suffix: str) -> str:
    return f"{label_domain(name)}/{suffix}"


def derivation_owner() -> str:
    identity = getattr(current(), "identity", None) or {}
    value = identity.get("derivationOwner") if isinstance(identity, dict) else None
    if not isinstance(value, str) or not value:
        raise ValueError("selected spec.identity.derivationOwner is required")
    return value


def ilb_paths() -> dict:
    value = _mapping("ilb")
    names = value.get("applicationNames")
    if not isinstance(names, dict) or not names:
        raise ValueError("selected spec.identity.ilb.applicationNames is required")
    return value


def _mapping(key: str) -> dict:
    identity = getattr(current(), "identity", None)
    value = identity.get(key) if isinstance(identity, dict) else None
    if not isinstance(value, dict):
        raise ValueError(f"selected spec.identity.{key} is required")
    return value


def template_root(name: str) -> str:
    value = _mapping("templateRoots").get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"selected spec.identity.templateRoots.{name} is required")
    return value


def generated_repo(name: str) -> str:
    value = _mapping("generatedRepos").get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"selected spec.identity.generatedRepos.{name} is required")
    return value


def gitops_repo_url() -> str:
    url = getattr(current(), "gitops_repo_url", None)
    if isinstance(url, str) and url:
        return url
    value = _mapping("sourceRepositories").get("gitops")
    if not isinstance(value, str) or not value:
        raise ValueError("selected GitOps repository URL is required")
    return value


def registry_url() -> str:
    url = getattr(current(), "registry_url", None)
    if not isinstance(url, str) or not url:
        raise ValueError("selected spec.registry.url is required")
    return url


def identity() -> dict:
    value = getattr(current(), "identity", None)
    if not isinstance(value, dict) or not value:
        raise ValueError("selected spec.identity is required")
    return value


def gitops_foundation(name: str) -> str:
    from pathlib import PurePosixPath
    value = _mapping("gitopsFoundation").get(name)
    if (not isinstance(value, str) or not value.startswith("clusters/")
            or ".." in PurePosixPath(value).parts):
        raise ValueError("selected GitOps foundation root is required under clusters/")
    return value
