import posixpath
from .common import *
from .selected_inputs import gitops_foundation


def _render_isovalent_root(gitops_repo_url: str,
                           gitops_target_revision: str,
                           defaults: ResolvedDefaults) -> dict[str, str]:
    """Render reusable Isovalent base wiring and Vector Push API application."""
    gitops_ns = defaults.namespace("gitops")
    timescape_ns = defaults.namespace("timescape")
    vector_image = defaults.gitops_image_values("vector")
    push_api = defaults.gitops_timescape.get("pushApi", {})
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "vector-exporter",
            "namespace": gitops_ns,
            "annotations": _annotation(defaults.sync_wave("isovalentVectorExporter")),
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": "default",
            "source": {
                "repoURL": gitops_repo_url,
                "targetRevision": gitops_target_revision,
                "path": "charts/vector",
                "helm": {
                    "parameters": [],
                    "values": """role: Agent
logLevel: \"info\"

image:
  repository: %(vector_repository)s
  tag: %(vector_tag)s

env:
  - name: PUSH_API_PASSWORD
    valueFrom:
      secretKeyRef:
        name: vector-push-credentials
        key: push-api-password

persistence:
  hostPath:
    enabled: true
    path: /var/lib/vector/timescape

resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 512Mi

securityContext:
  allowPrivilegeEscalation: true
  privileged: true
  runAsUser: 0

extraVolumes:
  - name: hubble-logs
    hostPath:
      path: /var/run/cilium/hubble
      type: DirectoryOrCreate

extraVolumeMounts:
  - name: hubble-logs
    mountPath: /var/run/cilium/hubble
    readOnly: true

customConfig:
  data_dir: /vector-data-dir
  api:
    enabled: true
    address: 127.0.0.1:8686

  sources:
    hubble_logs:
      type: file
      include:
        - /var/run/cilium/hubble/hubble.log
        - /var/run/cilium/hubble/k8s-events.log
        - /var/run/cilium/hubble/application-model.log
      read_from: end
      fingerprint:
        strategy: device_and_inode
      max_line_bytes: 1048576
      glob_minimum_cooldown_ms: 1000
    connection_logs:
      type: file
      include:
        - /var/run/cilium/hubble/connectionlog.log
      read_from: end
      fingerprint:
        strategy: device_and_inode
      max_line_bytes: 1048576
      glob_minimum_cooldown_ms: 1000

  transforms:
    add_cluster_name:
      type: remap
      inputs: [hubble_logs, connection_logs]
      source: |
        cluster_name = get_env_var!(\"CLUSTER_NAME\")
        parsed = parse_json!(string!(.message))
        if exists(parsed.flow) {
          parsed.flow.source.cluster_name = cluster_name
          parsed.flow.destination.cluster_name = cluster_name
        }
        if exists(parsed.k8s_event) {
          parsed.k8s_event.cluster = cluster_name
        }
        .message = encode_json(parsed)

  sinks:
    timescape_push_api:
      type: http
      inputs: [add_cluster_name]
      uri: \"%(timescape_push_uri)s\"
      method: post
      encoding:
        codec: raw_message
      framing:
        method: newline_delimited
      auth:
        strategy: basic
        user: timescape_push_api
        password: \"${PUSH_API_PASSWORD}\"
      batch:
        max_bytes: 10485760
        timeout_secs: 5
      request:
        timeout_secs: 60
        retry_attempts: 5
      healthcheck:
        enabled: false
      buffer:
        type: disk
        max_size: 268435488

tolerations:
  - effect: NoSchedule
    key: node-role.kubernetes.io/control-plane
  - effect: NoSchedule
    key: node-role.kubernetes.io/master

service:
  enabled: false
""" % {
                        "vector_repository": vector_image["repository"],
                        "vector_tag": vector_image["tag"],
                        "timescape_push_uri": push_api["uri"],
                    },
                },
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": timescape_ns,
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
            },
        },
    }
    return {
        f"{gitops_foundation('isovalent')}/kustomization.yaml": _kustomization([
            "cilium.yaml",
            "tetragon.yaml",
            "tetragon-catalogsource.yaml",
            "hubble-timescape.yaml",
            "hubble-timescape-scc.yaml",
            "vector-exporter.yaml",
            "clickhouse-operator.yaml",
        ]),
        f"{gitops_foundation('isovalent')}/vector-exporter.yaml": HEADER + _k8s_yaml(app),
    }


def _render_argocd_config(gitops_repo_url: str,
                          gitops_target_revision: str,
                          defaults: ResolvedDefaults) -> dict[str, str]:
    """Render GitOps-owned OpenShift GitOps controller configuration."""
    gitops_ns = defaults.namespace("gitops")
    oci_repository = defaults.gitops_argocd_oci_repository()
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "argocd-config",
            "namespace": gitops_ns,
            "annotations": _annotation(defaults.sync_wave("argocdConfig")),
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": "default",
            "source": {
                "repoURL": gitops_repo_url,
                "targetRevision": gitops_target_revision,
                "path": "apps/argocd-config",
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": gitops_ns,
            },
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["ServerSideApply=true", "Validate=false"],
            },
        },
    }
    argocd = {
        "apiVersion": "argoproj.io/v1beta1",
        "kind": "ArgoCD",
        "metadata": {
            "name": "openshift-gitops",
            "namespace": gitops_ns,
        },
        "spec": {
            "controller": {
                "resources": {
                    "requests": {"cpu": "250m", "memory": "1Gi"},
                    "limits": {"cpu": "2", "memory": "8Gi"},
                }
            }
        },
    }
    oci_registry_repo = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": oci_repository["name"],
            "namespace": gitops_ns,
            "labels": {"argocd.argoproj.io/secret-type": "repository"},
        },
        "stringData": {
            "url": oci_repository["url"],
            "name": oci_repository["displayName"],
            "enableOCI": str(oci_repository["enableOCI"]).lower(),
            "type": oci_repository["type"],
        },
    }
    return {
        f"{gitops_foundation('core')}/argocd-config.yaml": HEADER + _k8s_yaml(app),
        "apps/argocd-config/kustomization.yaml": _kustomization([
            "oci-registry-repo.yaml",
            "argocd-controller-resources.yaml",
        ]),
        "apps/argocd-config/oci-registry-repo.yaml": HEADER + _k8s_yaml(oci_registry_repo),
        "apps/argocd-config/argocd-controller-resources.yaml": HEADER + _k8s_yaml(argocd),
    }


def _render_platform_idms(cluster: ResolvedOCPCluster,
                          defaults: ResolvedDefaults) -> dict[str, str]:
    """Render GitOps platform-idms Application from Day 2 package intent."""
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": "platform-idms",
            "namespace": defaults.namespace("gitops"),
            "annotations": _annotation(defaults.gitops_app_type_sync_wave("platformIdms", "management")),
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": "default",
            "source": defaults.gitops_app_source(
                "platformIdms",
                profile=cluster.release.platform_day2_profile,
            ),
            "destination": {"server": "https://kubernetes.default.svc"},
            "syncPolicy": {"automated": {"prune": True, "selfHeal": True}},
        },
    }
    return {f"{gitops_foundation('core')}/platform-idms.yaml": HEADER + _k8s_yaml(app)}


# Cluster type overlays (clusters/_types/)
# ---------------------------------------------------------------------------

def _render_cluster_types() -> dict[str, str]:
    """Generate role-based cluster type kustomizations.

    Required capabilities are derived from role/CNI intent: HCP management
    clusters get core platform apps plus Isovalent apps. Optional add-ons
    such as NVIDIA, Splunk integrations, demo apps, and SAIF demo are not
    included in the HCP baseline.
    """
    return {
        "clusters/_types/hcp-management/kustomization.yaml": _kustomization([
            posixpath.relpath(gitops_foundation('core'), 'clusters/_types/hcp-management'),
            posixpath.relpath(gitops_foundation('isovalent'), 'clusters/_types/hcp-management'),
        ]),
        "clusters/_types/hcp-tenant/kustomization.yaml": _kustomization([
            posixpath.relpath(gitops_foundation('core'), 'clusters/_types/hcp-tenant'),
            posixpath.relpath(gitops_foundation('isovalent'), 'clusters/_types/hcp-tenant'),
        ]),
    }
