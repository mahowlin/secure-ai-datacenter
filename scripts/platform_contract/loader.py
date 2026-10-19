"""Load versioned SAIF catalogs and resolve authored selectors."""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, TypeVar

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import best_match

from .models import (
    ArtifactReference,
    CatalogBundle,
    CompatibilityDecision,
    Diagnostic,
    GateDecision,
    IEPRelease,
    IntentState,
    ManagementPlatformProfile,
    OpenShiftRelease,
    ProfileAuthorizationGate,
    ResolvedAuthorization,
    ResolvedHostedClusterIntent,
    ResolvedManagementClusterIntent,
    ResolvedPlatformIntent,
    ResolvedTopologyReferences,
    ResolvedTransitionIntent,
    ResolvedValue,
    ResolvedWorkerAttachment,
    SCHEMA_VERSION,
    SelectionPolicy,
    SupportStatus,
    TransitionAuthorizationGate,
    ValidationStatus,
    ValueOrigin,
    WorkerNetworkProfile,
)
from .validation import (
    ProfileContractError,
    evaluate_profile_authorization,
    resolve_release_pair,
    select_management_profile,
    select_worker_profile,
    transition_axis,
    validate_catalogs,
    validate_transition_gate,
    validate_transition_steps,
)


TEnum = TypeVar("TEnum")

CATALOG_FILES = {
    "openshift": "openshift-releases.yaml",
    "iep": "iep-releases.yaml",
    "compatibility": "release-compatibility.yaml",
    "management": "management-platform-profiles.yaml",
    "worker": "worker-network-profiles.yaml",
    "authorization": "authorization-gates.yaml",
}

SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "saif-platform-v2.schema.json"
SCHEMA_DEFINITION_BY_KIND = {
    "ManagementCluster": "managementCluster",
    "HostedCluster": "hostedCluster",
    "ReleaseTransition": "releaseTransition",
    "OpenShiftReleaseCatalog": "openShiftReleaseCatalog",
    "IEPReleaseCatalog": "iepReleaseCatalog",
    "ReleaseCompatibilityCatalog": "releaseCompatibilityCatalog",
    "ManagementPlatformProfileCatalog": "managementProfileCatalog",
    "WorkerNetworkProfileCatalog": "workerProfileCatalog",
    "AuthorizationGateCatalog": "authorizationGateCatalog",
}

FORBIDDEN_V2_KEYS = {
    "capabilities",
    "features",
    "releaseRef",
    "currentReleaseRef",
    "targetReleaseRef",
    "workerProfile",
}


def _expect_keys(data: dict[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ProfileContractError(f"{path} has unknown field(s): {', '.join(unknown)}")


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileContractError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProfileContractError(f"{path}: document must be a mapping")
    return value


@lru_cache(maxsize=1)
def _schema_validator() -> Draft202012Validator:
    try:
        schema = json.loads(SCHEMA_PATH.read_text())
        Draft202012Validator.check_schema(schema)
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileContractError(f"cannot load published schema {SCHEMA_PATH}: {exc}") from exc
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _validate_document_schema(document: dict[str, Any], path: Path) -> None:
    validator = _schema_validator()
    definition = SCHEMA_DEFINITION_BY_KIND.get(document.get("kind"))
    candidate = (
        validator.evolve(schema=validator.schema["$defs"][definition])
        if definition is not None
        else validator
    )
    error = best_match(candidate.iter_errors(document))
    if error is None:
        return
    location = ".".join(str(item) for item in error.absolute_path) or "<document>"
    message = error.message
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        unknown = sorted(set(error.instance) - allowed)
        message = f"unknown field(s): {', '.join(unknown)}"
    raise ProfileContractError(f"{path}: schema validation failed at {location}: {message}")


def _enum(enum_type: type[TEnum], value: Any, path: str) -> TEnum:
    try:
        return enum_type(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in enum_type)  # type: ignore[attr-defined]
        raise ProfileContractError(f"{path} must be one of: {allowed}") from exc


def _strings(value: Any, path: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if value is None and allow_empty:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProfileContractError(f"{path} must be a list of non-empty strings")
    return tuple(value)


def _required_string(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ProfileContractError(f"{path}.{key} is required")
    return value


def _required_bool(data: dict[str, Any], key: str, path: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ProfileContractError(f"{path}.{key} must be a boolean")
    return value


def _optional_string(data: dict[str, Any], key: str, path: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ProfileContractError(f"{path}.{key} must be a string")
    return value


def _artifact_reference(data: dict[str, Any], key: str, path: str) -> ArtifactReference:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ProfileContractError(f"{path}.{key} must be an immutable artifact reference")
    _expect_keys(value, {"ref", "digest"}, f"{path}.{key}")
    return ArtifactReference(
        ref=_required_string(value, "ref", f"{path}.{key}"),
        digest=_required_string(value, "digest", f"{path}.{key}"),
    )


def _check_schema(document: dict[str, Any], path: Path, kind: str) -> None:
    if document.get("schemaVersion") != SCHEMA_VERSION:
        raise ProfileContractError(
            f"{path}: schemaVersion must be {SCHEMA_VERSION!r}"
        )
    if document.get("kind") != kind:
        raise ProfileContractError(f"{path}: kind must be {kind!r}")


def _forbid_legacy_keys(value: Any, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_V2_KEYS:
                raise ProfileContractError(
                    f"{path}.{key} is a legacy toggle/selector and is forbidden by the v2 contract"
                )
            _forbid_legacy_keys(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _forbid_legacy_keys(child, f"{path}[{index}]")


def _catalog_digest(documents: list[dict[str, Any]]) -> str:
    canonical = json.dumps(documents, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def load_catalogs(catalog_dir: Path) -> CatalogBundle:
    paths = {name: catalog_dir / filename for name, filename in CATALOG_FILES.items()}
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise ProfileContractError(f"missing profile catalog file(s): {', '.join(missing)}")
    docs = {name: _read_yaml(path) for name, path in paths.items()}
    for name, document in docs.items():
        _validate_document_schema(document, paths[name])

    _check_schema(docs["openshift"], paths["openshift"], "OpenShiftReleaseCatalog")
    _check_schema(docs["iep"], paths["iep"], "IEPReleaseCatalog")
    _check_schema(docs["compatibility"], paths["compatibility"], "ReleaseCompatibilityCatalog")
    _check_schema(docs["management"], paths["management"], "ManagementPlatformProfileCatalog")
    _check_schema(docs["worker"], paths["worker"], "WorkerNetworkProfileCatalog")
    _check_schema(docs["authorization"], paths["authorization"], "AuthorizationGateCatalog")

    list_fields = {
        "openshift": "releases",
        "iep": "releases",
        "compatibility": "pairs",
        "management": "profiles",
        "worker": "profiles",
    }
    for name, list_field in list_fields.items():
        _expect_keys(
            docs[name],
            {"schemaVersion", "kind", "ownership", list_field},
            str(paths[name]),
        )
        ownership = docs[name].get("ownership")
        if not isinstance(ownership, dict):
            raise ProfileContractError(f"{paths[name]}: ownership must be a mapping")
        _expect_keys(ownership, {"owner", "purpose"}, f"{paths[name]}:ownership")
        _required_string(ownership, "owner", f"{paths[name]}:ownership")
        _required_string(ownership, "purpose", f"{paths[name]}:ownership")

    _expect_keys(
        docs["authorization"],
        {"schemaVersion", "kind", "ownership", "profileGates", "transitionGates"},
        str(paths["authorization"]),
    )
    authorization_ownership = docs["authorization"].get("ownership")
    if not isinstance(authorization_ownership, dict):
        raise ProfileContractError(f"{paths['authorization']}: ownership must be a mapping")
    _expect_keys(
        authorization_ownership,
        {"owner", "purpose"},
        f"{paths['authorization']}:ownership",
    )
    _required_string(authorization_ownership, "owner", f"{paths['authorization']}:ownership")
    _required_string(authorization_ownership, "purpose", f"{paths['authorization']}:ownership")

    openshift_releases = []
    for index, item in enumerate(docs["openshift"].get("releases", [])):
        path = f"{paths['openshift']}:releases[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {"ref", "version", "releaseImage", "installer", "rhcos", "constraints", "artifactBundle", "lifecycle"},
            path,
        )
        constraints = item.get("constraints", {}) or {}
        if not isinstance(constraints, dict):
            raise ProfileContractError(f"{path}.constraints must be a mapping")
        _expect_keys(constraints, {"hypershift", "cnv"}, f"{path}.constraints")
        openshift_releases.append(OpenShiftRelease(
            ref=_required_string(item, "ref", path),
            version=_required_string(item, "version", path),
            release_image=_required_string(item, "releaseImage", path),
            installer=_required_string(item, "installer", path),
            rhcos=_required_string(item, "rhcos", path),
            hypershift=_required_string(constraints, "hypershift", f"{path}.constraints"),
            cnv=_required_string(constraints, "cnv", f"{path}.constraints"),
            artifact_bundle=_artifact_reference(item, "artifactBundle", path),
            lifecycle=_enum(ValidationStatus, item.get("lifecycle"), f"{path}.lifecycle"),
        ))

    iep_releases = []
    for index, item in enumerate(docs["iep"].get("releases", [])):
        path = f"{paths['iep']}:releases[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {"ref", "version", "channel", "images", "chart", "clifeManifestBundle", "crdBundle", "artifactBundle", "lifecycle"},
            path,
        )
        images = item.get("images", {}) or {}
        if not isinstance(images, dict):
            raise ProfileContractError(f"{path}.images must be a mapping")
        _expect_keys(images, {"cilium", "operator", "clife", "envoy", "relay"}, f"{path}.images")
        chart = item.get("chart")
        if not isinstance(chart, dict):
            raise ProfileContractError(f"{path}.chart must be an immutable chart reference")
        _expect_keys(chart, {"ref", "version", "digest"}, f"{path}.chart")
        iep_releases.append(IEPRelease(
            ref=_required_string(item, "ref", path),
            version=_required_string(item, "version", path),
            channel=_required_string(item, "channel", path),
            cilium_image=_required_string(images, "cilium", f"{path}.images"),
            operator_image=_required_string(images, "operator", f"{path}.images"),
            clife_image=_required_string(images, "clife", f"{path}.images"),
            envoy_image=_required_string(images, "envoy", f"{path}.images"),
            relay_image=_required_string(images, "relay", f"{path}.images"),
            chart_ref=_required_string(chart, "ref", f"{path}.chart"),
            chart_version=_required_string(chart, "version", f"{path}.chart"),
            chart_digest=_required_string(chart, "digest", f"{path}.chart"),
            clife_manifest_bundle=_artifact_reference(item, "clifeManifestBundle", path),
            crd_bundle=_artifact_reference(item, "crdBundle", path),
            artifact_bundle=_artifact_reference(item, "artifactBundle", path),
            lifecycle=_enum(ValidationStatus, item.get("lifecycle"), f"{path}.lifecycle"),
        ))

    compatibility = []
    for index, item in enumerate(docs["compatibility"].get("pairs", [])):
        path = f"{paths['compatibility']}:pairs[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {"openshiftRef", "iepRef", "support", "validation", "reason", "evidence"},
            path,
        )
        compatibility.append(CompatibilityDecision(
            openshift_ref=_required_string(item, "openshiftRef", path),
            iep_ref=_required_string(item, "iepRef", path),
            support=_enum(SupportStatus, item.get("support"), f"{path}.support"),
            validation=_enum(ValidationStatus, item.get("validation"), f"{path}.validation"),
            reason=_required_string(item, "reason", path),
            evidence=_strings(item.get("evidence", []), f"{path}.evidence", allow_empty=True),
        ))

    management_profiles = []
    for index, item in enumerate(docs["management"].get("profiles", [])):
        path = f"{paths['management']}:profiles[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {"ref", "roles", "modes", "openshiftRefs", "iepRefs", "lifecycle", "selectionPolicy", "components", "rendererAuthorizations"},
            path,
        )
        management_profiles.append(ManagementPlatformProfile(
            ref=_required_string(item, "ref", path),
            roles=_strings(item.get("roles"), f"{path}.roles"),
            modes=_strings(item.get("modes"), f"{path}.modes"),
            openshift_refs=_strings(item.get("openshiftRefs", []), f"{path}.openshiftRefs", allow_empty=True),
            iep_refs=_strings(item.get("iepRefs", []), f"{path}.iepRefs", allow_empty=True),
            lifecycle=_enum(ValidationStatus, item.get("lifecycle"), f"{path}.lifecycle"),
            selection_policy=_enum(SelectionPolicy, item.get("selectionPolicy", "normal"), f"{path}.selectionPolicy"),
            components=_strings(item.get("components"), f"{path}.components"),
            renderer_authorizations=_strings(
                item.get("rendererAuthorizations"), f"{path}.rendererAuthorizations"
            ),
        ))

    worker_profiles = []
    for index, item in enumerate(docs["worker"].get("profiles", [])):
        path = f"{paths['worker']}:profiles[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {
                "ref", "attachment", "allocator", "stage", "egressGatewayPlacement",
                "bgpOwners", "validation", "selectionPolicy", "rendererAuthorizations",
            },
            path,
        )
        worker_profiles.append(WorkerNetworkProfile(
            ref=_required_string(item, "ref", path),
            attachment=_required_string(item, "attachment", path),
            allocator=_required_string(item, "allocator", path),
            stage=_required_string(item, "stage", path),
            egress_gateway_placement=_required_string(item, "egressGatewayPlacement", path),
            bgp_owners=_strings(item.get("bgpOwners"), f"{path}.bgpOwners"),
            validation=_enum(ValidationStatus, item.get("validation"), f"{path}.validation"),
            selection_policy=_enum(SelectionPolicy, item.get("selectionPolicy"), f"{path}.selectionPolicy"),
            renderer_authorizations=_strings(
                item.get("rendererAuthorizations"), f"{path}.rendererAuthorizations"
            ),
        ))

    profile_gates = []
    for index, item in enumerate(docs["authorization"].get("profileGates", [])):
        path = f"{paths['authorization']}:profileGates[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {
                "ref", "decision", "owner", "clusterRef", "profileKind", "profileRef",
                "intentState", "releaseRefs", "decidedBy", "decidedAt", "evidence",
            },
            path,
        )
        openshift_ref, iep_ref = _release_refs(item, path)
        profile_gates.append(ProfileAuthorizationGate(
            ref=_required_string(item, "ref", path),
            decision=_enum(GateDecision, item.get("decision"), f"{path}.decision"),
            owner=_required_string(item, "owner", path),
            cluster_ref=_required_string(item, "clusterRef", path),
            profile_kind=_required_string(item, "profileKind", path),
            profile_ref=_required_string(item, "profileRef", path),
            intent_state=_enum(IntentState, item.get("intentState"), f"{path}.intentState"),
            openshift_ref=openshift_ref,
            iep_ref=iep_ref,
            decided_by=_required_string(item, "decidedBy", path),
            decided_at=_required_string(item, "decidedAt", path),
            evidence=_strings(item.get("evidence"), f"{path}.evidence"),
        ))

    transition_gates = []
    for index, item in enumerate(docs["authorization"].get("transitionGates", [])):
        path = f"{paths['authorization']}:transitionGates[{index}]"
        if not isinstance(item, dict):
            raise ProfileContractError(f"{path} must be a mapping")
        _expect_keys(
            item,
            {
                "ref", "decision", "owner", "transitionRef", "clusterRef", "current",
                "target", "sequence", "decidedBy", "decidedAt", "evidence",
            },
            path,
        )
        current = item.get("current")
        target = item.get("target")
        if not isinstance(current, dict) or not isinstance(target, dict):
            raise ProfileContractError(f"{path}.current and target must be mappings")
        current_os, current_iep = _release_refs(current, f"{path}.current")
        target_os, target_iep = _release_refs(target, f"{path}.target")
        transition_gates.append(TransitionAuthorizationGate(
            ref=_required_string(item, "ref", path),
            decision=_enum(GateDecision, item.get("decision"), f"{path}.decision"),
            owner=_required_string(item, "owner", path),
            transition_ref=_required_string(item, "transitionRef", path),
            cluster_ref=_required_string(item, "clusterRef", path),
            current_openshift_ref=current_os,
            current_iep_ref=current_iep,
            target_openshift_ref=target_os,
            target_iep_ref=target_iep,
            sequence=_strings(item.get("sequence"), f"{path}.sequence"),
            decided_by=_required_string(item, "decidedBy", path),
            decided_at=_required_string(item, "decidedAt", path),
            evidence=_strings(item.get("evidence"), f"{path}.evidence"),
        ))

    bundle = CatalogBundle(
        openshift_releases=tuple(openshift_releases),
        iep_releases=tuple(iep_releases),
        compatibility=tuple(compatibility),
        management_profiles=tuple(management_profiles),
        worker_profiles=tuple(worker_profiles),
        profile_gates=tuple(profile_gates),
        transition_gates=tuple(transition_gates),
        compatibility_owner=docs["compatibility"]["ownership"]["owner"],
        management_profile_owner=docs["management"]["ownership"]["owner"],
        worker_profile_owner=docs["worker"]["ownership"]["owner"],
        source_digest=_catalog_digest([docs[name] for name in sorted(docs)]),
    )
    validate_catalogs(bundle)
    return bundle


def _identity_name(document: dict[str, Any], path: Path) -> str:
    identity = document.get("identity")
    if not isinstance(identity, dict):
        raise ProfileContractError(f"{path}: identity must be a mapping")
    _expect_keys(identity, {"name", "description"}, f"{path}:identity")
    return _required_string(identity, "name", f"{path}:identity")


def _release_refs(spec: dict[str, Any], path: str) -> tuple[str, str]:
    refs = spec.get("releaseRefs")
    if not isinstance(refs, dict):
        raise ProfileContractError(f"{path}.releaseRefs is required")
    _expect_keys(refs, {"openshift", "iep"}, f"{path}.releaseRefs")
    return (
        _required_string(refs, "openshift", f"{path}.releaseRefs"),
        _required_string(refs, "iep", f"{path}.releaseRefs"),
    )


def resolve_v2_tree(root: Path) -> ResolvedPlatformIntent:
    catalog_dir = root / "catalogs"
    cluster_dir = root / "clusters"
    transition_dir = root / "transitions"
    catalogs = load_catalogs(catalog_dir)

    management_docs: list[tuple[Path, dict[str, Any]]] = []
    hosted_docs: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(cluster_dir.glob("*.yaml")):
        document = _read_yaml(path)
        _validate_document_schema(document, path)
        _forbid_legacy_keys(document, str(path))
        kind = document.get("kind")
        if kind == "ManagementCluster":
            _check_schema(document, path, "ManagementCluster")
            _expect_keys(document, {"schemaVersion", "kind", "identity", "spec"}, str(path))
            management_docs.append((path, document))
        elif kind == "HostedCluster":
            _check_schema(document, path, "HostedCluster")
            _expect_keys(document, {"schemaVersion", "kind", "identity", "spec"}, str(path))
            hosted_docs.append((path, document))
        else:
            raise ProfileContractError(f"{path}: unsupported cluster kind {kind!r}")
    if not management_docs:
        raise ProfileContractError(f"no ManagementCluster documents in {cluster_dir}")

    identity_names: dict[Path, str] = {}
    identity_sources: dict[str, Path] = {}
    management_roles: dict[str, str] = {}
    for path, document in (*management_docs, *hosted_docs):
        name = _identity_name(document, path)
        previous = identity_sources.get(name)
        if previous is not None:
            raise ProfileContractError(
                f"duplicate cluster identity {name!r} in {previous} and {path}"
            )
        identity_sources[name] = path
        identity_names[path] = name
    for path, document in management_docs:
        spec = document.get("spec")
        assert isinstance(spec, dict)
        management_roles[identity_names[path]] = _required_string(spec, "role", f"{path}:spec")

    hosted_by_parent: dict[str, list[ResolvedHostedClusterIntent]] = {}
    for path, document in hosted_docs:
        source = path.relative_to(root).as_posix()
        name = identity_names[path]
        spec = document.get("spec", {})
        if not isinstance(spec, dict):
            raise ProfileContractError(f"{path}: spec must be a mapping")
        _expect_keys(
            spec,
            {"parentRef", "intentState", "releaseRefs", "workerNetworkProfileRef", "profileGateRef", "workerNetworks"},
            f"{path}:spec",
        )
        parent_ref = _required_string(spec, "parentRef", f"{path}:spec")
        parent_role = management_roles.get(parent_ref)
        if parent_role is None:
            raise ProfileContractError(f"{path}: hosted cluster references unknown parent {parent_ref!r}")
        if parent_role != "hcp-management":
            raise ProfileContractError(
                f"{path}: hosted cluster parent {parent_ref!r} must have role 'hcp-management'"
            )
        openshift_ref, iep_ref = _release_refs(spec, f"{path}:spec")
        releases = resolve_release_pair(
            catalogs,
            openshift_ref,
            iep_ref,
            openshift_origin=ValueOrigin(source, "spec.releaseRefs.openshift", "explicit hosted-cluster selection"),
            iep_origin=ValueOrigin(source, "spec.releaseRefs.iep", "explicit hosted-cluster selection"),
        )
        worker_ref = _required_string(spec, "workerNetworkProfileRef", f"{path}:spec")
        intent_state = _enum(IntentState, spec.get("intentState"), f"{path}:spec.intentState")
        profile_gate_ref = _optional_string(spec, "profileGateRef", f"{path}:spec")
        worker_profile = select_worker_profile(
            catalogs,
            ref=worker_ref,
            intent_state=intent_state,
        )
        authorized, authorization_reason = evaluate_profile_authorization(
            catalogs,
            cluster_ref=name,
            profile_kind="worker-network-profile",
            profile_ref=worker_profile.ref,
            profile_validation=worker_profile.validation,
            selection_policy=worker_profile.selection_policy,
            intent_state=intent_state,
            releases=releases,
            profile_gate_ref=profile_gate_ref,
        )
        network_entries = spec.get("workerNetworks")
        if not isinstance(network_entries, list) or len(network_entries) != 1:
            raise ProfileContractError(
                f"{path}: current single-interface baseline requires exactly one spec.workerNetworks entry"
            )
        worker_networks = []
        for index, entry in enumerate(network_entries):
            entry_path = f"{path}:spec.workerNetworks[{index}]"
            if not isinstance(entry, dict):
                raise ProfileContractError(f"{entry_path} must be a mapping")
            _expect_keys(entry, {"networkRef", "subnetRef", "guestDefaultRoute"}, entry_path)
            worker_networks.append(ResolvedWorkerAttachment(
                network_ref=_required_string(entry, "networkRef", entry_path),
                guest_default_route=_required_bool(entry, "guestDefaultRoute", entry_path),
                origin=ValueOrigin(source, f"spec.workerNetworks[{index}]", "factual attachment selection"),
                subnet_ref=_required_string(entry, "subnetRef", entry_path) if "subnetRef" in entry else "",
            ))
        default_count = sum(item.guest_default_route for item in worker_networks)
        if default_count != 1:
            raise ProfileContractError(
                f"{path}: current single-interface baseline requires exactly one guestDefaultRoute, got {default_count}"
            )
        hosted_by_parent.setdefault(parent_ref, []).append(ResolvedHostedClusterIntent(
            name=name,
            parent_ref=parent_ref,
            intent_state=intent_state,
            releases=releases,
            worker_profile=ResolvedValue(
                worker_ref,
                ValueOrigin(source, "spec.workerNetworkProfileRef", "explicit per-HCP worker realization"),
            ),
            worker_profile_expansion=worker_profile,
            worker_profile_validation=worker_profile.validation,
            worker_profile_selection_policy=worker_profile.selection_policy,
            profile_gate_ref=profile_gate_ref,
            deployment_authorized=authorized,
            authorization_reason=authorization_reason,
            worker_networks=tuple(worker_networks),
            renderer_authorizations=tuple(
                ResolvedAuthorization(
                    name=authorization,
                    profile_ref=worker_profile.ref,
                    reason="complete worker profile authorization; facts alone grant no permission",
                )
                for authorization in (worker_profile.renderer_authorizations if authorized else ())
            ),
        ))

    management_clusters = []
    known_management_names = set()
    for path, document in management_docs:
        source = path.relative_to(root).as_posix()
        name = identity_names[path]
        known_management_names.add(name)
        spec = document.get("spec", {})
        if not isinstance(spec, dict):
            raise ProfileContractError(f"{path}: spec must be a mapping")
        _expect_keys(
            spec,
            {"role", "mode", "intentState", "releaseRefs", "managementPlatformProfileRef", "profileGateRef", "topologyRefs"},
            f"{path}:spec",
        )
        role = _required_string(spec, "role", f"{path}:spec")
        mode = _required_string(spec, "mode", f"{path}:spec")
        intent_state = _enum(IntentState, spec.get("intentState"), f"{path}:spec.intentState")
        if role not in {"standalone", "hcp-management"}:
            raise ProfileContractError(f"{path}: spec.role must be standalone or hcp-management")
        if mode not in {"sno", "compact"}:
            raise ProfileContractError(f"{path}: spec.mode must be sno or compact")
        topology_data = spec.get("topologyRefs")
        if not isinstance(topology_data, dict):
            raise ProfileContractError(f"{path}: spec.topologyRefs must be a mapping")
        _expect_keys(topology_data, {"networkCatalog", "defaults"}, f"{path}:spec.topologyRefs")
        network_catalog_ref = _required_string(
            topology_data, "networkCatalog", f"{path}:spec.topologyRefs"
        )
        defaults_ref = topology_data.get("defaults")
        if defaults_ref is not None and (not isinstance(defaults_ref, str) or not defaults_ref):
            raise ProfileContractError(f"{path}: spec.topologyRefs.defaults must be a non-empty string")
        openshift_ref, iep_ref = _release_refs(spec, f"{path}:spec")
        releases = resolve_release_pair(
            catalogs,
            openshift_ref,
            iep_ref,
            openshift_origin=ValueOrigin(source, "spec.releaseRefs.openshift", "explicit management-cluster selection"),
            iep_origin=ValueOrigin(source, "spec.releaseRefs.iep", "explicit management-cluster selection"),
        )
        explicit_profile = spec.get("managementPlatformProfileRef")
        if explicit_profile is not None and not isinstance(explicit_profile, str):
            raise ProfileContractError(f"{path}: spec.managementPlatformProfileRef must be a string")
        profile, profile_reason = select_management_profile(
            catalogs,
            role=role,
            mode=mode,
            openshift_ref=openshift_ref,
            iep_ref=iep_ref,
            explicit_ref=explicit_profile,
        )
        profile_gate_ref = _optional_string(spec, "profileGateRef", f"{path}:spec")
        authorized, authorization_reason = evaluate_profile_authorization(
            catalogs,
            cluster_ref=name,
            profile_kind="management-platform-profile",
            profile_ref=profile.ref,
            profile_validation=profile.lifecycle,
            selection_policy=profile.selection_policy,
            intent_state=intent_state,
            releases=releases,
            profile_gate_ref=profile_gate_ref,
        )
        profile_field = "spec.managementPlatformProfileRef" if explicit_profile else "derived:role+releaseRefs"
        management_clusters.append(ResolvedManagementClusterIntent(
            name=name,
            intent_state=intent_state,
            role=ResolvedValue(role, ValueOrigin(source, "spec.role", "explicit cluster role")),
            mode=ResolvedValue(mode, ValueOrigin(source, "spec.mode", "explicit topology mode")),
            releases=releases,
            topology_refs=ResolvedTopologyReferences(
                network_catalog=ResolvedValue(
                    network_catalog_ref,
                    ValueOrigin(
                        source,
                        "spec.topologyRefs.networkCatalog",
                        "explicit factual network catalog reference",
                    ),
                ),
                defaults=(
                    ResolvedValue(
                        defaults_ref,
                        ValueOrigin(
                            source,
                            "spec.topologyRefs.defaults",
                            "explicit defaults catalog reference",
                        ),
                    )
                    if defaults_ref is not None
                    else None
                ),
            ),
            management_profile=ResolvedValue(
                profile.ref,
                ValueOrigin(source, profile_field, profile_reason),
            ),
            management_profile_expansion=profile,
            profile_validation=profile.lifecycle,
            profile_gate_ref=profile_gate_ref,
            deployment_authorized=authorized,
            authorization_reason=authorization_reason,
            renderer_authorizations=tuple(
                ResolvedAuthorization(
                    name=authorization,
                    profile_ref=profile.ref,
                    reason="complete management composition authorization",
                )
                for authorization in (profile.renderer_authorizations if authorized else ())
            ),
            hosted_clusters=tuple(sorted(hosted_by_parent.get(name, []), key=lambda item: item.name)),
        ))

    unknown_parents = sorted(set(hosted_by_parent) - known_management_names)
    if unknown_parents:
        raise ProfileContractError(f"hosted clusters reference unknown parents: {', '.join(unknown_parents)}")

    transitions = []
    cluster_release_refs = {
        cluster.name: (cluster.releases.openshift.value, cluster.releases.iep.value)
        for cluster in management_clusters
    }
    for cluster in management_clusters:
        cluster_release_refs.update({
            hosted.name: (hosted.releases.openshift.value, hosted.releases.iep.value)
            for hosted in cluster.hosted_clusters
        })
    if transition_dir.is_dir():
        for path in sorted(transition_dir.glob("*.yaml")):
            source = path.relative_to(root).as_posix()
            document = _read_yaml(path)
            _validate_document_schema(document, path)
            _forbid_legacy_keys(document, str(path))
            _check_schema(document, path, "ReleaseTransition")
            _expect_keys(document, {"schemaVersion", "kind", "identity", "spec"}, str(path))
            name = _identity_name(document, path)
            spec = document.get("spec", {})
            if not isinstance(spec, dict):
                raise ProfileContractError(f"{path}: spec must be a mapping")
            _expect_keys(
                spec,
                {"clusterRef", "current", "target", "coordinatedChangeApprovalRef", "sequence", "healthGates", "rollback"},
                f"{path}:spec",
            )
            cluster_ref = _required_string(spec, "clusterRef", f"{path}:spec")
            if cluster_ref not in cluster_release_refs:
                raise ProfileContractError(
                    f"{path}: transition references unknown cluster {cluster_ref!r}"
                )
            current_data = spec.get("current", {})
            target_data = spec.get("target", {})
            if not isinstance(current_data, dict) or not isinstance(target_data, dict):
                raise ProfileContractError(f"{path}: spec.current and spec.target must be mappings")
            _expect_keys(current_data, {"releaseRefs"}, f"{path}:spec.current")
            _expect_keys(target_data, {"releaseRefs"}, f"{path}:spec.target")
            current_os, current_iep = _release_refs(current_data, f"{path}:spec.current")
            target_os, target_iep = _release_refs(target_data, f"{path}:spec.target")
            if (current_os, current_iep) != cluster_release_refs[cluster_ref]:
                raise ProfileContractError(
                    f"{path}: transition current releaseRefs do not match cluster {cluster_ref!r}"
                )
            current = resolve_release_pair(
                catalogs,
                current_os,
                current_iep,
                openshift_origin=ValueOrigin(source, "spec.current.releaseRefs.openshift", "transition current state"),
                iep_origin=ValueOrigin(source, "spec.current.releaseRefs.iep", "transition current state"),
            )
            target = resolve_release_pair(
                catalogs,
                target_os,
                target_iep,
                openshift_origin=ValueOrigin(source, "spec.target.releaseRefs.openshift", "transition target state"),
                iep_origin=ValueOrigin(source, "spec.target.releaseRefs.iep", "transition target state"),
            )
            coordinated_ref = _optional_string(
                spec, "coordinatedChangeApprovalRef", f"{path}:spec"
            )
            axis = transition_axis(
                current,
                target,
                coordinated_change_approval_ref=coordinated_ref,
            )
            sequence = _strings(spec.get("sequence"), f"{path}:spec.sequence")
            health_gates = _strings(spec.get("healthGates"), f"{path}:spec.healthGates")
            rollback = spec.get("rollback", {})
            if not isinstance(rollback, dict):
                raise ProfileContractError(f"{path}: spec.rollback must be a mapping")
            _expect_keys(rollback, {"releaseRefs", "method"}, f"{path}:spec.rollback")
            rollback_os, rollback_iep = _release_refs(rollback, f"{path}:spec.rollback")
            rollback_method = _required_string(rollback, "method", f"{path}:spec.rollback")
            if (rollback_os, rollback_iep) != (current_os, current_iep):
                raise ProfileContractError(f"{path}: rollback releaseRefs must restore the current pair")
            validate_transition_steps(
                axis=axis,
                sequence=sequence,
                health_gates=health_gates,
                rollback_method=rollback_method,
            )
            if axis == "coordinated":
                validate_transition_gate(
                    catalogs,
                    gate_ref=coordinated_ref,
                    transition_ref=name,
                    cluster_ref=cluster_ref,
                    current=current,
                    target=target,
                    sequence=sequence,
                )
            elif coordinated_ref:
                raise ProfileContractError(
                    f"{path}: coordinatedChangeApprovalRef is valid only for coordinated transitions"
                )
            transitions.append(ResolvedTransitionIntent(
                name=name,
                cluster_ref=cluster_ref,
                axis=axis,
                current=current,
                target=target,
                sequence=sequence,
                health_gates=health_gates,
                authorization_gate_ref=coordinated_ref,
                rollback_releases=(rollback_os, rollback_iep),
                rollback_method=rollback_method,
            ))

    return ResolvedPlatformIntent(
        schema_version=SCHEMA_VERSION,
        catalog_digest=catalogs.source_digest,
        management_clusters=tuple(sorted(management_clusters, key=lambda item: item.name)),
        transitions=tuple(sorted(transitions, key=lambda item: item.name)),
        diagnostics=(),
        legacy_adapter_used=False,
    )


def resolve_profile_intent(root: Path) -> ResolvedPlatformIntent:
    """Resolve v2 data/catalogs when present; otherwise use the legacy adapter."""
    if (root / "data" / "catalogs").is_dir():
        return resolve_v2_tree(root / "data")
    from .legacy import resolve_legacy_root

    return resolve_legacy_root(root)
