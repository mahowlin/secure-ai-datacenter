"""Time-bounded adapter from the current combined/toggle contract.

The adapter exists only to keep the maintained tree renderable until the
dedicated migration bead converts it.  It never promotes legacy data presence
to v2 authorization: every such result carries a migration diagnostic.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml

from .models import (
    ArtifactReference,
    CatalogBundle,
    CompatibilityDecision,
    Diagnostic,
    IEPRelease,
    IntentState,
    ManagementPlatformProfile,
    OpenShiftRelease,
    ResolvedAuthorization,
    ResolvedHostedClusterIntent,
    ResolvedManagementClusterIntent,
    ResolvedPlatformIntent,
    ResolvedTopologyReferences,
    ResolvedValue,
    ResolvedWorkerAttachment,
    SCHEMA_VERSION,
    SelectionPolicy,
    SupportStatus,
    ValidationStatus,
    ValueOrigin,
    WorkerNetworkProfile,
)
from .validation import ProfileContractError, resolve_release_pair, select_management_profile, validate_catalogs


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileContractError(f"cannot read legacy input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileContractError(f"legacy input {path} must be a mapping")
    return value


def _stable_ref(prefix: str, version: str) -> str:
    value = version.strip().lower().replace("+", "-").replace("_", "-")
    if not value:
        raise ProfileContractError(f"legacy {prefix} release has no version")
    return f"{prefix}-{value}"


def _add_unique(items: dict[str, Any], ref: str, value: Any, description: str) -> None:
    previous = items.get(ref)
    if previous is not None and previous != value:
        raise ProfileContractError(
            f"legacy combined releases map {ref!r} to conflicting {description} records"
        )
    items[ref] = value


def _legacy_required(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ProfileContractError(f"legacy {path}.{key} is required")
    return value


def _legacy_catalogs(root: Path) -> tuple[CatalogBundle, dict[str, tuple[str, str]]]:
    release_path = root / "platform-release.yaml"
    release_data = _read_yaml(release_path)
    releases = release_data.get("releases")
    if not isinstance(releases, dict) or not releases:
        raise ProfileContractError("legacy platform-release.yaml has no named releases")

    openshift: dict[str, OpenShiftRelease] = {}
    iep: dict[str, IEPRelease] = {}
    pairs = []
    combined_refs: dict[str, tuple[str, str]] = {}
    for combined_ref, profile in sorted(releases.items()):
        if not isinstance(profile, dict):
            raise ProfileContractError(f"legacy release {combined_ref!r} must be a mapping")
        os_data = profile.get("openshift", {}) or {}
        iep_data = profile.get("cilium", {}) or {}
        if not isinstance(os_data, dict) or not isinstance(iep_data, dict):
            raise ProfileContractError(f"legacy release {combined_ref!r} release axes must be mappings")
        os_version = _legacy_required(os_data, "version", f"release {combined_ref}.openshift")
        iep_version = _legacy_required(iep_data, "version", f"release {combined_ref}.cilium")
        os_ref = _stable_ref("ocp", os_version)
        iep_ref = _stable_ref("iep", iep_version)
        os_record = OpenShiftRelease(
            ref=os_ref,
            version=os_version,
            release_image=f"unresolved://legacy/{os_ref}",
            installer=_legacy_required(os_data, "installer", f"release {combined_ref}.openshift"),
            rhcos=_legacy_required(os_data, "rhcos", f"release {combined_ref}.openshift"),
            hypershift="legacy-unresolved",
            cnv="legacy-unresolved",
            artifact_bundle=ArtifactReference(
                ref=_legacy_required(
                    os_data, "image_policy_bundle", f"release {combined_ref}.openshift"
                ),
                digest="legacy-unresolved",
            ),
            lifecycle=ValidationStatus.CANDIDATE,
        )
        iep_record = IEPRelease(
            ref=iep_ref,
            version=iep_version,
            channel=_legacy_required(iep_data, "channel", f"release {combined_ref}.cilium"),
            cilium_image=_legacy_required(iep_data, "image", f"release {combined_ref}.cilium"),
            operator_image=_legacy_required(
                iep_data, "operator_image", f"release {combined_ref}.cilium"
            ),
            clife_image=_legacy_required(
                iep_data, "clife_image", f"release {combined_ref}.cilium"
            ),
            envoy_image="legacy-unresolved",
            relay_image="legacy-unresolved",
            chart_ref="legacy-unresolved",
            chart_version=iep_version,
            chart_digest="legacy-unresolved",
            clife_manifest_bundle=ArtifactReference(
                ref=str(iep_data.get("clife_manifest_bundle", "")) or "legacy-unresolved",
                digest="legacy-unresolved",
            ),
            crd_bundle=ArtifactReference(ref="legacy-unresolved", digest="legacy-unresolved"),
            artifact_bundle=ArtifactReference(
                ref=_legacy_required(
                    iep_data, "image_policy_bundle", f"release {combined_ref}.cilium"
                ),
                digest="legacy-unresolved",
            ),
            lifecycle=ValidationStatus.CANDIDATE,
        )
        _add_unique(openshift, os_ref, os_record, "OpenShift")
        _add_unique(iep, iep_ref, iep_record, "IEP")
        combined_refs[str(combined_ref)] = (os_ref, iep_ref)
        pairs.append(CompatibilityDecision(
            openshift_ref=os_ref,
            iep_ref=iep_ref,
            support=SupportStatus.UNKNOWN,
            validation=ValidationStatus.CANDIDATE,
            reason=f"legacy combined selector {combined_ref}; support and lab validation are not separated",
            evidence=(f"{release_path}:releases.{combined_ref}",),
        ))

    management_profiles = (
        ManagementPlatformProfile(
            ref="legacy-standalone",
            roles=("standalone",),
            modes=("sno", "compact"),
            openshift_refs=(),
            iep_refs=(),
            lifecycle=ValidationStatus.CANDIDATE,
            selection_policy=SelectionPolicy.LEGACY_ONLY,
            components=("openshift",),
            renderer_authorizations=("render.aci", "render.ucs", "render.ocp"),
        ),
        ManagementPlatformProfile(
            ref="legacy-hcp-management",
            roles=("hcp-management",),
            modes=("compact",),
            openshift_refs=(),
            iep_refs=(),
            lifecycle=ValidationStatus.CANDIDATE,
            selection_policy=SelectionPolicy.LEGACY_ONLY,
            components=("openshift", "cnv", "mce", "nmstate", "hypershift", "clife"),
            renderer_authorizations=(
                "render.aci",
                "render.ucs",
                "render.ocp",
                "render.gitops.hcp-management",
            ),
        ),
    )
    worker_profiles = (
        WorkerNetworkProfile(
            ref="multus-l2-dhcp",
            attachment="multus-l2",
            allocator="central-dhcp",
            stage="interim",
            egress_gateway_placement="hosted-worker",
            bgp_owners=("management", "hosted-worker"),
            validation=ValidationStatus.ACTIVE_TEST,
            selection_policy=SelectionPolicy.ACTIVE_TEST_ONLY,
            renderer_authorizations=("render.hcp.multus-nad", "render.hcp.worker-dhcp"),
        ),
    )
    digest_input = {
        "legacyReleaseData": release_data.get("releases"),
        "adapterWorkerProfiles": [item.ref for item in worker_profiles],
    }
    digest = hashlib.sha256(
        json.dumps(digest_input, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    bundle = CatalogBundle(
        openshift_releases=tuple(openshift.values()),
        iep_releases=tuple(iep.values()),
        compatibility=tuple(pairs),
        management_profiles=management_profiles,
        worker_profiles=worker_profiles,
        profile_gates=(),
        transition_gates=(),
        compatibility_owner="platform-architecture",
        management_profile_owner="platform-architecture",
        worker_profile_owner="hcp-worker-networking",
        source_digest=digest,
    )
    validate_catalogs(bundle)
    return bundle, combined_refs


def _legacy_role(features: Any, path: Path) -> str:
    if not isinstance(features, dict):
        raise ProfileContractError(f"{path}: spec.features must be a mapping for legacy adaptation")
    unknown = sorted(set(features) - {"hostedControlPlanes", "KubeVirt"})
    if unknown:
        raise ProfileContractError(f"{path}: unknown legacy feature(s): {', '.join(unknown)}")
    hcp = bool(features.get("hostedControlPlanes", False))
    kubevirt = bool(features.get("KubeVirt", False))
    if hcp != kubevirt:
        raise ProfileContractError(
            f"{path}: hostedControlPlanes={hcp} and KubeVirt={kubevirt} are ambiguous; migrate to spec.role"
        )
    return "hcp-management" if hcp else "standalone"


def _legacy_worker_profile(
    *,
    parent: dict[str, Any],
    hcp_path: Path,
    hcp_spec: dict[str, Any],
    network_sources: dict[str, set[tuple[str, str]]],
    worker_profiles: dict[str, WorkerNetworkProfile],
) -> tuple[str, IntentState, list[dict[str, Any]], bool]:
    explicit_ref = hcp_spec.get("workerNetworkProfileRef")
    if explicit_ref is not None and (not isinstance(explicit_ref, str) or not explicit_ref):
        raise ProfileContractError(f"{hcp_path}: spec.workerNetworkProfileRef must be a non-empty string")
    explicit_profile = worker_profiles.get(explicit_ref) if explicit_ref else None
    if explicit_ref and explicit_profile is None:
        raise ProfileContractError(f"{hcp_path}: unknown workerNetworkProfileRef {explicit_ref!r}")
    networking = hcp_spec.get("networking", {}) or {}
    entries = networking.get("workerNetworks")
    if not isinstance(entries, list) or not entries:
        raise ProfileContractError(f"{hcp_path}: workerNetworks must be a non-empty list")
    normalized = []
    attachment_kinds = set()
    allocators = set()
    for index, entry in enumerate(entries):
        if isinstance(entry, str):
            network_ref = entry
            guest_default = index == 0
            subnet_ref = ""
        elif isinstance(entry, dict):
            network_ref = str(entry.get("networkRef", ""))
            guest_default = bool(entry.get("guestDefaultRoute", False))
            subnet_ref = str(entry.get("subnetRef", ""))
        else:
            raise ProfileContractError(f"{hcp_path}: invalid workerNetworks[{index}]")
        source_shapes = network_sources.get(network_ref)
        if not network_ref or source_shapes is None:
            raise ProfileContractError(f"{hcp_path}: unknown worker network {network_ref!r}")
        if explicit_profile is not None:
            source_shape = (explicit_profile.attachment, explicit_profile.allocator)
            if source_shape not in source_shapes:
                raise ProfileContractError(
                    f"{hcp_path}: workerNetworkProfileRef {explicit_ref!r} requires "
                    f"{source_shape!r} input for {network_ref!r}, got {sorted(source_shapes)}"
                )
        else:
            if len(source_shapes) != 1:
                raise ProfileContractError(
                    f"{hcp_path}: worker network {network_ref!r} has multiple implementations; "
                    "workerNetworkProfileRef must select one"
                )
            source_shape = next(iter(source_shapes))
        attachment_kind, allocator = source_shape
        attachment_kinds.add(attachment_kind)
        allocators.add(allocator)
        normalized.append({
            "networkRef": network_ref,
            "guestDefaultRoute": guest_default,
            **({"subnetRef": subnet_ref} if subnet_ref else {}),
        })
    if len(attachment_kinds) != 1:
        raise ProfileContractError(
            f"{hcp_path}: legacy worker networks mix attachment kinds {sorted(attachment_kinds)}"
        )
    if len(allocators) != 1:
        raise ProfileContractError(
            f"{hcp_path}: legacy worker networks mix allocators {sorted(allocators)}; "
            "migration must select one per-HCP profile"
        )
    if len(normalized) != 1:
        raise ProfileContractError(
            f"{hcp_path}: single-NIC worker profiles require exactly one workload attachment"
        )
    if sum(bool(item["guestDefaultRoute"]) for item in normalized) != 1:
        raise ProfileContractError(
            f"{hcp_path}: legacy worker networks do not resolve to exactly one default-producing attachment"
        )
    if explicit_ref:
        profile = explicit_profile
        assert profile is not None
        if {profile.attachment} != attachment_kinds:
            raise ProfileContractError(
                f"{hcp_path}: workerNetworkProfileRef {explicit_ref!r} requires "
                f"{profile.attachment!r} input, got {sorted(attachment_kinds)}"
            )
        if {profile.allocator} != allocators:
            raise ProfileContractError(
                f"{hcp_path}: workerNetworkProfileRef {explicit_ref!r} requires allocator "
                f"{profile.allocator!r}, got {sorted(allocators)}"
            )
        intent_state = (
            IntentState.ACTIVE_TEST
            if profile.validation == ValidationStatus.ACTIVE_TEST
            else IntentState.LEGACY
            if profile.stage == "legacy"
            else IntentState.CANDIDATE
        )
        return explicit_ref, intent_state, normalized, True
    candidates = [
        profile for profile in worker_profiles.values()
        if {profile.attachment} == attachment_kinds and {profile.allocator} == allocators
    ]
    if len(candidates) != 1:
        raise ProfileContractError(
            f"{hcp_path}: worker network facts resolve {len(candidates)} profile candidates"
        )
    profile = candidates[0]
    intent_state = IntentState.LEGACY if profile.stage == "legacy" else IntentState.ACTIVE_TEST
    return profile.ref, intent_state, normalized, False


def resolve_legacy_root(root: Path) -> ResolvedPlatformIntent:
    catalogs, combined_refs = _legacy_catalogs(root)
    network_files = sorted((root / "data" / "networks").glob("*.yaml"))
    if not network_files:
        raise ProfileContractError("legacy adapter found no NetworkCatalog")
    network_data = _read_yaml(network_files[0])
    network_spec = network_data.get("spec", {}) or {}
    tenant_networks = network_spec.get("tenantNetworks", {}) or {}
    network_sources: dict[str, set[tuple[str, str]]] = {}
    for network_ref, network in tenant_networks.items():
        subnets = (network or {}).get("subnets", {}) or {}
        workers = (subnets or {}).get("workers", {}) or {}
        dhcp = workers.get("dhcp", False)
        network_sources.setdefault(str(network_ref), set()).add((
            "multus-l2",
            "central-dhcp" if dhcp is True or isinstance(dhcp, dict) else "whereabouts",
        ))
    worker_profiles = {profile.ref: profile for profile in catalogs.worker_profiles}

    management_clusters = []
    diagnostics = []
    cluster_root = root / "data" / "clusters"
    for cluster_dir in sorted(path for path in cluster_root.iterdir() if path.is_dir()):
        cluster_path = cluster_dir / "cluster.yaml"
        if not cluster_path.is_file():
            continue
        document = _read_yaml(cluster_path)
        if document.get("kind") != "OCPCluster":
            continue
        spec = document.get("spec", {}) or {}
        cluster_source = cluster_path.relative_to(root).as_posix()
        name = str((document.get("metadata", {}) or {}).get("name", cluster_dir.name))
        role = _legacy_role(spec.get("features", {}), cluster_path)
        mode = str(spec.get("mode", ""))
        combined_ref = str(spec.get("releaseRef", ""))
        pair_refs = combined_refs.get(combined_ref)
        if pair_refs is None:
            raise ProfileContractError(f"{cluster_path}: unknown legacy releaseRef {combined_ref!r}")
        os_ref, iep_ref = pair_refs
        releases = resolve_release_pair(
            catalogs,
            os_ref,
            iep_ref,
            openshift_origin=ValueOrigin(
                cluster_source, "spec.releaseRef", f"adapted OpenShift axis from combined {combined_ref}"
            ),
            iep_origin=ValueOrigin(
                cluster_source, "spec.releaseRef", f"adapted IEP axis from combined {combined_ref}"
            ),
        )
        capabilities = spec.get("installConfig", {}).get("capabilities", {})
        if capabilities:
            if not isinstance(capabilities, dict):
                raise ProfileContractError(f"{cluster_path}: legacy capabilities must be a mapping")
            unknown = sorted(set(capabilities) - {"disconnectedCatalogs", "mdadm"})
            if unknown:
                raise ProfileContractError(
                    f"{cluster_path}: unknown generic capabilities cannot be adapted: {', '.join(unknown)}"
                )
        profile, profile_reason = select_management_profile(
            catalogs,
            role=role,
            mode=mode,
            openshift_ref=os_ref,
            iep_ref=iep_ref,
            explicit_ref=None,
            allow_legacy=True,
        )

        hosted_clusters = []
        hcp_dir = cluster_dir / "hcpclusters"
        if hcp_dir.is_dir():
            for hcp_path in sorted(hcp_dir.glob("*.yaml")):
                hcp_doc = _read_yaml(hcp_path)
                if hcp_doc.get("kind") != "HCPCluster":
                    continue
                hcp_spec = hcp_doc.get("spec", {}) or {}
                hcp_source = hcp_path.relative_to(root).as_posix()
                hcp_name = str((hcp_doc.get("metadata", {}) or {}).get("name", hcp_path.stem))
                worker_ref, intent_state, entries, explicit_worker_ref = _legacy_worker_profile(
                    parent=document,
                    hcp_path=hcp_path,
                    hcp_spec=hcp_spec,
                    network_sources=network_sources,
                    worker_profiles=worker_profiles,
                )
                worker_profile = catalogs.worker(worker_ref)
                if worker_profile is None:
                    raise ProfileContractError(f"legacy adapter has no worker profile {worker_ref!r}")
                hosted_clusters.append(ResolvedHostedClusterIntent(
                    name=hcp_name,
                    parent_ref=name,
                    intent_state=intent_state,
                    releases=resolve_release_pair(
                        catalogs,
                        os_ref,
                        iep_ref,
                        openshift_origin=ValueOrigin(
                            hcp_source, "derived:parent.releaseRef", f"inherited from parent {name}"
                        ),
                        iep_origin=ValueOrigin(
                            hcp_source, "derived:parent.releaseRef", f"inherited from parent {name}"
                        ),
                    ),
                    worker_profile=ResolvedValue(
                        worker_ref,
                        ValueOrigin(
                            hcp_source,
                            "spec.workerNetworkProfileRef" if explicit_worker_ref else "derived:workerNetworks+NetworkCatalog",
                            (
                                "explicit per-HCP worker realization in the maintained contract"
                                if explicit_worker_ref
                                else "legacy compatibility mapping; data presence is not v2 authorization"
                            ),
                        ),
                    ),
                    worker_profile_expansion=worker_profile,
                    worker_profile_validation=worker_profile.validation,
                    worker_profile_selection_policy=worker_profile.selection_policy,
                    profile_gate_ref="",
                    deployment_authorized=False,
                    authorization_reason=(
                        "legacy compatibility view is inspection-only until explicit migration"
                    ),
                    worker_networks=tuple(
                        ResolvedWorkerAttachment(
                            network_ref=str(entry["networkRef"]),
                            guest_default_route=bool(entry["guestDefaultRoute"]),
                            subnet_ref=str(entry.get("subnetRef", "")),
                            origin=ValueOrigin(
                                hcp_source,
                                f"spec.networking.workerNetworks[{index}]",
                                "factual logical worker attachment",
                            ),
                        )
                        for index, entry in enumerate(entries)
                    ),
                    renderer_authorizations=tuple(
                        ResolvedAuthorization(
                            name=authorization,
                            profile_ref=worker_ref,
                            reason="legacy compatibility view only; migrate before profile authorization",
                        )
                        for authorization in ()
                    ),
                ))
                diagnostics.append(Diagnostic(
                    code="legacy.worker-profile-explicit" if explicit_worker_ref else "legacy.worker-profile-derived",
                    severity="info" if explicit_worker_ref else "warning",
                    source=hcp_source,
                    message=(
                        f"explicit spec.workerNetworkProfileRef={worker_ref!r} owns worker realization"
                        if explicit_worker_ref
                        else (
                            f"migrate to spec.workerNetworkProfileRef={worker_ref!r}; legacy network data "
                            "does not grant v2 authorization"
                        )
                    ),
                ))

        management_clusters.append(ResolvedManagementClusterIntent(
            name=name,
            intent_state=IntentState.LEGACY,
            role=ResolvedValue(
                role,
                ValueOrigin(cluster_source, "spec.features", "unambiguous legacy role mapping"),
            ),
            mode=ResolvedValue(
                mode,
                ValueOrigin(cluster_source, "spec.mode", "legacy explicit topology mode"),
            ),
            releases=releases,
            topology_refs=ResolvedTopologyReferences(
                network_catalog=ResolvedValue(
                    str((network_data.get("metadata", {}) or {}).get("name", network_files[0].stem)),
                    ValueOrigin(
                        network_files[0].relative_to(root).as_posix(),
                        "metadata.name",
                        "legacy factual network catalog reference",
                    ),
                ),
                defaults=ResolvedValue(
                    "legacy:data/defaults",
                    ValueOrigin(
                        "data/defaults",
                        "derived:legacy-defaults-directory",
                        "legacy compatibility reference; migrate to spec.topologyRefs.defaults",
                    ),
                ),
            ),
            management_profile=ResolvedValue(
                profile.ref,
                ValueOrigin(cluster_source, "derived:role+releaseRef", profile_reason),
            ),
            management_profile_expansion=profile,
            profile_validation=profile.lifecycle,
            profile_gate_ref="",
            deployment_authorized=False,
            authorization_reason=(
                "legacy compatibility view is inspection-only until explicit migration"
            ),
            renderer_authorizations=tuple(
                ResolvedAuthorization(
                    name=authorization,
                    profile_ref=profile.ref,
                    reason="legacy compatibility view only; migrate before profile authorization",
                )
                for authorization in ()
            ),
            hosted_clusters=tuple(hosted_clusters),
        ))
        diagnostics.extend([
            Diagnostic(
                code="legacy.combined-release-ref",
                severity="warning",
                source=cluster_source,
                message=(
                    f"migrate releaseRef {combined_ref!r} to independent releaseRefs.openshift={os_ref!r} "
                    f"and releaseRefs.iep={iep_ref!r}"
                ),
            ),
            Diagnostic(
                code="legacy.features-map",
                severity="warning",
                source=cluster_source,
                message=f"migrate spec.features to spec.role={role!r}; KubeVirt is not a selectable capability",
            ),
        ])
        if capabilities:
            diagnostics.append(Diagnostic(
                code="legacy.generic-capabilities",
                severity="warning",
                source=cluster_source,
                message="remove installConfig.capabilities; the complete management composition owns it",
            ))

    return ResolvedPlatformIntent(
        schema_version=SCHEMA_VERSION,
        catalog_digest=catalogs.source_digest,
        management_clusters=tuple(management_clusters),
        transitions=(),
        diagnostics=tuple(diagnostics),
        legacy_adapter_used=True,
    )
