"""Fail-closed semantic validation for SAIF release and profile intent."""

from __future__ import annotations

from .models import (
    CatalogBundle,
    CompatibilityDecision,
    GateDecision,
    IntentState,
    ManagementPlatformProfile,
    ProfileAuthorizationGate,
    ResolvedReleasePair,
    ResolvedValue,
    SelectionPolicy,
    SupportStatus,
    ValidationStatus,
    ValueOrigin,
    WorkerNetworkProfile,
)


WORKER_PROFILE_REFS = {
    "multus-l2-dhcp",
}

WORKER_PROFILE_SHAPES = {
    "multus-l2-dhcp": ("multus-l2", "central-dhcp", "interim"),
}

WORKER_PROFILE_TOPOLOGIES = {
    "multus-l2-dhcp": ("hosted-worker", ("management", "hosted-worker")),
}

SUPPORTED_ROLE_MODES = {
    ("standalone", "sno"),
    ("standalone", "compact"),
    ("hcp-management", "compact"),
}


class ProfileContractError(ValueError):
    """An authored contract cannot be resolved without guessing."""


def _duplicates(values: list[str]) -> list[str]:
    return sorted({value for value in values if values.count(value) > 1})


def validate_catalogs(catalogs: CatalogBundle) -> None:
    groups = {
        "OpenShift release": [item.ref for item in catalogs.openshift_releases],
        "IEP release": [item.ref for item in catalogs.iep_releases],
        "management profile": [item.ref for item in catalogs.management_profiles],
        "worker profile": [item.ref for item in catalogs.worker_profiles],
        "profile authorization gate": [item.ref for item in catalogs.profile_gates],
        "transition authorization gate": [item.ref for item in catalogs.transition_gates],
    }
    for description, refs in groups.items():
        duplicates = _duplicates(refs)
        if duplicates:
            raise ProfileContractError(f"duplicate {description} ref(s): {', '.join(duplicates)}")

    actual_worker_refs = set(groups["worker profile"])
    if actual_worker_refs != WORKER_PROFILE_REFS:
        missing = sorted(WORKER_PROFILE_REFS - actual_worker_refs)
        extra = sorted(actual_worker_refs - WORKER_PROFILE_REFS)
        raise ProfileContractError(
            "worker profile catalog must contain exactly the staged profile set; "
            f"missing={missing}, extra={extra}"
        )
    for profile in catalogs.worker_profiles:
        actual_shape = (profile.attachment, profile.allocator, profile.stage)
        expected_shape = WORKER_PROFILE_SHAPES[profile.ref]
        if actual_shape != expected_shape:
            raise ProfileContractError(
                f"worker profile {profile.ref!r} must resolve to attachment/allocator/stage "
                f"{expected_shape}, got {actual_shape}"
            )
        actual_topology = (profile.egress_gateway_placement, profile.bgp_owners)
        expected_topology = WORKER_PROFILE_TOPOLOGIES[profile.ref]
        if actual_topology != expected_topology:
            raise ProfileContractError(
                f"worker profile {profile.ref!r} must resolve to "
                f"egressGatewayPlacement/bgpOwners {expected_topology}, got {actual_topology}"
            )

    gate_refs = [item.ref for item in catalogs.profile_gates] + [
        item.ref for item in catalogs.transition_gates
    ]
    duplicate_gate_refs = _duplicates(gate_refs)
    if duplicate_gate_refs:
        raise ProfileContractError(
            f"duplicate authorization gate ref(s): {', '.join(duplicate_gate_refs)}"
        )

    seen_pairs: set[tuple[str, str]] = set()
    for decision in catalogs.compatibility:
        pair = (decision.openshift_ref, decision.iep_ref)
        if pair in seen_pairs:
            raise ProfileContractError(f"duplicate compatibility pair {pair[0]} x {pair[1]}")
        seen_pairs.add(pair)
        if catalogs.openshift(decision.openshift_ref) is None:
            raise ProfileContractError(
                f"compatibility references unknown OpenShift release {decision.openshift_ref!r}"
            )
        if catalogs.iep(decision.iep_ref) is None:
            raise ProfileContractError(
                f"compatibility references unknown IEP release {decision.iep_ref!r}"
            )
        if (
            decision.support == SupportStatus.SUPPORTED
            or decision.validation in {ValidationStatus.SETTLED, ValidationStatus.ACTIVE_TEST}
        ) and not decision.evidence:
            raise ProfileContractError(
                f"compatibility {decision.openshift_ref} x {decision.iep_ref} requires evidence "
                f"for support={decision.support.value}, validation={decision.validation.value}"
            )

    for profile in (*catalogs.management_profiles, *catalogs.worker_profiles):
        if not profile.renderer_authorizations:
            raise ProfileContractError(f"profile {profile.ref!r} has no renderer authorizations")
        if len(set(profile.renderer_authorizations)) != len(profile.renderer_authorizations):
            raise ProfileContractError(f"profile {profile.ref!r} repeats a renderer authorization")

    for profile in catalogs.management_profiles:
        unsupported = sorted(
            (role, mode)
            for role in profile.roles
            for mode in profile.modes
            if (role, mode) not in SUPPORTED_ROLE_MODES
        )
        if unsupported:
            raise ProfileContractError(
                f"management profile {profile.ref!r} contains unsupported role/mode pairs {unsupported}"
            )

    for gate in catalogs.profile_gates:
        if gate.profile_kind == "management-platform-profile":
            expected_owner = catalogs.management_profile_owner
            if catalogs.management(gate.profile_ref) is None:
                raise ProfileContractError(
                    f"profile gate {gate.ref!r} references unknown management profile {gate.profile_ref!r}"
                )
        elif gate.profile_kind == "worker-network-profile":
            expected_owner = catalogs.worker_profile_owner
            if catalogs.worker(gate.profile_ref) is None:
                raise ProfileContractError(
                    f"profile gate {gate.ref!r} references unknown worker profile {gate.profile_ref!r}"
                )
        else:
            raise ProfileContractError(
                f"profile gate {gate.ref!r} has unknown profile kind {gate.profile_kind!r}"
            )
        if gate.owner != expected_owner:
            raise ProfileContractError(
                f"profile gate {gate.ref!r} owner {gate.owner!r} does not match "
                f"{gate.profile_kind} catalogue owner {expected_owner!r}"
            )
        if catalogs.openshift(gate.openshift_ref) is None or catalogs.iep(gate.iep_ref) is None:
            raise ProfileContractError(
                f"profile gate {gate.ref!r} references an unknown release"
            )
        if gate.intent_state != IntentState.ACTIVE_TEST:
            raise ProfileContractError(
                f"profile gate {gate.ref!r} must be scoped to active-test intent"
            )

    for gate in catalogs.transition_gates:
        if gate.owner != catalogs.compatibility_owner:
            raise ProfileContractError(
                f"transition gate {gate.ref!r} owner {gate.owner!r} does not match "
                f"compatibility catalogue owner {catalogs.compatibility_owner!r}"
            )
        for openshift_ref, iep_ref in (
            (gate.current_openshift_ref, gate.current_iep_ref),
            (gate.target_openshift_ref, gate.target_iep_ref),
        ):
            if catalogs.openshift(openshift_ref) is None or catalogs.iep(iep_ref) is None:
                raise ProfileContractError(
                    f"transition gate {gate.ref!r} references an unknown release"
                )
        if gate.sequence not in {("openshift", "iep"), ("iep", "openshift")}:
            raise ProfileContractError(
                f"transition gate {gate.ref!r} must order openshift and iep exactly once"
            )


def resolve_release_pair(
    catalogs: CatalogBundle,
    openshift_ref: str,
    iep_ref: str,
    *,
    openshift_origin: ValueOrigin,
    iep_origin: ValueOrigin,
) -> ResolvedReleasePair:
    openshift_release = catalogs.openshift(openshift_ref)
    if openshift_release is None:
        raise ProfileContractError(f"unknown OpenShift release ref {openshift_ref!r}")
    iep_release = catalogs.iep(iep_ref)
    if iep_release is None:
        raise ProfileContractError(f"unknown IEP release ref {iep_ref!r}")
    decision = catalogs.pair(openshift_ref, iep_ref)
    if decision is None:
        raise ProfileContractError(
            f"OpenShift {openshift_ref!r} x IEP {iep_ref!r} has no compatibility decision"
        )
    _require_selectable_pair(decision)
    if openshift_release.lifecycle == ValidationStatus.INVALID:
        raise ProfileContractError(f"OpenShift release {openshift_ref!r} is invalid")
    if iep_release.lifecycle == ValidationStatus.INVALID:
        raise ProfileContractError(f"IEP release {iep_ref!r} is invalid")
    return ResolvedReleasePair(
        openshift=ResolvedValue(openshift_ref, openshift_origin),
        iep=ResolvedValue(iep_ref, iep_origin),
        openshift_release=openshift_release,
        iep_release=iep_release,
        support=decision.support,
        validation=decision.validation,
        reason=decision.reason,
        evidence=decision.evidence,
    )


def _require_selectable_pair(decision: CompatibilityDecision) -> None:
    if decision.support == SupportStatus.UNSUPPORTED:
        raise ProfileContractError(
            f"OpenShift {decision.openshift_ref!r} x IEP {decision.iep_ref!r} is unsupported: "
            f"{decision.reason}"
        )
    if decision.validation == ValidationStatus.INVALID:
        raise ProfileContractError(
            f"OpenShift {decision.openshift_ref!r} x IEP {decision.iep_ref!r} is invalid: "
            f"{decision.reason}"
        )


def select_management_profile(
    catalogs: CatalogBundle,
    *,
    role: str,
    mode: str,
    openshift_ref: str,
    iep_ref: str,
    explicit_ref: str | None,
    allow_legacy: bool = False,
) -> tuple[ManagementPlatformProfile, str]:
    candidates = [
        profile
        for profile in catalogs.management_profiles
        if role in profile.roles
        and mode in profile.modes
        and (not profile.openshift_refs or openshift_ref in profile.openshift_refs)
        and (not profile.iep_refs or iep_ref in profile.iep_refs)
        and profile.lifecycle != ValidationStatus.INVALID
        and (allow_legacy or profile.selection_policy != SelectionPolicy.LEGACY_ONLY)
    ]
    if explicit_ref:
        profile = catalogs.management(explicit_ref)
        if profile is None:
            raise ProfileContractError(f"unknown managementPlatformProfileRef {explicit_ref!r}")
        if profile not in candidates:
            raise ProfileContractError(
                f"management profile {explicit_ref!r} is incompatible with role {role!r}, "
                f"mode {mode!r}, OpenShift {openshift_ref!r}, or IEP {iep_ref!r}"
            )
        if len(candidates) < 2:
            raise ProfileContractError(
                f"managementPlatformProfileRef {explicit_ref!r} is redundant; role plus releases "
                "already select exactly one composition"
            )
        return profile, "explicit selection among multiple compatible owned compositions"
    if not candidates:
        raise ProfileContractError(
            f"no management profile matches role {role!r}, OpenShift {openshift_ref!r}, "
            f"IEP {iep_ref!r}, and mode {mode!r}"
        )
    if len(candidates) > 1:
        refs = ", ".join(sorted(item.ref for item in candidates))
        raise ProfileContractError(
            f"management composition is ambiguous ({refs}); set managementPlatformProfileRef"
        )
    return candidates[0], "derived because role plus release pair has exactly one owned composition"


def select_worker_profile(
    catalogs: CatalogBundle,
    *,
    ref: str,
    intent_state: IntentState,
) -> WorkerNetworkProfile:
    profile = catalogs.worker(ref)
    if profile is None:
        raise ProfileContractError(f"unknown workerNetworkProfileRef {ref!r}")
    if profile.selection_policy == SelectionPolicy.ACTIVE_TEST_ONLY and intent_state not in {
        IntentState.ACTIVE_TEST,
        IntentState.CANDIDATE,
    }:
        raise ProfileContractError(
            f"worker profile {ref!r} is Active-test only; intentState must be 'active-test'"
        )
    if profile.selection_policy == SelectionPolicy.LEGACY_ONLY and intent_state != IntentState.LEGACY:
        raise ProfileContractError(
            f"worker profile {ref!r} is legacy-only; intentState must be 'legacy'"
        )
    if profile.validation == ValidationStatus.INVALID:
        raise ProfileContractError(f"worker profile {ref!r} is invalid")
    return profile


def _resolve_profile_gate(
    catalogs: CatalogBundle,
    *,
    gate_ref: str,
    cluster_ref: str,
    profile_kind: str,
    profile_ref: str,
    intent_state: IntentState,
    releases: ResolvedReleasePair,
) -> ProfileAuthorizationGate:
    gate = catalogs.profile_gate(gate_ref)
    if gate is None:
        raise ProfileContractError(f"unknown profileGateRef {gate_ref!r}")
    if gate.decision != GateDecision.ACCEPTED:
        raise ProfileContractError(f"profileGateRef {gate_ref!r} is not accepted")
    actual = (
        gate.cluster_ref,
        gate.profile_kind,
        gate.profile_ref,
        gate.intent_state,
        gate.openshift_ref,
        gate.iep_ref,
    )
    expected = (
        cluster_ref,
        profile_kind,
        profile_ref,
        intent_state,
        releases.openshift.value,
        releases.iep.value,
    )
    if actual != expected:
        raise ProfileContractError(
            f"profileGateRef {gate_ref!r} does not match the exact cluster/profile/release/intent scope"
        )
    return gate


def evaluate_profile_authorization(
    catalogs: CatalogBundle,
    *,
    cluster_ref: str,
    profile_kind: str,
    profile_ref: str,
    profile_validation: ValidationStatus,
    selection_policy: SelectionPolicy,
    intent_state: IntentState,
    releases: ResolvedReleasePair,
    profile_gate_ref: str,
) -> tuple[bool, str]:
    """Resolve inspection intent separately from deployment authorization."""
    gate = None
    if profile_gate_ref:
        if intent_state != IntentState.ACTIVE_TEST:
            raise ProfileContractError(
                f"cluster {cluster_ref!r} may set profileGateRef only for active-test intent"
            )
        gate = _resolve_profile_gate(
            catalogs,
            gate_ref=profile_gate_ref,
            cluster_ref=cluster_ref,
            profile_kind=profile_kind,
            profile_ref=profile_ref,
            intent_state=intent_state,
            releases=releases,
        )

    release_states = {
        releases.openshift_release.lifecycle,
        releases.iep_release.lifecycle,
    }
    if intent_state == IntentState.SETTLED:
        if release_states != {ValidationStatus.SETTLED}:
            raise ProfileContractError(
                f"Settled cluster {cluster_ref!r} requires Settled OpenShift and IEP releases"
            )
        if releases.support != SupportStatus.SUPPORTED or releases.validation != ValidationStatus.SETTLED:
            raise ProfileContractError(
                f"Settled cluster {cluster_ref!r} requires a supported, Settled release pair"
            )
        if profile_validation != ValidationStatus.SETTLED or selection_policy != SelectionPolicy.NORMAL:
            raise ProfileContractError(
                f"Settled cluster {cluster_ref!r} cannot select {profile_validation.value}/"
                f"{selection_policy.value} profile {profile_ref!r}"
            )
        return True, "Settled releases, compatibility and profile authorize deployment"

    if intent_state == IntentState.LEGACY:
        if selection_policy != SelectionPolicy.LEGACY_ONLY:
            raise ProfileContractError(
                f"legacy cluster {cluster_ref!r} must select a legacy-only profile"
            )
        return False, "legacy compatibility view is inspection-only until explicit migration"

    if selection_policy == SelectionPolicy.LEGACY_ONLY:
        raise ProfileContractError(
            f"{intent_state.value} cluster {cluster_ref!r} cannot select legacy-only profile {profile_ref!r}"
        )

    if intent_state == IntentState.CANDIDATE:
        return False, "candidate intent resolves for inspection and never authorizes rendering"

    if intent_state != IntentState.ACTIVE_TEST:
        raise ProfileContractError(f"unsupported intent state {intent_state.value!r}")

    eligible_release_states = {ValidationStatus.SETTLED, ValidationStatus.ACTIVE_TEST}
    if not release_states.issubset(eligible_release_states):
        return False, "active-test releases are not yet promoted beyond candidate"
    if releases.validation not in eligible_release_states:
        return False, "release-pair validation is not eligible for active-test authorization"
    if profile_validation not in eligible_release_states:
        return False, "profile validation is not eligible for active-test authorization"
    if gate is None:
        return False, "active-test intent requires an exact accepted profile gate"
    return True, f"active-test authorization accepted by gate {gate.ref}"


def validate_transition_gate(
    catalogs: CatalogBundle,
    *,
    gate_ref: str,
    transition_ref: str,
    cluster_ref: str,
    current: ResolvedReleasePair,
    target: ResolvedReleasePair,
    sequence: tuple[str, ...],
) -> None:
    gate = catalogs.transition_gate(gate_ref)
    if gate is None:
        raise ProfileContractError(f"unknown coordinatedChangeApprovalRef {gate_ref!r}")
    if gate.decision != GateDecision.ACCEPTED:
        raise ProfileContractError(f"coordinatedChangeApprovalRef {gate_ref!r} is not accepted")
    actual = (
        gate.transition_ref,
        gate.cluster_ref,
        gate.current_openshift_ref,
        gate.current_iep_ref,
        gate.target_openshift_ref,
        gate.target_iep_ref,
        gate.sequence,
    )
    expected = (
        transition_ref,
        cluster_ref,
        current.openshift.value,
        current.iep.value,
        target.openshift.value,
        target.iep.value,
        sequence,
    )
    if actual != expected:
        raise ProfileContractError(
            f"coordinatedChangeApprovalRef {gate_ref!r} does not match the exact transition scope"
        )


def transition_axis(
    current: ResolvedReleasePair,
    target: ResolvedReleasePair,
    *,
    coordinated_change_approval_ref: str,
) -> str:
    changed = []
    if current.openshift.value != target.openshift.value:
        changed.append("openshift")
    if current.iep.value != target.iep.value:
        changed.append("iep")
    if not changed:
        raise ProfileContractError("release transition changes neither OpenShift nor IEP")
    if len(changed) > 1 and not coordinated_change_approval_ref:
        raise ProfileContractError(
            "simultaneous OpenShift and IEP change is unvalidated; change one axis or set an "
            "approved coordinatedChangeApprovalRef"
        )
    return "coordinated" if len(changed) > 1 else changed[0]


def validate_transition_steps(
    *,
    axis: str,
    sequence: tuple[str, ...],
    health_gates: tuple[str, ...],
    rollback_method: str,
) -> None:
    if not sequence:
        raise ProfileContractError("release transition sequence is required")
    if axis != "coordinated" and sequence != (axis,):
        raise ProfileContractError(
            f"{axis}-only transition sequence must contain exactly {axis!r}"
        )
    if axis == "coordinated" and sequence not in {
        ("openshift", "iep"),
        ("iep", "openshift"),
    }:
        raise ProfileContractError(
            "approved coordinated transition sequence must order openshift and iep exactly once"
        )
    if not health_gates:
        raise ProfileContractError("release transition requires at least one health gate")
    if not rollback_method:
        raise ProfileContractError("release transition rollback.method is required")
