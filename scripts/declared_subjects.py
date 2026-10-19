"""Role and subject selection for deploy-path renderers.

Phase directories and worker namespaces are derived from a declared role plus
the fixture's selected hosted cluster. A missing role or subject fails.
"""

from __future__ import annotations


PHASE_SUFFIX = {
    "production": "production",
    "retained": "retained",
    "currentBridge": "current-bridge",
}
PLAIN_PHASES = ("no-route", "local-breakout", "overlap", "hcp-hairpin")
REQUIRED_PHASE_ROLES = ("production", "retained", "currentBridge", "diagnostic", *PLAIN_PHASES)
PRODUCTION_ROLES = ("production", "retained", "overlap", "hcp-hairpin")


def require_text(value, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} is required")
    return value


def egw_phase(subject: str, role: str, diagnostic_overlay: str) -> str:
    subject = require_text(subject, "EGW subject hosted cluster")
    if role in PHASE_SUFFIX:
        return f"{subject}-{PHASE_SUFFIX[role]}"
    if role == "diagnostic":
        return require_text(diagnostic_overlay, "EGW diagnostic overlay")
    if role in PLAIN_PHASES:
        return role
    raise ValueError(f"unknown EGW phase role {role}")


def egw_phases(subject: str, diagnostic_overlay: str) -> dict[str, str]:
    return {role: egw_phase(subject, role, diagnostic_overlay) for role in REQUIRED_PHASE_ROLES}


def egw_resource(subject: str, kind: str, diagnostic_policy: str) -> str:
    subject = require_text(subject, "EGW subject hosted cluster")
    if kind == "productionPolicy":
        return f"{subject}-egw-conformance"
    if kind == "advertisement":
        return f"{subject}-egw-conformance"
    if kind == "diagnosticPolicy":
        return require_text(diagnostic_policy, "EGW diagnostic policy name")
    raise ValueError(f"unknown EGW resource {kind}")


def worker_namespace(name: str) -> str:
    name = require_text(name, "hosted cluster")
    return f"clusters-{name}-{name}"


def egw_bridge_owner(roles: dict, subjects: dict, fixture_namespace: str) -> str:
    """Return the hosted cluster that owns this worker namespace."""
    if not isinstance(subjects, dict):
        raise ValueError("deploymentSubjects are required")
    namespace = require_text(fixture_namespace, "worker fixture namespace")
    owners = [
        name for key, name in subjects.items()
        if key != "stagedOnly" and isinstance(name, str) and worker_namespace(name) == namespace
    ]
    if len(owners) != 1:
        raise ValueError("worker fixture namespace must match exactly one hosted cluster")
    return owners[0]
