"""Immutable model for the versioned SAIF profile contract.

The existing renderer-facing resolver graph remains a compatibility surface while
the profile migration is staged.  These types are the fail-closed intent model
that future renderers consume: every selector has an origin, and every renderer
authorization is owned by a complete named profile.
"""

from __future__ import annotations
from site_identity import api_label

from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any


SCHEMA_VERSION = api_label("v2alpha1")


class SupportStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


class ValidationStatus(str, Enum):
    SETTLED = "settled"
    ACTIVE_TEST = "active-test"
    CANDIDATE = "candidate"
    INVALID = "invalid"


class SelectionPolicy(str, Enum):
    NORMAL = "normal"
    LEGACY_ONLY = "legacy-only"
    ACTIVE_TEST_ONLY = "active-test-only"
    REQUIRES_GATE = "requires-gate"


class IntentState(str, Enum):
    SETTLED = "settled"
    ACTIVE_TEST = "active-test"
    CANDIDATE = "candidate"
    LEGACY = "legacy"


class GateDecision(str, Enum):
    ACCEPTED = "accepted"
    REVOKED = "revoked"


@dataclass(frozen=True)
class ArtifactReference:
    ref: str
    digest: str


@dataclass(frozen=True)
class ValueOrigin:
    source: str
    field: str
    reason: str


@dataclass(frozen=True)
class ResolvedValue:
    value: str
    origin: ValueOrigin


@dataclass(frozen=True)
class OpenShiftRelease:
    ref: str
    version: str
    release_image: str
    installer: str
    rhcos: str
    hypershift: str
    cnv: str
    artifact_bundle: ArtifactReference
    lifecycle: ValidationStatus


@dataclass(frozen=True)
class IEPRelease:
    ref: str
    version: str
    channel: str
    cilium_image: str
    operator_image: str
    clife_image: str
    envoy_image: str
    relay_image: str
    chart_ref: str
    chart_version: str
    chart_digest: str
    clife_manifest_bundle: ArtifactReference
    crd_bundle: ArtifactReference
    artifact_bundle: ArtifactReference
    lifecycle: ValidationStatus


@dataclass(frozen=True)
class CompatibilityDecision:
    openshift_ref: str
    iep_ref: str
    support: SupportStatus
    validation: ValidationStatus
    reason: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ManagementPlatformProfile:
    ref: str
    roles: tuple[str, ...]
    modes: tuple[str, ...]
    openshift_refs: tuple[str, ...]
    iep_refs: tuple[str, ...]
    lifecycle: ValidationStatus
    selection_policy: SelectionPolicy
    components: tuple[str, ...]
    renderer_authorizations: tuple[str, ...]


@dataclass(frozen=True)
class WorkerNetworkProfile:
    ref: str
    attachment: str
    allocator: str
    stage: str
    egress_gateway_placement: str
    bgp_owners: tuple[str, ...]
    validation: ValidationStatus
    selection_policy: SelectionPolicy
    renderer_authorizations: tuple[str, ...]


@dataclass(frozen=True)
class ProfileAuthorizationGate:
    ref: str
    decision: GateDecision
    owner: str
    cluster_ref: str
    profile_kind: str
    profile_ref: str
    intent_state: IntentState
    openshift_ref: str
    iep_ref: str
    decided_by: str
    decided_at: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TransitionAuthorizationGate:
    ref: str
    decision: GateDecision
    owner: str
    transition_ref: str
    cluster_ref: str
    current_openshift_ref: str
    current_iep_ref: str
    target_openshift_ref: str
    target_iep_ref: str
    sequence: tuple[str, ...]
    decided_by: str
    decided_at: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class CatalogBundle:
    openshift_releases: tuple[OpenShiftRelease, ...]
    iep_releases: tuple[IEPRelease, ...]
    compatibility: tuple[CompatibilityDecision, ...]
    management_profiles: tuple[ManagementPlatformProfile, ...]
    worker_profiles: tuple[WorkerNetworkProfile, ...]
    profile_gates: tuple[ProfileAuthorizationGate, ...]
    transition_gates: tuple[TransitionAuthorizationGate, ...]
    compatibility_owner: str
    management_profile_owner: str
    worker_profile_owner: str
    source_digest: str

    def openshift(self, ref: str) -> OpenShiftRelease | None:
        return next((item for item in self.openshift_releases if item.ref == ref), None)

    def iep(self, ref: str) -> IEPRelease | None:
        return next((item for item in self.iep_releases if item.ref == ref), None)

    def pair(self, openshift_ref: str, iep_ref: str) -> CompatibilityDecision | None:
        return next(
            (
                item
                for item in self.compatibility
                if item.openshift_ref == openshift_ref and item.iep_ref == iep_ref
            ),
            None,
        )

    def management(self, ref: str) -> ManagementPlatformProfile | None:
        return next((item for item in self.management_profiles if item.ref == ref), None)

    def worker(self, ref: str) -> WorkerNetworkProfile | None:
        return next((item for item in self.worker_profiles if item.ref == ref), None)

    def profile_gate(self, ref: str) -> ProfileAuthorizationGate | None:
        return next((item for item in self.profile_gates if item.ref == ref), None)

    def transition_gate(self, ref: str) -> TransitionAuthorizationGate | None:
        return next((item for item in self.transition_gates if item.ref == ref), None)


@dataclass(frozen=True)
class ResolvedReleasePair:
    openshift: ResolvedValue
    iep: ResolvedValue
    openshift_release: OpenShiftRelease
    iep_release: IEPRelease
    support: SupportStatus
    validation: ValidationStatus
    reason: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class ResolvedAuthorization:
    name: str
    profile_ref: str
    reason: str


@dataclass(frozen=True)
class ResolvedWorkerAttachment:
    network_ref: str
    guest_default_route: bool
    origin: ValueOrigin
    subnet_ref: str = ""


@dataclass(frozen=True)
class ResolvedHostedClusterIntent:
    name: str
    parent_ref: str
    intent_state: IntentState
    releases: ResolvedReleasePair
    worker_profile: ResolvedValue
    worker_profile_expansion: WorkerNetworkProfile
    worker_profile_validation: ValidationStatus
    worker_profile_selection_policy: SelectionPolicy
    profile_gate_ref: str
    deployment_authorized: bool
    authorization_reason: str
    worker_networks: tuple[ResolvedWorkerAttachment, ...]
    renderer_authorizations: tuple[ResolvedAuthorization, ...]


@dataclass(frozen=True)
class ResolvedTopologyReferences:
    network_catalog: ResolvedValue
    defaults: ResolvedValue | None


@dataclass(frozen=True)
class ResolvedManagementClusterIntent:
    name: str
    intent_state: IntentState
    role: ResolvedValue
    mode: ResolvedValue
    releases: ResolvedReleasePair
    topology_refs: ResolvedTopologyReferences
    management_profile: ResolvedValue
    management_profile_expansion: ManagementPlatformProfile
    profile_validation: ValidationStatus
    profile_gate_ref: str
    deployment_authorized: bool
    authorization_reason: str
    renderer_authorizations: tuple[ResolvedAuthorization, ...]
    hosted_clusters: tuple[ResolvedHostedClusterIntent, ...]


@dataclass(frozen=True)
class ResolvedTransitionIntent:
    name: str
    cluster_ref: str
    axis: str
    current: ResolvedReleasePair
    target: ResolvedReleasePair
    sequence: tuple[str, ...]
    health_gates: tuple[str, ...]
    authorization_gate_ref: str
    rollback_releases: tuple[str, str]
    rollback_method: str


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    source: str
    message: str


@dataclass(frozen=True)
class ResolvedPlatformIntent:
    schema_version: str
    catalog_digest: str
    management_clusters: tuple[ResolvedManagementClusterIntent, ...]
    transitions: tuple[ResolvedTransitionIntent, ...]
    diagnostics: tuple[Diagnostic, ...]
    legacy_adapter_used: bool

    def cluster(self, name: str) -> ResolvedManagementClusterIntent | None:
        return next((item for item in self.management_clusters if item.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return _primitive(self)


def _primitive(value: Any) -> Any:
    """Convert the frozen model into stable, insertion-ordered primitives."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {item.name: _primitive(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, tuple):
        return [_primitive(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _primitive(value[key]) for key in sorted(value)}
    return value
