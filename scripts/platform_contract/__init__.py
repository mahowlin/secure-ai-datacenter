"""Versioned SAIF release/profile contract API."""

from .loader import load_catalogs, resolve_profile_intent, resolve_v2_tree
from .models import (
    IntentState,
    ResolvedPlatformIntent,
    SCHEMA_VERSION,
    SelectionPolicy,
    SupportStatus,
    ValidationStatus,
)
from .validation import ProfileContractError, WORKER_PROFILE_REFS

__all__ = [
    "ProfileContractError",
    "IntentState",
    "ResolvedPlatformIntent",
    "SCHEMA_VERSION",
    "SelectionPolicy",
    "SupportStatus",
    "ValidationStatus",
    "WORKER_PROFILE_REFS",
    "load_catalogs",
    "resolve_profile_intent",
    "resolve_v2_tree",
]
