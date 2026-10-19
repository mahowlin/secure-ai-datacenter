"""Tenant observability exporter Helm values for Timescape object-store ingest."""

import json

from .common import *


def _tenant_exporter_scc_binding(cluster_name: str, defaults: ResolvedDefaults) -> dict:
    timescape_ns = defaults.namespace("timescape")
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": f"{cluster_name}-timescape-exporter-privileged"},
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": f"{cluster_name}-hubble-k8s-exporter",
                "namespace": timescape_ns,
            },
            {
                "kind": "ServiceAccount",
                "name": f"{cluster_name}-timescape-s3-exporter-vector",
                "namespace": timescape_ns,
            },
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "system:openshift:scc:privileged",
        },
    }


def _tenant_k8s_exporter_values(cluster_name: str, defaults: ResolvedDefaults) -> dict:
    image = defaults.gitops_image_values("hubbleK8sExporter")
    return {
        "image": {
            "repository": image["repository"],
            "tag": image["tag"],
        },
        "export": {
            "clusterName": cluster_name,
            "filePath": "/var/run/cilium/hubble/k8s-events.log",
        },
        "securityContext": {"privileged": True, "runAsUser": 0},
    }


def _safe_file_source(path: str) -> dict:
    return {
        "type": "file",
        "include": [path],
        "read_from": "end",
        "fingerprint": {"strategy": "device_and_inode"},
        "glob_minimum_cooldown_ms": 1000,
        "max_line_bytes": 1048576,
    }


def _cluster_name_transform() -> dict:
    return {
        "type": "remap",
        "source": """cluster_name = get_env_var!(\"CLUSTER_NAME\")
parsed = parse_json!(string!(.message))
if exists(parsed.flow) {
  parsed.flow.source.cluster_name = cluster_name
  parsed.flow.destination.cluster_name = cluster_name
}
if exists(parsed.k8s_event) {
  parsed.k8s_event.cluster = cluster_name
}
.message = encode_json(parsed)
""",
    }


def _timescape_s3_sink(defaults: ResolvedDefaults,
                       cluster_name: str,
                       source_name: str,
                       inputs: list[str]) -> dict:
    object_store = defaults.gitops_timescape["objectStore"]
    return {
        "type": "aws_s3",
        "inputs": inputs,
        "bucket": object_store["bucket"],
        "key_prefix": f"hubble-events/{cluster_name}/%Y/%m/%d/{source_name}/",
        "endpoint": object_store["endpoint"],
        "region": object_store["region"],
        "force_path_style": object_store["forcePathStyle"],
        "compression": "gzip",
        "encoding": {"codec": "raw_message"},
        "batch": {"max_bytes": 10485760, "timeout_secs": 60},
        "buffer": {"type": "disk", "max_size": 268435488},
    }


def _vector_image(defaults: ResolvedDefaults, image_pull: str, pull_endpoint: str | None = None) -> dict:
    """Lab tag unless this hosted cluster opts into the in-cluster mirror."""
    image = defaults.gitops_image_values("vector")
    if image_pull == "":
        return {"repository": image["repository"], "tag": image["tag"]}
    if image_pull != "hosted-mirror":
        raise ValueError(f"unsupported timescape exporter image pull {image_pull!r}")
    pull_repository = str(image.get("hostedPullRepository") or "")
    digest = str(image.get("digest") or "")
    declared_endpoint = str(image.get("hostedPullEndpoint") or "")
    if (not declared_endpoint or "/" in declared_endpoint
            or not pull_repository.startswith(declared_endpoint + "/")
            or not digest.startswith("sha256:")):
        raise ValueError("vector hosted exporter requires its declared hostedPullEndpoint and a sha256 digest")
    if pull_endpoint is not None:
        if not pull_endpoint or "/" in pull_endpoint:
            raise ValueError("selected registry pull endpoint must be a host:port authority")
        pull_repository = pull_endpoint + pull_repository[len(declared_endpoint):]
    registry_host = str(defaults.registry_url).split(":", 1)[0]
    if not registry_host:
        raise ValueError("selected spec.registry.url is required")
    if registry_host in pull_repository:
        raise ValueError("vector hosted exporter must not pull from the configured registry")
    return {"repository": pull_repository, "tag": image["tag"], "sha": digest}


def _vector_s3_exporter_values(cluster_name: str, defaults: ResolvedDefaults,
                               image_pull: str = "", metrics: dict | None = None,
                               pull_endpoint: str | None = None) -> dict:
    exporter_metrics = metrics or {}
    values = {
        "clusterName": cluster_name,
        "role": "Agent",
        "logLevel": "info",
        "image": _vector_image(defaults, image_pull, pull_endpoint),
        "envFrom": [{"secretRef": {"name": "timescape-object-writer"}}],
        "persistence": {"hostPath": {"enabled": True, "path": "/var/lib/vector/timescape"}},
        "resources": {
            "requests": {"cpu": "100m", "memory": "128Mi"},
            "limits": {"cpu": "500m", "memory": "512Mi"},
        },
        "securityContext": {
            "allowPrivilegeEscalation": True,
            "privileged": True,
            "runAsUser": 0,
        },
        "extraVolumes": [
            {
                "name": "hubble-logs",
                "hostPath": {
                    "path": "/var/run/cilium/hubble",
                    "type": "DirectoryOrCreate",
                },
            },
        ],
        "extraVolumeMounts": [
            {
                "name": "hubble-logs",
                "mountPath": "/var/run/cilium/hubble",
                "readOnly": True,
            },
        ],
        "customConfig": {
            "data_dir": "/vector-data-dir",
            "api": {"enabled": True, "address": "127.0.0.1:8686"},
            "sources": {
                "hubble_flows": _safe_file_source("/var/run/cilium/hubble/hubble.log"),
                "k8s_events": _safe_file_source("/var/run/cilium/hubble/k8s-events.log"),
                "connection_logs": _safe_file_source("/var/run/cilium/hubble/connectionlog.log"),
            },
            "transforms": {
                "flows_cluster_name": {"inputs": ["hubble_flows"], **_cluster_name_transform()},
                "k8s_events_cluster_name": {"inputs": ["k8s_events"], **_cluster_name_transform()},
                "connection_logs_cluster_name": {"inputs": ["connection_logs"], **_cluster_name_transform()},
            },
            "sinks": {
                "hubble_flows_object_store": _timescape_s3_sink(
                    defaults, cluster_name, "hubble.log", ["flows_cluster_name"]
                ),
                "k8s_events_object_store": _timescape_s3_sink(
                    defaults, cluster_name, "k8s-events.log", ["k8s_events_cluster_name"]
                ),
                "connection_logs_object_store": _timescape_s3_sink(
                    defaults, cluster_name, "connectionlog.log", ["connection_logs_cluster_name"]
                ),
            },
        },
        "tolerations": [
            {"effect": "NoSchedule", "key": "node-role.kubernetes.io/control-plane"},
            {"effect": "NoSchedule", "key": "node-role.kubernetes.io/master"},
        ],
        "service": {"enabled": False},
    }
    if exporter_metrics.get("endpoint", False):
        values["customConfig"]["sources"]["internal_metrics"] = {"type": "internal_metrics"}
        values["customConfig"]["sinks"]["prom_exporter"] = {
            "type": "prometheus_exporter",
            "inputs": ["internal_metrics"],
            "address": f"0.0.0.0:{exporter_metrics['port']}",
        }
        if exporter_metrics.get("podMonitor", False):
            values["podMonitor"] = {"enabled": True, "port": "prom-exporter"}
    return values


def timescape_ingest_hosts(cluster: ResolvedOCPCluster) -> list[str]:
    """Selected/retained hosted exporters plus explicitly retained external guests."""
    external = cluster.observability.get("centralTimescape", {}).get(
        "retainedHostedClusters", [])
    return sorted({hosted.name for hosted in cluster.hosted_clusters
                   if hosted.network_type == "Cilium" and
                   hosted.timescape_exporter_enrollment in ("selected", "retained")}
                  | set(external))


def render_hosted_writer_intent(cluster: ResolvedOCPCluster,
                                defaults: ResolvedDefaults) -> dict[str, str]:
    """Declare selected writers and every hosted cluster's explicit enrollment."""
    if not cluster.features.get("hostedControlPlanes", False):
        return {}
    review = cluster.observability["centralTimescape"]["writerReview"]
    controller_namespace, controller_name = review["guestControllerRef"].split("/", 1)
    targets = []
    enrollments = []
    for hosted in sorted(cluster.hosted_clusters, key=lambda item: item.name):
        enrollments.append({"cluster": hosted.name,
                            "enrollment": hosted.timescape_exporter_enrollment,
                            "centralIngest": hosted.timescape_exporter_enrollment in
                            ("selected", "retained")})
        if hosted.timescape_exporter_enrollment != "selected":
            continue
        targets.append({
            "cluster": hosted.name,
            "hostedClusterName": hosted.name,
            "hostedClusterNamespace": f"clusters-{hosted.name}",
            "controllerName": controller_name,
            "controllerNamespace": controller_namespace,
            "adminKubeconfigSecretName": f"{hosted.name}-admin-kubeconfig",
            "adminKubeconfigSecretKey": "kubeconfig",
            "namespace": defaults.namespace("timescape"),
            "secretName": "timescape-object-writer",
            "keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"],
            "sealedPath": (f"apps/sealed-secrets/{hosted.name}/"
                           f"{defaults.namespace('timescape')}/timescape-object-writer.yaml"),
        })
    path = "apps/sealed-secrets/hosted-writer-intent.json"
    return {path: json.dumps({"source": "HCPCluster spec.timescapeExporter.enrollment",
                              "managementCluster": cluster.name,
                              "managementApiEndpoint": review["managementApiEndpoint"],
                              "executionPolicy": {"commandTimeoutSeconds": review["commandTimeoutSeconds"],
                                                  "jobTimeoutMinutes": review["jobTimeoutMinutes"]},
                              "activation": "manual", "enrollments": enrollments,
                              "retainedExternal": sorted(cluster.observability.get(
                                  "centralTimescape", {}).get("retainedHostedClusters", [])),
                              "targets": targets},
                             sort_keys=True, indent=2) + "\n"}
