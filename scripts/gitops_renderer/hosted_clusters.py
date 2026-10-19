from hcp_cache import reference as cache_reference, require_ready
from .console_login import hosted_identity_provider, sealed_secret_present
from .selected_inputs import api_label
from .registry_exposure import registry_exposure, selected_registry_endpoint

import base64
import copy
import hashlib
import json

from .common import *

HOSTED_REGISTRY_CA_CONFIGMAP = "registry-ca-bundle"
HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION = "hypershift.openshift.io/kubevirt-vm-jsonpatch"


ROUTER_INGRESS_PORT = "8443"


def _router_ingress_policy(defaults: ResolvedDefaults,
                           namespace: str) -> dict:
    ingress = [{
        "fromEntities": ["all"],
        "toPorts": [{
            "ports": [{"port": ROUTER_INGRESS_PORT, "protocol": "TCP"}],
        }],
    }]
    return {
        "apiVersion": "cilium.io/v2",
        "kind": "CiliumNetworkPolicy",
        "metadata": {
            "name": "allow-hcp-router-ingress",
            "namespace": namespace,
            "annotations": _annotation(defaults.sync_wave("nodePool")),
        },
        "spec": {
            "endpointSelector": {
                "matchLabels": {
                    "app": "private-router",
                    "hypershift.openshift.io/request-serving-component": "true",
                },
            },
            "ingress": ingress,
        },
    }


def _hcp_release_image(cluster: ResolvedOCPCluster, hcp, defaults: ResolvedDefaults) -> str:
    ocp_version = hcp.ocp_version
    if cluster.release and ocp_version == cluster.release.ocp_minor:
        ocp_version = cluster.release.ocp_version
    repository = defaults.release_image_repository
    suffix = defaults.gitops_hosted_cluster["releaseImageArchitectureSuffix"]
    source = f"{repository}:{ocp_version}-{suffix}"
    contract = cluster.cache_production
    if contract and contract.get("mode") != "preceding-registry":
        return cache_reference(contract, source, consumer=contract["policy"]["releaseLookup"] == "hosted-cache" and hcp.name in contract["subjects"])
    return source


def _control_plane_ingress_dns_records(cluster: ResolvedOCPCluster) -> list[dict]:
    records = []
    for hcp in cluster.hosted_clusters:
        vip = hcp.control_plane_ingress_load_balancer_vip
        net = hcp.control_plane_ingress_network
        if not vip or not net:
            continue
        address = vip.cidr.split("/", 1)[0]
        for service in ("APIServer", "Ignition", "OAuthServer", "Konnectivity"):
            records.append({
                "name": _hcp_service_hostname(cluster, hcp, service),
                "type": "A",
                "address": address,
                "hostedCluster": hcp.name,
                "service": service,
                "loadBalancerVIPRef": vip.name,
                "owningNetwork": net.name,
            })
    return records


def _kubevirt_worker_dns_json_patch(servers: list[str]) -> str:
    if not servers:
        raise ValueError("direct HCP worker KubeVirt VM DNS patch requires DNS servers")
    patch = [
        {
            "op": "add",
            "path": "/spec/template/spec/dnsPolicy",
            "value": "None",
        },
        {
            "op": "add",
            "path": "/spec/template/spec/dnsConfig",
            "value": {
                "nameservers": list(servers),
            },
        },
    ]
    return json.dumps(patch, separators=(",", ":"))


def _canonical_worker_placement_patch(policy: dict, pool_name: str) -> dict:
    """Spread a NodePool over eligible hosts without blocking live migration."""
    return {
        "op": "add", "path": "/spec/template/spec/affinity",
        "value": {
            "nodeAffinity": {"requiredDuringSchedulingIgnoredDuringExecution": {
                "nodeSelectorTerms": [{"matchExpressions": [{
                    "key": "kubernetes.io/hostname", "operator": "In",
                    "values": list(policy["eligibleManagementNodes"]),
                }]}],
            }},
            "podAntiAffinity": {"preferredDuringSchedulingIgnoredDuringExecution": [{
                "weight": policy["spreadWeight"],
                "podAffinityTerm": {
                    "labelSelector": {"matchLabels": {
                        "hypershift.openshift.io/nodepool-name": pool_name,
                        "kubevirt.io": "virt-launcher",
                    }},
                    "topologyKey": "kubernetes.io/hostname",
                },
            }]},
        },
    }


def _local_registry_image_content_sources(sources: list[dict], registry: dict, endpoint: str | None = None) -> list[dict]:
    """Prepend the rendered pull Service for repositories named in data."""
    endpoint = endpoint or f"{registry['pullService']}.{registry['namespace']}.svc.cluster.local:{registry['port']}"
    mapped = {item["source"]: (item.get("endpoint", endpoint), item["repository"]) for item in registry.get("mirrors") or []}
    rendered: list[dict] = []
    seen: set[str] = set()
    for item in sources:
        seen.add(item["source"])
        mirrors = list(item["mirrors"])
        repository = mapped.get(item["source"])
        if repository:
            source_endpoint, source_repository = repository
            local = f"{source_endpoint}/{source_repository}"
            mirrors = [local] + [mirror for mirror in mirrors if mirror != local]
        rendered.append({"source": item["source"], "mirrors": mirrors})
    for item in registry.get("mirrors") or []:
        if item["source"] in seen:
            continue
        rendered.append({
            "source": item["source"],
            "mirrors": [f"{item.get('endpoint', endpoint)}/{item['repository']}"],
        })
    return rendered


def _hosted_image_content_sources(cluster: ResolvedOCPCluster,
                                  defaults: ResolvedDefaults) -> list[dict]:
    """Platform image mirrors for the hosted cluster release."""
    sources = defaults.gitops_hosted_image_content_sources()
    if (cluster.local_registry or {}).get("cacheProduction"):
        by_source = {item["source"]: item for item in defaults.image_digest_sources}
        by_source.update({item["source"]: item for item in sources})
        return list(by_source.values())
    return sources


def _render_hosted_clusters(cluster: ResolvedOCPCluster,
                            defaults: ResolvedDefaults) -> dict[str, str]:
    """HostedCluster + NodePool for each HCP, under management/hosted-clusters/."""
    files: dict[str, str] = {}
    base = f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{cluster.name}"
    gitops_ns = defaults.namespace("gitops")
    ose_tools_image = defaults.gitops_image_reference("oseTools")
    hcp_phase = cluster.hosted_cluster_lifecycle.get("phase", "legacy")
    require_ready(cluster, hcp_phase)
    worker_replicas = cluster.hosted_cluster_lifecycle.get("workerReplicas", 0)
    worker_replicas_by_cluster = cluster.hosted_cluster_lifecycle.get("workerReplicasByCluster", {})
    local_registry = cluster.local_registry or {}
    hc_dirs = []
    for hcp in cluster.hosted_clusters:
        hcp_worker_replicas = worker_replicas_by_cluster.get(hcp.name, worker_replicas)
        hc_dir = f"{base}/{hcp.name}"
        hc_dirs.append(hcp.name)
        hcp_ns = f"clusters-{hcp.name}"
        if hcp.worker_attachment_kind != "multus-l2":
            raise ValueError(
                f"{hcp.name}: this example renders only Multus worker attachments")

        # Namespace for the hosted cluster
        files[f"{hc_dir}/namespace.yaml"] = HEADER + _k8s_yaml(
            _namespace(defaults, hcp_ns, {
                "app.kubernetes.io/part-of": "hypershift",
                "hypershift.openshift.io/hosted-cluster": hcp.name,
            }))

        # registry-ca-bundle.yaml is target-owned secret-bearing GitOps state.
        # The renderer only wires HostedCluster.additionalTrustBundle to the
        # expected ConfigMap name; it must not emit or transport the CA payload.

        # Build KubeVirt network attachment list for workers.
        # NADs are rendered into the hosted cluster namespace; use local names so
        # Multus namespace isolation does not reject virt-launcher pods.
        additional_networks = []
        for attachment in hcp.worker_network_attachments:
            if attachment.attachment_name:
                additional_networks.append({"name": attachment.attachment_name})

        release_image = _hcp_release_image(cluster, hcp, defaults)

        hc = {
            "apiVersion": "hypershift.openshift.io/v1beta1",
            "kind": "HostedCluster",
            "metadata": {
                "name": hcp.name,
                "namespace": hcp_ns,
                "annotations": _annotation(defaults.sync_wave("hostedCluster")),
            },
            "spec": {
                "release": {"image": release_image},
                "pullSecret": {"name": "pull-secret"},
                "sshKey": {"name": "ssh-key"},
                "additionalTrustBundle": {"name": HOSTED_REGISTRY_CA_CONFIGMAP},
                "platform": {
                    "type": "KubeVirt",
                    "kubevirt": {"baseDomainPassthrough": True},
                },
                "networking": {
                    "networkType": "Other" if hcp.network_type == "Cilium" else hcp.network_type,
                    "clusterNetwork": hcp.cluster_networks,
                    "serviceNetwork": [sn if isinstance(sn, dict) else {"cidr": sn} for sn in hcp.service_networks],
                },
                "services": [
                    {
                        "service": service,
                        "servicePublishingStrategy": {
                            "type": "Route",
                            "route": {"hostname": _hcp_service_hostname(cluster, hcp, service)},
                        },
                    }
                    for service in ("APIServer", "OAuthServer", "Konnectivity", "Ignition")
                ],
                "dns": {"baseDomain": cluster.base_domain},
                "configuration": {
                    "operatorhub": {"disableAllDefaultSources": True},
                },
            },
        }
        # Hosted clusters must receive mirror policy through HostedCluster; HCP
        # blocks tenant-side IDMS/ITMS writes in the hosted cluster API.
        image_content_sources = _hosted_image_content_sources(cluster, defaults)
        external_endpoint = selected_registry_endpoint(cluster, hcp)
        selected_consumer = registry_exposure(cluster)["consumer"] if external_endpoint else None
        frozen_mirrors = bool(selected_consumer)
        if (hcp.timescape_exporter_image_pull == "hosted-mirror" and not frozen_mirrors
                and local_registry.get("cacheProduction", {}).get("mode") != "preceding-registry"
                and not cluster.cache_production.get("roots")):
            vector = defaults.gitops_image_values("vector")
            image_content_sources = [
                *image_content_sources,
                {"source": vector["repository"], "mirrors": [vector["hostedPullRepository"]]},
            ]
        if image_content_sources:
            hc["spec"]["imageContentSources"] = image_content_sources
        if frozen_mirrors:
            mirror_registry = local_registry
            if external_endpoint and not cluster.cache_production.get("roots"):
                vector = defaults.gitops_image_values("vector")
                endpoint = vector.get("hostedPullEndpoint")
                pull_repository = str(vector.get("hostedPullRepository") or "")
                if not endpoint or not pull_repository.startswith(endpoint + "/"):
                    raise ValueError("external registry requires the declared Vector mirror endpoint/repository")
                mirror_registry = {**local_registry, "mirrors": [
                    *local_registry["mirrors"],
                    {"source": vector["repository"], "repository": pull_repository[len(endpoint) + 1:]},
                ]}
            hc["spec"]["imageContentSources"] = _local_registry_image_content_sources(
                image_content_sources, mirror_registry, external_endpoint
            )
            hc["spec"]["pullSecret"] = {"name": selected_consumer["pullSecret"]}
            hc["spec"]["additionalTrustBundle"] = {"name": selected_consumer["trustBundle"]}
        provider = hosted_identity_provider(cluster, hcp.name)
        if provider:
            hc["spec"]["configuration"]["oauth"] = {"identityProviders": [provider]}
        files[f"{hc_dir}/hostedcluster.yaml"] = HEADER + _k8s_yaml(hc)

        # NodePool with KubeVirt network attachments
        kubevirt_spec: dict = defaults.gitops_hosted_kubevirt_worker()
        if additional_networks:
            kubevirt_spec["additionalNetworks"] = additional_networks
            kubevirt_spec["attachDefaultNetwork"] = False

        ntp_config_ref = None
        if defaults.ntp_servers:
            ntp_config_ref = f"{hcp.name}-worker-chrony"
            chrony_conf = "".join(f"server {server} iburst\n" for server in defaults.ntp_servers)
            encoded_chrony_conf = base64.b64encode(chrony_conf.encode()).decode()
            machine_config = {
                "apiVersion": "machineconfiguration.openshift.io/v1",
                "kind": "MachineConfig",
                "metadata": {
                    "name": "99-worker-chrony",
                    "labels": {
                        "machineconfiguration.openshift.io/role": "worker",
                    },
                },
                "spec": {
                    "config": {
                        "ignition": {"version": "3.2.0"},
                        "storage": {
                            "files": [{
                                "path": "/etc/chrony.conf",
                                "mode": 420,
                                "overwrite": True,
                                "contents": {
                                    "source": f"data:text/plain;charset=utf-8;base64,{encoded_chrony_conf}",
                                },
                            }],
                        },
                    },
                },
            }
            files[f"{hc_dir}/worker-chrony-machineconfig.yaml"] = HEADER + _k8s_yaml({
                "apiVersion": "v1",
                "kind": "ConfigMap",
                "metadata": {
                    "name": ntp_config_ref,
                    "namespace": hcp_ns,
                    "annotations": _annotation(defaults.sync_wave("nodePool")),
                },
                "data": {
                    "config": _k8s_yaml(machine_config),
                },
            })

        pool_policy = cluster.storage.get("hcpNodePool") or {}
        pool_suffix = pool_policy.get("suffix") or "workers"
        pool_name = f"{hcp.name}-{pool_suffix}"
        nodepool_annotations = _annotation(defaults.sync_wave("nodePool"))
        vm_patches: list[dict] = []
        if additional_networks:
            vm_patches.extend(json.loads(_kubevirt_worker_dns_json_patch(defaults.dns_servers)))
        subjects = cluster.hosted_cluster_lifecycle.get("deploymentSubjects") or {}
        if hcp.name in {subjects.get(role) for role in ("multus",)}:
            vm_patches.append(_canonical_worker_placement_patch(
                cluster.hosted_cluster_lifecycle["workerPlacement"], pool_name))
        if vm_patches:
            nodepool_annotations[HYPERSHIFT_KUBEVIRT_VM_JSONPATCH_ANNOTATION] = json.dumps(
                vm_patches, separators=(",", ":"))
        staged_only = {
            name for name in (
                (cluster.hosted_cluster_lifecycle.get("deploymentSubjects") or {}).get("stagedOnly") or []
            )
            if isinstance(name, str) and name
        }
        if pool_policy and hcp.name in staged_only:
            # Already on the live staged predecessor. The selected pools do not carry them.
            nodepool_annotations.update({
                api_label("activation"): pool_policy["activation"],
                api_label("replaces"): f"{hcp.name}-workers",
                api_label("storage-state"): "staged-replacement-excluded-from-kustomization",
            })
        required_pool = ("autoRepair", "upgradeType", "maxUnavailable")
        if any(key not in pool_policy for key in required_pool):
            raise ValueError(
                f"{cluster.name}: storage.hcpNodePool requires {', '.join(required_pool)}")
        management = {
            "autoRepair": pool_policy["autoRepair"],
            "upgradeType": pool_policy["upgradeType"],
            "inPlace": {"maxUnavailable": pool_policy["maxUnavailable"]},
        }
        active_pool_replicas = hcp_worker_replicas if hcp_phase == "workers" else hcp.replicas

        np = {
            "apiVersion": "hypershift.openshift.io/v1beta1",
            "kind": "NodePool",
            "metadata": {
                "name": pool_name,
                "namespace": hcp_ns,
                "annotations": nodepool_annotations,
            },
            "spec": {
                "clusterName": hcp.name,
                "replicas": active_pool_replicas,
                "management": management,
                "platform": {
                    "type": "KubeVirt",
                    "kubevirt": kubevirt_spec,
                },
                "release": {"image": release_image},
            },
        }
        nodepool_config_refs = []
        if ntp_config_ref:
            nodepool_config_refs.append({"name": ntp_config_ref})
        if nodepool_config_refs:
            np["spec"]["config"] = nodepool_config_refs
        files[f"{hc_dir}/nodepool-workers.yaml"] = HEADER + _k8s_yaml(np)

        staged_policy = pool_policy
        storage_cutover_patches = []

        storage_consumers_held = hcp_phase == "held" or (
            hcp_phase == "legacy" and cluster.storage.get("activation") in {
            "consumer-hold", "provider-cutover"
            }
        )
        nodepool_resources = []
        if ntp_config_ref:
            nodepool_resources.append("worker-chrony-machineconfig.yaml")
        if hcp_phase == "workers":
            nodepool_resources.append("nodepool-workers.yaml")
        elif hcp_phase == "legacy" and not storage_consumers_held:
            nodepool_resources.append("nodepool-workers.yaml")
        common_kustomization_resources = [
            "namespace.yaml", "registry-ca-bundle.yaml",
            "router-ingress-cnp.yaml", "virt-launcher-hostnetwork-egress-cnp.yaml",
            "cdi-importer-ingress-cnp.yaml", *nodepool_resources,
        ]
        if hcp_phase == "control-planes":
            common_kustomization_resources = [
                "namespace.yaml", "registry-ca-bundle.yaml", "hostedcluster.yaml",
            ]
        elif storage_consumers_held:
            # HyperShift removes the control-plane namespace with the HostedCluster.
            # Keep only resources whose namespaces survive the hold.
            common_kustomization_resources = [
                "namespace.yaml", "registry-ca-bundle.yaml", *nodepool_resources,
            ]
        if hcp_phase == "workers" or (hcp_phase == "legacy" and not storage_consumers_held):
            common_kustomization_resources.insert(2, "hostedcluster.yaml")

        kubevirt_machine_namespace = f"clusters-{hcp.name}-{hcp.name}"
        files[f"{hc_dir}/router-ingress-cnp.yaml"] = HEADER + _k8s_yaml(
            _router_ingress_policy(defaults, kubevirt_machine_namespace)
        )

        files[f"{hc_dir}/virt-launcher-hostnetwork-egress-cnp.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": {
                "name": "allow-virt-launcher-to-hostnetwork-ingress",
                "namespace": kubevirt_machine_namespace,
                "annotations": _annotation(defaults.sync_wave("nodePool")),
            },
            "spec": {
                "endpointSelector": {
                    "matchLabels": {
                        "kubevirt.io": "virt-launcher",
                    },
                },
                "egress": [{
                    "toEntities": ["host"],
                    "toPorts": [{
                        "ports": [{"port": "443", "protocol": "TCP"}],
                    }],
                }],
            },
        })

        files[f"{hc_dir}/cdi-importer-ingress-cnp.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumNetworkPolicy",
            "metadata": {
                "name": "allow-cdi-controller-to-importer",
                "namespace": kubevirt_machine_namespace,
                "annotations": _annotation(defaults.sync_wave("nodePool")),
            },
            "spec": {
                "endpointSelector": {
                    "matchLabels": {
                        "cdi.kubevirt.io": "importer",
                        "app": "containerized-data-importer",
                    },
                },
                "ingress": [{
                    "fromEndpoints": [{
                        "matchLabels": {
                            "io.kubernetes.pod.namespace": "openshift-cnv",
                            "io.cilium.k8s.policy.serviceaccount": "cdi-sa",
                            "cdi.kubevirt.io": "cdi-deployment",
                        },
                    }],
                    "toPorts": [{
                        "ports": [{"port": "8443", "protocol": "TCP"}],
                    }],
                }],
            },
        })

        if hosted_identity_provider(cluster, hcp.name) and sealed_secret_present(defaults, cluster.name, hcp.name):
            common_kustomization_resources.append("console-login-sealedsecret.yaml")
        files[f"{hc_dir}/kustomization.yaml"] = _kustomization(
            common_kustomization_resources,
            [] if storage_consumers_held else storage_cutover_patches,
        )

    root_resources = sorted(hc_dirs)
    dns_records = _control_plane_ingress_dns_records(cluster)
    if dns_records:
        files[f"{base}/control-plane-ingress-dns-records.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {
                "name": "hcp-control-plane-ingress-dns-records",
                "namespace": gitops_ns,
                "annotations": _annotation(defaults.sync_wave("hostedCluster")),
            },
            "data": {
                "records.yaml": yaml.safe_dump({"records": dns_records}, sort_keys=False),
            },
        })
        root_resources.append("control-plane-ingress-dns-records.yaml")

    # --- ArgoCD cluster-secret sync: renders RBAC + Job that reads HyperShift
    # <hosted-cluster>-admin-kubeconfig secrets and creates ArgoCD cluster secrets dynamically.
    # This enables central ArgoCD to deploy into hosted cluster APIs without
    # committing kubeconfig material to Git. ---
    if cluster.hosted_clusters and hcp_phase in {"control-planes", "workers"} or (
        cluster.hosted_clusters and hcp_phase == "legacy"
        and cluster.storage.get("activation") not in {"consumer-hold", "provider-cutover"}
    ):
        bootstrap_wave = (defaults.sync_wave("hostedCluster") if hcp_phase != "legacy"
                          else defaults.sync_wave("argocdClusterSecret"))
        sa_name = "argocd-cluster-secret-sync"
        cr_name = "argocd-cluster-secret-sync"

        # RBAC: namespace-scoped Roles for least-privilege access.
        # - Read <hosted-cluster>-admin-kubeconfig from each HCP namespace
        # - Create/update ArgoCD cluster secrets in openshift-gitops
        rbac_docs = []

        # Role in openshift-gitops: manage argocd-cluster-* secrets
        rbac_docs.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "Role",
            "metadata": {
                "name": cr_name,
                "namespace": gitops_ns,
                "annotations": _annotation(bootstrap_wave),
            },
            "rules": [{
                "apiGroups": [""],
                "resources": ["secrets"],
                "verbs": ["get", "create", "update", "patch"],
            }],
        })
        rbac_docs.append({
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": cr_name,
                "namespace": gitops_ns,
                "annotations": _annotation(bootstrap_wave),
            },
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "Role",
                "name": cr_name,
            },
            "subjects": [{
                "kind": "ServiceAccount",
                "name": sa_name,
                "namespace": gitops_ns,
            }],
        })

        # Role per HCP namespace: read admin-kubeconfig
        for hcp in cluster.hosted_clusters:
            hcp_ns = f"clusters-{hcp.name}"
            rbac_docs.append({
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {
                    "name": cr_name,
                    "namespace": hcp_ns,
                    "annotations": _annotation(bootstrap_wave),
                },
                "rules": [{
                    "apiGroups": [""],
                    "resources": ["secrets"],
                    "verbs": ["get"],
                    "resourceNames": [f"{hcp.name}-admin-kubeconfig"],
                }],
            })
            rbac_docs.append({
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": cr_name,
                    "namespace": hcp_ns,
                    "annotations": _annotation(bootstrap_wave),
                },
                "roleRef": {
                    "apiGroup": "rbac.authorization.k8s.io",
                    "kind": "Role",
                    "name": cr_name,
                },
                "subjects": [{
                    "kind": "ServiceAccount",
                    "name": sa_name,
                    "namespace": gitops_ns,
                }],
            })

        files[f"{base}/argocd-cluster-sync-rbac.yaml"] = HEADER + "".join(
            _k8s_yaml(doc) for doc in rbac_docs
        )

        # Build HCP list for the sync script
        hcp_entries = []
        for hcp in cluster.hosted_clusters:
            hcp_ns = f"clusters-{hcp.name}"
            hcp_entries.append(f"{hcp.name}:{hcp_ns}")
        hcp_list = ",".join(hcp_entries)

        sync_script = f"""set -euo pipefail

HCP_LIST="{hcp_list}"
GITOPS_NS="{gitops_ns}"

for entry in $(echo "$HCP_LIST" | tr ',' ' '); do
  HCP_NAME="${{entry%%:*}}"
  HCP_NS="${{entry#*:}}"
  SECRET_NAME="argocd-cluster-$HCP_NAME"

  echo "Syncing ArgoCD cluster secret for $HCP_NAME (ns=$HCP_NS)"

  # Read admin kubeconfig from HyperShift
  KUBECONFIG_SECRET="$HCP_NAME-admin-kubeconfig"
  KC=""
  for attempt in $(seq 1 180); do
    KC=$(oc get secret "$KUBECONFIG_SECRET" -n "$HCP_NS" -o jsonpath='{{.data.kubeconfig}}' 2>/dev/null || true)
    [ -n "$KC" ] && break
    sleep 10
  done
  if [ -z "$KC" ]; then
    echo "ERROR: $KUBECONFIG_SECRET absent in $HCP_NS after 30 minutes"
    exit 1
  fi

  # Argo runs on the management cluster. Its remote client uses the stable
  # internal HCP API Service, avoiding a management-to-tenant fabric hairpin.
  # The admin kubeconfig CA also validates this Service's serving certificate.
  KC_DECODED=$(echo "$KC" | base64 -d)
  SERVER="https://kube-apiserver.$HCP_NS-$HCP_NAME.svc:6443"
  CA_DATA=$(echo "$KC_DECODED" | grep 'certificate-authority-data:' | awk '{{print $2}}')
  CERT_DATA=$(echo "$KC_DECODED" | grep 'client-certificate-data:' | awk '{{print $2}}')
  KEY_DATA=$(echo "$KC_DECODED" | grep 'client-key-data:' | awk '{{print $2}}')

  # Build ArgoCD cluster secret config JSON
  CONFIG=$(cat <<EOCFG
{{
  "tlsClientConfig": {{
    "caData": "$CA_DATA",
    "certData": "$CERT_DATA",
    "keyData": "$KEY_DATA"
  }}
}}
EOCFG
)

  # Create or update ArgoCD cluster secret without embedding multi-line JSON in YAML.
  oc create secret generic "$SECRET_NAME" \
    -n "$GITOPS_NS" \
    --type=Opaque \
    --from-literal=name="$HCP_NAME" \
    --from-literal=server="$SERVER" \
    --from-literal=config="$CONFIG" \
    --dry-run=client -o yaml \
    | oc label --local -f - argocd.argoproj.io/secret-type=cluster -o yaml \
    | oc annotate --local -f - managed-by=render-gitops.py -o yaml \
    | oc apply -f -

  echo "OK: $SECRET_NAME synced"
done
"""

        # A Job hook alone does not mark an already-synced Application OutOfSync.
        # Bind its script to a regular resource at the same wave so script changes
        # select the wave and Argo replaces the completed hook.
        files[f"{base}/argocd-cluster-sync-sa.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": sa_name,
                "namespace": gitops_ns,
                "annotations": {
                    **_annotation(bootstrap_wave),
                    api_label("bootstrap-script-sha256"): hashlib.sha256(
                        sync_script.encode("utf-8")
                    ).hexdigest(),
                },
            },
        })

        files[f"{base}/argocd-cluster-sync-job.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": "argocd-cluster-secret-sync",
                "namespace": gitops_ns,
                "annotations": {
                    **_annotation(bootstrap_wave),
                    "argocd.argoproj.io/hook": "Sync" if hcp_phase != "legacy" else "PostSync",
                    "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
                },
            },
            "spec": {
                "backoffLimit": 3,
                "activeDeadlineSeconds": 3900 if hcp_phase != "legacy" else 300,
                "template": {
                    "metadata": {"labels": {"app": "argocd-cluster-secret-sync"}},
                    "spec": {
                        "serviceAccountName": sa_name,
                        "restartPolicy": "Never",
                        "containers": [{
                            "name": "sync",
                            "image": ose_tools_image,
                            "command": ["/bin/bash", "-c"],
                            "args": [sync_script],
                        }],
                    },
                },
            },
        })

        root_resources.extend([
            "argocd-cluster-sync-sa.yaml",
            "argocd-cluster-sync-rbac.yaml",
            "argocd-cluster-sync-job.yaml",
        ])

        root_resources.sort()

        router_labeler_sa = "hcp-router-service-labeler"
        router_labeler_docs = [{
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": router_labeler_sa,
                "namespace": gitops_ns,
                "annotations": _annotation(bootstrap_wave),
            },
        }]
        router_entries = []
        for hcp in cluster.hosted_clusters:
            if not hcp.control_plane_ingress_load_balancer_vip:
                raise ValueError(
                    f"{hcp.name}: no controlPlaneIngress.loadBalancerVIPRef resolved")
            hcp_control_plane_ns = f"clusters-{hcp.name}-{hcp.name}"
            labels = _control_plane_ingress_service_labels(hcp)
            label_args = " ".join(f"{key}={value}" for key, value in sorted(labels.items()))
            vip_ip = hcp.control_plane_ingress_load_balancer_vip.cidr.split("/", 1)[0]
            annotation_args = f"io.cilium/lb-ipam-ips={vip_ip}"
            router_entries.append(f"{hcp_control_plane_ns}|{label_args}|{annotation_args}")
            if hcp_phase == "legacy":
                router_labeler_docs.extend([
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "Role",
                    "metadata": {
                        "name": router_labeler_sa,
                        "namespace": hcp_control_plane_ns,
                        "annotations": _annotation(bootstrap_wave),
                    },
                    "rules": [{
                        "apiGroups": [""],
                        "resources": ["services"],
                        "resourceNames": ["router"],
                        "verbs": ["get", "patch", "update"],
                    }],
                },
                {
                    "apiVersion": "rbac.authorization.k8s.io/v1",
                    "kind": "RoleBinding",
                    "metadata": {
                        "name": router_labeler_sa,
                        "namespace": hcp_control_plane_ns,
                        "annotations": _annotation(bootstrap_wave),
                    },
                    "roleRef": {
                        "apiGroup": "rbac.authorization.k8s.io",
                        "kind": "Role",
                        "name": router_labeler_sa,
                    },
                    "subjects": [{
                        "kind": "ServiceAccount",
                        "name": router_labeler_sa,
                        "namespace": gitops_ns,
                    }],
                },
                ])

        if hcp_phase != "legacy" and router_entries:
            router_labeler_docs.extend([{
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRole",
                "metadata": {"name": f"{cluster.name}-{router_labeler_sa}"},
                "rules": [{
                    "apiGroups": [""], "resources": ["services"],
                    "resourceNames": ["router"], "verbs": ["get", "patch", "update"],
                }],
            }, {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {"name": f"{cluster.name}-{router_labeler_sa}"},
                "roleRef": {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole",
                            "name": f"{cluster.name}-{router_labeler_sa}"},
                "subjects": [{"kind": "ServiceAccount", "name": router_labeler_sa,
                              "namespace": gitops_ns}],
            }])

        if router_entries:
            router_entries_text = "\n".join(router_entries)
            router_labeler_script = f"""set -euo pipefail

label_router_service() {{
  local namespace="$1"
  local label_args="$2"
  local annotation_args="$3"
  local attempts=0
  local max_attempts=120

  until oc -n "$namespace" get service router >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if [ "$attempts" -ge "$max_attempts" ]; then
      echo "ERROR: timed out waiting for $namespace/router"
      return 1
    fi
    sleep 10
  done

  oc -n "$namespace" label service router $label_args --overwrite
  oc -n "$namespace" annotate service router $annotation_args --overwrite
  echo "OK: labeled $namespace/router with $label_args"
  echo "OK: annotated $namespace/router with $annotation_args"
}}

while IFS='|' read -r namespace label_args annotation_args; do
  [ -n "$namespace" ] || continue
  label_router_service "$namespace" "$label_args" "$annotation_args"
done <<'ROUTERS'
{router_entries_text}
ROUTERS
"""

            files[f"{base}/router-service-labeler-rbac.yaml"] = HEADER + "".join(
                _k8s_yaml(doc) for doc in router_labeler_docs
            )
            files[f"{base}/router-service-labeler-job.yaml"] = HEADER + _k8s_yaml({
                "apiVersion": "batch/v1",
                "kind": "Job",
                "metadata": {
                    "name": "hcp-router-service-labeler",
                    "namespace": gitops_ns,
                    "annotations": {
                        **_annotation(bootstrap_wave),
                        "argocd.argoproj.io/hook": "Sync" if hcp_phase != "legacy" else "PostSync",
                        "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
                    },
                },
                "spec": {
                    "backoffLimit": 5,
                    "activeDeadlineSeconds": 2460 if hcp_phase != "legacy" else 600,
                    "template": {
                        "metadata": {"labels": {"app": "hcp-router-service-labeler"}},
                        "spec": {
                            "serviceAccountName": router_labeler_sa,
                            "restartPolicy": "Never",
                            "containers": [{
                                "name": "labeler",
                                "image": ose_tools_image,
                                "command": ["/bin/bash", "-c"],
                                "args": [router_labeler_script],
                                "resources": {
                                    "requests": {"cpu": "10m", "memory": "64Mi"},
                                    "limits": {"cpu": "100m", "memory": "128Mi"},
                                },
                            }],
                        },
                    },
                },
            })
            root_resources.extend([
                "router-service-labeler-rbac.yaml",
                "router-service-labeler-job.yaml",
            ])
            root_resources.sort()

    # hosted-clusters root kustomization
    files[f"{base}/kustomization.yaml"] = _kustomization(root_resources)

    return files
