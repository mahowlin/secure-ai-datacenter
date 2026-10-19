"""Topology-selected Day-2 storage rendering.

The generated payloads are deliberately split from activation. SNO storage is
an active cluster entrypoint; compact Portworx is emitted as a staged
Application which is not included by the cluster kustomization until the live
maintenance gate accepts the revalidated disks and destructive scope.
"""

from .selected_inputs import api_label

import base64
import copy

from .common import *


def _data_url(value: str) -> str:
    return "data:text/plain;charset=utf-8;base64," + base64.b64encode(value.encode()).decode()


def _application(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults,
                 name: str, path: str, wave: int, *, automated: bool) -> dict:
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": name,
            "namespace": defaults.namespace("gitops"),
            "annotations": {
                **_annotation(wave),
                api_label("storage-profile"): cluster.storage["profile"],
                api_label("activation"): cluster.storage["activation"],
            },
        },
        "spec": {
            "project": "default",
            "source": {
                "repoURL": defaults.gitops_repo_url,
                "targetRevision": defaults.gitops_target_revision,
                "path": path,
            },
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {"syncOptions": ["SkipDryRunOnMissingResource=true", "ServerSideApply=true"]},
        },
    }
    if automated:
        app["spec"]["syncPolicy"]["automated"] = {"prune": True, "selfHeal": True}
    return app


def _sno_create_script(devices: list[str]) -> str:
    quoted = "\n".join(devices)
    return f"""#!/usr/bin/env bash
set -euo pipefail
array=/dev/md/nvme-data
marker=/var/lib/saif-storage/nvme-data.ready
failure=/var/lib/saif-storage/nvme-data.failed
mapfile -t devices <<'SAIF_DEVICES'
{quoted}
SAIF_DEVICES
mkdir -p /var/lib/saif-storage /etc/mdadm
rm -f "$failure"
fail() {{ printf '%s\n' "$1" >"$failure"; exit 1; }}
[[ ${{#devices[@]}} -eq 5 ]] || fail invalid-device-count
for device in "${{devices[@]}}"; do
  [[ "$device" == /dev/disk/by-id/* ]] || fail unstable-device-path
  [[ -b "$device" ]] || fail missing-device
done
if [[ ! -e "$array" ]]; then
  member_count=0
  for device in "${{devices[@]}}"; do
    if /usr/sbin/mdadm --examine "$device" >/dev/null 2>&1; then
      member_count=$((member_count + 1))
    fi
  done
  if (( member_count > 0 )); then
    /usr/sbin/mdadm --assemble --run "$array" "${{devices[@]}}" || fail assemble-failed
  else
    for device in "${{devices[@]}}"; do
      [[ -z "$(lsblk -nro MOUNTPOINT "$device")" ]] || fail mounted-device
      [[ -z "$(lsblk -nro PKNAME "$device")" ]] || fail partition-device
    done
    /usr/sbin/mdadm --create "$array" --level=10 --raid-devices=4 \
      --spare-devices=1 --metadata=1.2 --chunk=512K --name=nvme-data \
      --homehost=any --bitmap=internal --run "${{devices[@]}}" || fail create-failed
  fi
fi
detail=$(/usr/sbin/mdadm --detail "$array") || fail unreadable-existing-array
grep -q 'Raid Level : raid10' <<<"$detail" || fail wrong-raid-level
grep -q 'Raid Devices : 4' <<<"$detail" || fail wrong-member-count
grep -q 'Active Devices : 4' <<<"$detail" || fail degraded-array
grep -Eq 'State : (clean|active)' <<<"$detail" || fail degraded-array
if ! /usr/sbin/lvmdevices --adddev "$(readlink -f "$array")"; then
  /usr/sbin/lvmdevices --check || fail lvmdevices-add-failed
fi
/usr/sbin/mdadm --detail --scan > /etc/mdadm.conf
touch "$marker"
"""


def _sno_readiness_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
timeout=${SAIF_READINESS_TIMEOUT_SECONDS:-1800}
retry=${SAIF_READINESS_RETRY_SECONDS:-15}
host=${SAIF_HOST_ROOT:-/host}
deadline=$((SECONDS + timeout))
while (( SECONDS < deadline )); do
  if ! json=$(oc get machineconfigpool master -o json 2>/dev/null); then
    sleep "$retry"
    continue
  fi
  if ! machine=$(jq -er '.status.machineCount // 0' <<<"$json") ||
     ! updated=$(jq -er '.status.updatedMachineCount // 0' <<<"$json") ||
     ! degraded=$(jq -er '.status.degradedMachineCount // 0' <<<"$json") ||
     ! updating=$(jq -er '[.status.conditions[] | select(.type=="Updating")][0].status // "True"' <<<"$json"); then
    sleep "$retry"
    continue
  fi
  if (( degraded > 0 )); then
    echo 'MCP is degraded' >&2
    exit 1
  fi
  if [[ "$machine" -eq 1 && "$updated" -eq 1 && "$degraded" -eq 0 && "$updating" == False ]]; then
    if [[ -e "$host/var/lib/saif-storage/nvme-data.failed" ]]; then
      cat "$host/var/lib/saif-storage/nvme-data.failed" >&2
      exit 1
    fi
    if chroot "$host" systemctl is-active --quiet saif-mdadm-nvme.service &&
       [[ -e "$host/dev/md/nvme-data" ]]; then
      if detail=$(chroot "$host" /usr/sbin/mdadm --detail /dev/md/nvme-data 2>/dev/null); then
        if grep -q 'Raid Level : raid10' <<<"$detail" &&
           grep -q 'Raid Devices : 4' <<<"$detail" &&
           grep -q 'Active Devices : 4' <<<"$detail" &&
           grep -Eq 'State : (clean|active)' <<<"$detail"; then
          exit 0
        fi
        echo 'array is present but degraded or has the wrong geometry' >&2
        exit 1
      fi
    fi
  fi
  sleep "$retry"
done
echo 'SNO storage did not become ready before the deadline' >&2
exit 1
"""


def _render_sno(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> dict[str, str]:
    storage = cluster.storage
    array = storage["array"]
    base = f"apps/storage/sno-mdadm-lvms/{cluster.name}"
    readiness_namespace = defaults.namespace("storageReadiness")
    create_script = _sno_create_script(array["devices"])
    mc = {
        "apiVersion": "machineconfiguration.openshift.io/v1", "kind": "MachineConfig",
        "metadata": {"name": "99-saif-mdadm-nvme-raid10", "labels": {"machineconfiguration.openshift.io/role": "master"},
                     "annotations": _annotation(defaults.sync_wave("storageHostConfig"))},
        "spec": {"config": {"ignition": {"version": "3.2.0"},
            "storage": {"files": [{"path": "/usr/local/sbin/saif-mdadm-nvme", "mode": 493, "overwrite": True,
                                    "contents": {"source": _data_url(create_script)}}]},
            "systemd": {"units": [{"name": "saif-mdadm-nvme.service", "enabled": True, "contents": """[Unit]
Description=SAIF SNO NVMe RAID10
After=local-fs.target
Before=kubelet.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/saif-mdadm-nvme
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""}]}}},
    }
    readiness_rbac_wave = defaults.sync_wave("storageReadiness") - 1
    readiness_ns = _namespace(defaults, readiness_namespace)
    readiness_ns["metadata"]["annotations"] = _annotation(defaults.sync_wave("storageHostConfig"))
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "sno-storage-readiness", "namespace": readiness_namespace, "annotations": _annotation(readiness_rbac_wave)}}
    role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": f"{cluster.name}-storage-readiness", "annotations": _annotation(readiness_rbac_wave)},
            "rules": [
                {"apiGroups": ["machineconfiguration.openshift.io"], "resources": ["machineconfigpools"], "verbs": ["get"]},
                {"apiGroups": ["security.openshift.io"], "resources": ["securitycontextconstraints"],
                 "resourceNames": ["privileged"], "verbs": ["use"]},
            ]}
    binding = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": f"{cluster.name}-storage-readiness", "annotations": _annotation(readiness_rbac_wave)},
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": f"{cluster.name}-storage-readiness"},
               "subjects": [{"kind": "ServiceAccount", "name": "sno-storage-readiness", "namespace": readiness_namespace}]}
    job = {"apiVersion": "batch/v1", "kind": "Job", "metadata": {"name": "sno-storage-readiness", "namespace": readiness_namespace,
            "annotations": {**_annotation(defaults.sync_wave("storageReadiness")), "argocd.argoproj.io/hook": "Sync", "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation,HookSucceeded"}},
           "spec": {"backoffLimit": 2, "template": {"spec": {"serviceAccountName": "sno-storage-readiness", "restartPolicy": "Never",
               "hostPID": True, "containers": [{"name": "readiness", "image": defaults.gitops_image_reference("storageReadiness"),
                   "command": ["/bin/bash", "-ceu", _sno_readiness_script()], "securityContext": {"privileged": True},
                   "volumeMounts": [{"name": "host", "mountPath": "/host"}]}],
               "volumes": [{"name": "host", "hostPath": {"path": "/", "type": "Directory"}}]}}}}
    ns = _namespace(defaults, defaults.namespace("storage"), {
        "app.kubernetes.io/name": "lvms-operator",
        "app.kubernetes.io/managed-by": "argocd",
        "openshift.io/cluster-monitoring": "true",
    })
    ns["metadata"]["annotations"] = _annotation(defaults.sync_wave("storageOperator") - 2)
    og = _operatorgroup(defaults, "openshift-storage-operatorgroup", defaults.namespace("storage"), [defaults.namespace("storage")])
    # The source path changes in place under the existing Application owner;
    # keep the overlapping provider object semantically identical.
    og["spec"].pop("upgradeStrategy", None)
    og["metadata"]["annotations"] = _annotation(defaults.sync_wave("storageOperator") - 1)
    lvms_defaults = defaults.gitops_storage["lvms"]
    sub = {"apiVersion": "operators.coreos.com/v1alpha1", "kind": "Subscription", "metadata": {"name": "lvms-operator", "namespace": defaults.namespace("storage"), "annotations": _annotation(defaults.sync_wave("storageOperator")),
            "labels": {"operators.coreos.com/lvms-operator.openshift-storage": ""}},
           "spec": {"name": "lvms-operator", "channel": lvms_defaults["channel"], "source": lvms_defaults["catalogSource"], "sourceNamespace": defaults.namespace("marketplace"), "installPlanApproval": "Automatic"}}
    lvm = {"apiVersion": "lvm.topolvm.io/v1alpha1", "kind": "LVMCluster", "metadata": {"name": "lvmcluster", "namespace": defaults.namespace("storage"), "annotations": _annotation(defaults.sync_wave("storageCluster"))},
           "spec": {"storage": {"deviceClasses": [{"name": "nvme-raid10", "default": True, "deviceSelector": {"paths": [array["devicePath"]]},
               "thinPoolConfig": {"name": "thin-pool-0", "sizePercent": 90, "overprovisionRatio": 10}}]}}}
    resources = {"machineconfig.yaml": mc, "readiness-namespace.yaml": readiness_ns,
                 "serviceaccount.yaml": sa, "clusterrole.yaml": role,
                 "clusterrolebinding.yaml": binding, "readiness-job.yaml": job,
                 "namespace.yaml": ns, "operatorgroup.yaml": og,
                 "subscription.yaml": sub, "lvmcluster.yaml": lvm}
    files = {f"{base}/{name}": HEADER + _k8s_yaml(value) for name, value in resources.items()}
    files[f"{base}/kustomization.yaml"] = _kustomization(list(resources))
    # Preserve the exact legacy Application identity and finalizer. Root
    # promotion updates its source path in place instead of pruning the old
    # provider owner and creating a competing Application.
    app = _application(cluster, defaults, "lvms-operator", base,
                       defaults.sync_wave("storageHostConfig"), automated=True)
    app["metadata"]["finalizers"] = ["resources-finalizer.argocd.argoproj.io"]
    files[f"clusters/{cluster.name}/storage.yaml"] = HEADER + _k8s_yaml(app)
    return files



def _mirror(image: str, registry: str) -> str:
    source, digest = image.rsplit("@", 1)
    path = source.split("/", 1)[1]
    return f"{registry}/{path}@{digest}"


def _portworx_versions(px: dict, registry: str) -> str:
    images = px["images"]
    component_images = {
        "stork": "stork", "autopilot": "autopilot", "clusterDiags": "cluster-diags",
        "csiPlugin": "pure-csi-driver", "csiMigrator": "pure-csi-migrator",
        "csiLivenessProbe": "livenessprobe", "csiNodeDriverRegistrar": "csi-node-driver-registrar",
        "csiProvisioner": "csi-provisioner", "csiAttacher": "pure-csi-attacher",
        "csiResizer": "csi-resizer", "csiSnapshotter": "csi-snapshotter",
        "csiSnapshotController": "snapshot-controller", "csiTelemetry": "pure-csi-telemetry",
        "prometheus": "prometheus", "prometheusOperator": "prometheus-operator",
        "prometheusConfigReloader": "prometheus-config-reloader", "alertManager": "alertmanager",
        "telemetry": "ccm-go", "pxLibUpdate": "px-lib", "metricsCollector": "realtime-metrics",
        "telemetryProxy": "edge-envoy", "logUploader": "log-upload", "cacheAgent": "cache-agent",
        "integrationOperator": "integration-operator", "fusionController": "fusion",
        "dynamicPlugin": "dynamic-plugin", "dynamicPluginProxy": "nginx",
        "certManagerController": "cert-manager-controller", "certManagerWebhook": "cert-manager-webhook",
        "certManagerCaInjector": "cert-manager-cainjector", "certManagerAcmeSolver": "cert-manager-acmesolver",
        "kubeScheduler": "kube-scheduler", "kubeControllerManager": "kube-controller-manager",
        "pause": "pause",
    }
    manifest = {
        "version": str(px["version"]),
        "components": {name: _mirror(images[key], registry) for name, key in component_images.items()},
    }
    if px.get("csiProvisionerTagOnlyCompatibility"):
        # Only this operator input requires tag syntax. Inventory remains
        # digest-locked and transport verifies the registry tag before push.
        manifest["components"]["csiProvisioner"] = manifest["components"]["csiProvisioner"].split("@", 1)[0]
    return yaml.safe_dump(manifest, sort_keys=False)


def _render_portworx(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> dict[str, str]:
    storage = cluster.storage
    px = defaults.gitops_storage["portworx"]
    images = px["images"]
    ns_name = defaults.namespace("portworx")
    base = f"apps/storage/portworx-local/{cluster.name}"
    namespace = _namespace(defaults, ns_name, {"pod-security.kubernetes.io/enforce": "privileged"})
    operator_rbac_wave = defaults.sync_wave("storageOperator") - 1
    namespace["metadata"]["annotations"] = _annotation(operator_rbac_wave)
    sa = {"apiVersion": "v1", "kind": "ServiceAccount", "metadata": {"name": "portworx-operator", "namespace": ns_name, "annotations": _annotation(operator_rbac_wave)}}
    role = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole", "metadata": {"name": "portworx-operator", "annotations": _annotation(operator_rbac_wave)},
            "rules": [{"apiGroups": ["*"], "resources": ["*"], "verbs": ["*"]}]}
    binding = {"apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRoleBinding", "metadata": {"name": "portworx-operator", "annotations": _annotation(operator_rbac_wave)},
               "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": "portworx-operator"},
               "subjects": [{"kind": "ServiceAccount", "name": "portworx-operator", "namespace": ns_name}]}
    operator = {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {"name": "portworx-operator", "namespace": ns_name,
                "annotations": _annotation(defaults.sync_wave("storageOperator"))}, "spec": {"replicas": 1,
                "strategy": {"type": "RollingUpdate", "rollingUpdate": {"maxSurge": 1, "maxUnavailable": 1}},
                "selector": {"matchLabels": {"name": "portworx-operator"}}, "template": {"metadata": {"labels": {"name": "portworx-operator"}},
                "spec": {"serviceAccountName": "portworx-operator", "containers": [{"name": "portworx-operator",
                    "image": _mirror(images["px-operator"], defaults.registry_url), "imagePullPolicy": "IfNotPresent",
                    "command": ["/operator", "--verbose", "--driver=portworx", "--leader-elect=true"],
                    "env": [{"name": "OPERATOR_NAME", "value": "portworx-operator"}, {"name": "POD_NAME",
                             "valueFrom": {"fieldRef": {"fieldPath": "metadata.name"}}}]}],
                    "affinity": {"podAntiAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": [{
                        "labelSelector": {"matchExpressions": [{"key": "name", "operator": "In", "values": ["portworx-operator"]}]},
                        "topologyKey": "kubernetes.io/hostname"}]}}}}}}
    # Pin the operator to a portable non-root SCC. Its image declares the
    # non-numeric user "nobody", so give kubelet the equivalent numeric UID.
    operator["spec"]["template"]["metadata"]["annotations"] = {"openshift.io/required-scc": "nonroot-v2"}
    operator["spec"]["template"]["spec"]["securityContext"] = {
        "runAsUser": 65534, "runAsNonRoot": True, "seccompProfile": {"type": "RuntimeDefault"},
    }
    versions = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "px-versions", "namespace": ns_name,
                "annotations": _annotation(defaults.sync_wave("storageOperator") - 1)},
                "data": {"versions.yaml": _portworx_versions(px, defaults.registry_url)}}
    migrated_placement = px["migratedComponentPlacement"]
    component_config = {
        "apiVersion": "core.libopenstorage.org/v1",
        "kind": "ComponentK8sConfig",
        "metadata": {"name": "stork-openshift-scc", "namespace": ns_name,
                     "annotations": _annotation(defaults.sync_wave("storageCluster") + 1)},
        "spec": {"components": [
            {"componentNames": ["Stork"], "workloadConfigs": [
                {"workloadNames": ["stork"],
                 "annotations": {"openshift.io/required-scc": px["storkRequiredScc"]}}]},
            {"componentNames": copy.deepcopy(migrated_placement["componentNames"]),
             "workloadConfigs": [{
                 "workloadNames": copy.deepcopy(migrated_placement["workloadNames"]),
                 "placement": {"tolerations": copy.deepcopy(migrated_placement["tolerations"])},
             }]},
        ]},
    }
    default_class_policy = px["defaultStorageClass"]
    default_class = {
        "apiVersion": "storage.k8s.io/v1", "kind": "StorageClass",
        "metadata": {"name": default_class_policy["name"],
                     "annotations": {**_annotation(defaults.sync_wave("storageCluster") + 2),
                                     "storageclass.kubernetes.io/is-default-class": "true"}},
        "provisioner": "pxd.portworx.com",
        "parameters": {"repl": default_class_policy["replicationFactor"]},
        "reclaimPolicy": "Delete", "volumeBindingMode": "Immediate", "allowVolumeExpansion": True,
    }
    stc = {"apiVersion": "core.libopenstorage.org/v1", "kind": "StorageCluster", "metadata": {"name": "portworx", "namespace": ns_name,
            "annotations": {**_annotation(defaults.sync_wave("storageCluster")), "portworx.io/misc-args": "-T px-storev2",
                            "portworx.io/run-on-master": "true", api_label("license-secret-ref"): storage["licenseSecretName"],
                            "portworx.io/preflight-check": "true",
                            "portworx.io/pvc-controller-port": "10261",
                            "portworx.io/pvc-controller-secure-port": "10262",
                            api_label("device-inventory"): "historical-revalidate-at-live-gate"}},
           "spec": {"image": _mirror(images["oci-monitor"], defaults.registry_url), "imagePullPolicy": "IfNotPresent",
                    "startPort": 17001,
                    "kvdb": {"internal": True},
                    "storage": {"useAll": False, "useAllWithPartitions": False, "forceUseDisks": False, "devices": []},
                    "nodes": [{"selector": {"nodeName": f"{item['name']}.{cluster.base_domain}"}, "storage": {"devices": item["devices"],
                               "systemMetadataDevice": item["systemMetadataDevice"]}} for item in storage["nodes"]],
                    "csi": {"enabled": True, "kubeVirtStorageClasses": {"pxRwxBlockKubevirt": True,
                            "pxRwxFileKubevirt": True, "pxCdiScratch": True}},
                    "stork": {"enabled": True, "image": _mirror(images["stork"], defaults.registry_url), "lockImage": True},
                    "autopilot": {"enabled": False}, "clusterDiags": {"enabled": False},
                    "updateStrategy": {"type": "RollingUpdate", "rollingUpdate": {"maxUnavailable": 1}}}}
    resources = {"namespace.yaml": namespace, "serviceaccount.yaml": sa, "clusterrole.yaml": role,
                 "clusterrolebinding.yaml": binding, "operator.yaml": operator, "px-versions.yaml": versions,
                 "storagecluster.yaml": stc, "componentk8sconfig.yaml": component_config,
                 "default-storageclass.yaml": default_class}
    files = {f"{base}/{name}": HEADER + _k8s_yaml(value) for name, value in resources.items()}
    files[f"{base}/kustomization.yaml"] = _kustomization(list(resources))
    activation = storage["activation"]
    if activation == "provider-cutover":
        app = _application(cluster, defaults, f"{cluster.name}-portworx", base,
                           defaults.sync_wave("storageOperator"), automated=False)
        app["metadata"]["annotations"][api_label("activation")] = "manual-maintenance-window"
        files[f"clusters/{cluster.name}/storage-portworx.yaml"] = HEADER + _k8s_yaml(app)
    else:
        app = _application(cluster, defaults, f"{cluster.name}-portworx-staged", base,
                           defaults.sync_wave("storageOperator"), automated=False)
        app["metadata"]["annotations"][api_label("activation")] = "staged"
        files[f"clusters/{cluster.name}/storage-portworx-staged.yaml"] = HEADER + _k8s_yaml(app)
    return files


def render_storage(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> dict[str, str]:
    profile = cluster.storage.get("profile")
    if profile == "sno-mdadm-lvms":
        return _render_sno(cluster, defaults)
    if profile == "portworx-local":
        return _render_portworx(cluster, defaults)
    raise ValueError(f"unsupported storage profile {profile!r}")
