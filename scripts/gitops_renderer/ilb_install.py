"""Render manual, fail-closed placement stages for the official 1.19.5 ILB."""

from .selected_inputs import api_label, domain_label, gitops_repo_url, identity as selected_identity, ilb_paths

import json

from .common import *


def _placement_security_context() -> dict:
    return {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
        "readOnlyRootFilesystem": True,
        "runAsNonRoot": True,
    }


def _placement_resources(cluster: ResolvedOCPCluster,
                         defaults: ResolvedDefaults,
                         stage: str,
                         script: str) -> str:
    name = f"saif-ilb-{stage}-placement"
    namespace = defaults.namespace("gitops")
    image = cluster.in_cluster_load_balancer.placement_job_image
    documents = [
        {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": name, "namespace": namespace},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRole",
            "metadata": {"name": name},
            "rules": [
                {
                    "apiGroups": [""],
                    "resources": ["nodes"],
                    "verbs": ["get", "list", "watch", "patch", "update"],
                },
                {
                    "apiGroups": ["operator.openshift.io"],
                    "resources": ["ingresscontrollers"],
                    "verbs": (
                        ["get", "list", "watch", "patch", "update"]
                        if stage == "router-t1"
                        else ["get", "list", "watch"]
                    ),
                },
                {
                    "apiGroups": ["apps"],
                    "resources": ["deployments"],
                    "verbs": ["get", "list", "watch"],
                },
                {
                    "apiGroups": [""],
                    "resources": ["pods"],
                    "verbs": ["get", "list", "watch"],
                },
            ],
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "ClusterRoleBinding",
            "metadata": {"name": name},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": name,
            },
            "subjects": [{"kind": "ServiceAccount", "name": name, "namespace": namespace}],
        },
        {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": name,
                "namespace": namespace,
                "annotations": {
                    "argocd.argoproj.io/sync-wave": "1",
                    "argocd.argoproj.io/hook": "Sync",
                    "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation,HookSucceeded",
                    api_label("activation"): "manual-maintenance-window",
                },
            },
            "spec": {
                "activeDeadlineSeconds": 900,
                "backoffLimit": 0,
                "ttlSecondsAfterFinished": 600,
                "template": {
                    "spec": {
                        "serviceAccountName": name,
                        "restartPolicy": "Never",
                        "securityContext": {"seccompProfile": {"type": "RuntimeDefault"}},
                        "volumes": [{"name": "tmp", "emptyDir": {}}],
                        "containers": [{
                            "name": "placement",
                            "image": image,
                            "imagePullPolicy": "IfNotPresent",
                            "command": ["/bin/bash", "-euo", "pipefail", "-c", script],
                            "env": [{"name": "HOME", "value": "/tmp"}],
                            "volumeMounts": [{"name": "tmp", "mountPath": "/tmp"}],
                            "resources": {
                                "requests": {"cpu": "10m", "memory": "32Mi"},
                                "limits": {"cpu": "100m", "memory": "128Mi"},
                            },
                            "securityContext": _placement_security_context(),
                        }],
                    }
                },
            },
        },
    ]
    return HEADER + "".join(_k8s_yaml(document) for document in documents)


def _hostname_map(cluster: ResolvedOCPCluster) -> dict[str, str]:
    return {node.name: node.hostname for node in cluster.nodes}


def _router_script(router_nodes: list[str], t2_node: str, replicas: int) -> str:
    router_words = " ".join(router_nodes)
    return f"""router_nodes=({router_words})
t2_node={t2_node}
for node in "${{router_nodes[@]}}" "$t2_node"; do
  oc get node "$node" >/dev/null
done
for node in "${{router_nodes[@]}}"; do
  oc label node "$node" node-role.kubernetes.io/ingress=true service.cilium.io/node=t1 --overwrite
done
oc label node "$t2_node" node-role.kubernetes.io/ingress- service.cilium.io/node- --overwrite
oc patch ingresscontroller default -n openshift-ingress-operator --type=merge -p '{{"spec":{{"replicas":{replicas},"nodePlacement":{{"nodeSelector":{{"matchLabels":{{"node-role.kubernetes.io/ingress":"true"}}}}}}}}}}'
oc rollout status deployment/router-default -n openshift-ingress --timeout=600s
for node in "${{router_nodes[@]}}"; do
  test "$(oc get node "$node" -o jsonpath='{{.metadata.labels.node-role\\.kubernetes\\.io/ingress}}')" = true
  test "$(oc get node "$node" -o jsonpath='{{.metadata.labels.service\\.cilium\\.io/node}}')" = t1
done
test -z "$(oc get node "$t2_node" -o jsonpath='{{.metadata.labels.service\\.cilium\\.io/node}}')"
test "$(oc get deployment router-default -n openshift-ingress -o jsonpath='{{.status.availableReplicas}}')" = {replicas}
"""


def _t2_script(router_nodes: list[str], t2_node: str, replicas: int) -> str:
    router_words = " ".join(router_nodes)
    return f"""router_nodes=({router_words})
t2_node={t2_node}
for node in "${{router_nodes[@]}}"; do
  test "$(oc get node "$node" -o jsonpath='{{.metadata.labels.node-role\\.kubernetes\\.io/ingress}}')" = true
  test "$(oc get node "$node" -o jsonpath='{{.metadata.labels.service\\.cilium\\.io/node}}')" = t1
done
test "$(oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{{.spec.replicas}}')" = {replicas}
test "$(oc get ingresscontroller default -n openshift-ingress-operator -o jsonpath='{{.spec.nodePlacement.nodeSelector.matchLabels.node-role\\.kubernetes\\.io/ingress}}')" = true
test "$(oc get deployment router-default -n openshift-ingress -o jsonpath='{{.status.availableReplicas}}')" = {replicas}
if oc get pod -n openshift-ingress -l ingresscontroller.operator.openshift.io/deployment-ingresscontroller=default -o jsonpath='{{range .items[*]}}{{.spec.nodeName}}{{"\\n"}}{{end}}' | grep -Fx "$t2_node"; then
  echo "Router pod is still scheduled on the intended T2-only node" >&2
  exit 1
fi
oc label node "$t2_node" node-role.kubernetes.io/ingress- service.cilium.io/node- --overwrite
oc label node "$t2_node" service.cilium.io/node=t2 --overwrite
test "$(oc get node "$t2_node" -o jsonpath='{{.metadata.labels.service\\.cilium\\.io/node}}')" = t2
test -z "$(oc get node "$t2_node" -o jsonpath='{{.metadata.labels.node-role\\.kubernetes\\.io/ingress}}')"
"""


def _render_ilb_install(cluster: ResolvedOCPCluster,
                        defaults: ResolvedDefaults) -> dict[str, str]:
    intent = cluster.in_cluster_load_balancer
    if not intent.enabled:
        return {}
    hostnames = _hostname_map(cluster)
    routers = [hostnames[name] for name in intent.router_t1_nodes]
    t2_node = hostnames[intent.t2_nodes[0]]
    root = f"apps/hcp/management/ilb-install/overlays/{cluster.name}"
    stages = {
        "router-t1": _router_script(routers, t2_node, intent.router_replicas),
        "t2": _t2_script(routers, t2_node, intent.router_replicas),
    }
    files: dict[str, str] = {}
    for stage, script in stages.items():
        base = f"{root}/{stage}"
        files[f"{base}/placement.yaml"] = _placement_resources(
            cluster, defaults, stage, script
        )
        files[f"{base}/kustomization.yaml"] = _kustomization(["placement.yaml"])

    return files
