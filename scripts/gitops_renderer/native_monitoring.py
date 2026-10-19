"""Render held OpenShift-native monitoring persistence from selected inventory."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from .common import HEADER, ResolvedDefaults, ResolvedOCPCluster

NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
STORAGE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?$")
ROLES = {"multus"}
DURATION = re.compile(r"^[1-9][0-9]{0,5}(?:ms|s|m|h|d|w|y)$")


def _name(value: object, field: str, pattern: re.Pattern[str] = NAME) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _policy(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> tuple[dict, str] | None:
    ref = cluster.observability.get("nativeMonitoringPolicyRef")
    if ref is None:
        return None
    if not isinstance(ref, str) or not ref.endswith(".yaml"):
        raise ValueError("native monitoring policy ref must be a YAML path")
    relative = Path(ref)
    if relative.is_absolute() or ".." in relative.parts or defaults.input_root is None:
        raise ValueError("native monitoring policy ref must stay within selected input root")
    root = defaults.input_root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("native monitoring policy ref escapes selected input root")
    policy = yaml.safe_load(path.read_text())
    if not isinstance(policy, dict) or policy.get("metadata", {}).get("name") != cluster.name:
        raise ValueError("native monitoring policy identity differs from selected cluster")
    if policy.get("kind") != "NativeMonitoringPersistence" or policy.get("spec", {}).get("activation") != "review-only":
        raise ValueError("native monitoring requires review-only policy")
    return policy, ref


def _retention(value: object) -> str:
    if not isinstance(value, str) or not DURATION.fullmatch(value):
        raise ValueError("invalid native monitoring retention duration")
    return value


def _component(row: object) -> dict:
    if not isinstance(row, dict) or set(row) != {"observedReplicas", "retention", "storageGi"}:
        raise ValueError("monitoring component sizing requires replicas, retention and storage")
    replicas, size = row["observedReplicas"], row["storageGi"]
    if type(replicas) is not int or not 1 <= replicas <= 3:
        raise ValueError("invalid observed monitoring replica count")
    if type(size) is not int or not 1 <= size <= 500:
        raise ValueError("invalid monitoring PVC size")
    _retention(row["retention"])
    return row


def _claim(storage_class: str, size: int) -> dict:
    return {"spec": {"storageClassName": storage_class, "accessModes": ["ReadWriteOnce"],
                     "volumeMode": "Filesystem", "resources": {"requests": {"storage": f"{size}Gi"}}}}


def _configmap(name: str, namespace: str, config: dict) -> str:
    obj = {"apiVersion": "v1", "kind": "ConfigMap",
           "metadata": {"name": name, "namespace": namespace},
           "data": {"config.yaml": yaml.safe_dump(config, sort_keys=False)}}
    return HEADER + "# Manual sync. Rendering does not activate this overlay.\n" + yaml.safe_dump(obj, sort_keys=False)


def _kustomization(resource: str) -> str:
    return HEADER + "# Manual sync. Rendering does not activate this overlay.\n" + yaml.safe_dump({
        "apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
        "resources": [resource]}, sort_keys=False)


def render(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> dict[str, str]:
    selected = _policy(cluster, defaults)
    if selected is None:
        return {}
    policy, ref = selected
    app = _name(Path(ref).stem, "native monitoring application")
    spec = policy["spec"]
    management = spec.get("management")
    if not isinstance(management, dict) or management.get("preserveEnableUserWorkload") is not True:
        raise ValueError("management UWM existing intent must remain enabled")
    management_sc = _name(management.get("storageClassName"), "management storage class", STORAGE)
    pk = _component(management.get("prometheusK8s"))
    uwm = _component(management.get("prometheusUserWorkload"))
    alert = _component(management.get("alertmanagerMain"))
    subjects = cluster.hosted_cluster_lifecycle["deploymentSubjects"]
    guests = spec.get("guests")
    if not isinstance(guests, list) or not guests:
        raise ValueError("at least one measured guest required")
    refs: list[str] = []
    for row in guests:
        if not isinstance(row, dict) or set(row) != {
                "targetRef", "observedReplicas", "retention", "storageClassName",
                "prometheusK8sStorageGi", "userWorkload"}:
            raise ValueError("guest monitoring row missing selected inputs")
        ref_name = row["targetRef"]
        if ref_name not in ROLES or ref_name not in subjects or ref_name in refs:
            raise ValueError("invalid or duplicate guest target ref")
        refs.append(ref_name)
        _name(subjects[ref_name], "hosted guest name")
        _name(row["storageClassName"], "guest storage class", STORAGE)
        _component({"observedReplicas": row["observedReplicas"], "retention": row["retention"],
                    "storageGi": row["prometheusK8sStorageGi"]})
        guest_uwm = row["userWorkload"]
        if not isinstance(guest_uwm, dict) or set(guest_uwm) != {"enable", "retention", "storageGi"}:
            raise ValueError("guest UWM enablement, retention and storage required")
        if guest_uwm["enable"] is not True or (
                type(guest_uwm["storageGi"]) is not int or not 1 <= guest_uwm["storageGi"] <= 100):
            raise ValueError("invalid guest user-workload monitor")
        _retention(guest_uwm["retention"])
    pending = spec.get("pendingGuestRefs")
    if not isinstance(pending, list) or set(pending) != ROLES - set(refs) or len(pending) != len(set(pending)):
        raise ValueError("unmeasured guest roles must remain explicitly pending")
    if set(subjects[ref] for ref in refs) - {h.name for h in cluster.hosted_clusters}:
        raise ValueError("guest policy target absent from resolved inventory")

    platform_cm = {
        "enableUserWorkload": True,
        "prometheusK8s": {"retention": pk["retention"],
                          "volumeClaimTemplate": _claim(management_sc, pk["storageGi"])},
        "alertmanagerMain": {"volumeClaimTemplate": _claim(management_sc, alert["storageGi"])},
    }
    prometheus_only_cm = {key: value for key, value in platform_cm.items()
                          if key != "alertmanagerMain"}
    uwm_cm = {"prometheus": {"retention": uwm["retention"],
                            "volumeClaimTemplate": _claim(management_sc, uwm["storageGi"])}}
    base = f"apps/hcp/management/{app}/overlays"
    files = {
        f"{base}/{cluster.name}/platform-prometheus/core.yaml": _configmap(
            "cluster-monitoring-config", "openshift-monitoring", prometheus_only_cm),
        f"{base}/{cluster.name}/platform-prometheus/kustomization.yaml": _kustomization("core.yaml"),
        f"{base}/{cluster.name}/platform/core.yaml": _configmap(
            "cluster-monitoring-config", "openshift-monitoring", platform_cm),
        f"{base}/{cluster.name}/platform/kustomization.yaml": _kustomization("core.yaml"),
        f"{base}/{cluster.name}/user-workload/config.yaml": _configmap(
            "user-workload-monitoring-config", "openshift-user-workload-monitoring", uwm_cm),
        f"{base}/{cluster.name}/user-workload/kustomization.yaml": _kustomization("config.yaml"),
    }
    guest_summary = []
    for row in guests:
        name = subjects[row["targetRef"]]
        guest_cm = {"enableUserWorkload": True, "prometheusK8s": {
            "retention": row["retention"],
            "volumeClaimTemplate": _claim(row["storageClassName"], row["prometheusK8sStorageGi"])}}
        guest_root = f"apps/hcp/tenant/{app}/overlays/{name}"
        files[f"{guest_root}/platform/core.yaml"] = _configmap(
            "cluster-monitoring-config", "openshift-monitoring", guest_cm)
        files[f"{guest_root}/platform/kustomization.yaml"] = _kustomization("core.yaml")
        files[f"{guest_root}/user-workload/config.yaml"] = _configmap(
            "user-workload-monitoring-config", "openshift-user-workload-monitoring",
            {"prometheus": {"retention": row["userWorkload"]["retention"],
                            "volumeClaimTemplate": _claim(row["storageClassName"],
                                                          row["userWorkload"]["storageGi"])}})
        files[f"{guest_root}/user-workload/kustomization.yaml"] = _kustomization("config.yaml")
        guest_summary.append({"cluster": name, "observedReplicas": row["observedReplicas"],
                              "sizeGiPerReplica": row["prometheusK8sStorageGi"],
                              "storageClassName": row["storageClassName"],
                              "userWorkload": {"enabled": True,
                                               "sizeGiPerReplica": row["userWorkload"]["storageGi"],
                                               "observedReplicas": None}})
    files[f"{base}/{cluster.name}/manifest.json"] = json.dumps({
        "activation": "review-only", "source": ref, "managementCluster": cluster.name,
        "observedState": "not-applied", "management": {
            "platformPrometheus": {"replicas": pk["observedReplicas"], "sizeGiPerReplica": pk["storageGi"]},
            "userWorkloadPrometheus": {"replicas": uwm["observedReplicas"], "sizeGiPerReplica": uwm["storageGi"]},
            "alertmanagerMain": {"replicas": alert["observedReplicas"], "sizeGiPerReplica": alert["storageGi"]},
            "storageClassName": management_sc, "preserveEnableUserWorkload": True},
        "selectedGuests": guest_summary,
        "pendingGuestRefs": pending,
        "rollout": ["platform-prometheus", "user-workload", "platform-final-alertmanager",
                    "each-guest-user-workload-persistence-before-enablement",
                    "one-guest-platform-prometheus-and-enable-uwm-at-a-time"],
    }, indent=2, sort_keys=True) + "\n"
    return files
