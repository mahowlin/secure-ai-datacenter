from .selected_inputs import api_group
from resolver import legacy_management_cilium_devices
from .selected_inputs import api_group, api_label, domain_label

import copy
from management_exporter import management_exporter_parameters
from .registry_exposure import registry_exposure, selected_registry_endpoint

from .common import *
from .tenant_observability import (
    _tenant_exporter_scc_binding,
    _tenant_k8s_exporter_values,
    _vector_s3_exporter_values,
    timescape_ingest_hosts,
)


def _clife_package_source(package, defaults: ResolvedDefaults, values: dict) -> dict:
    registry = defaults.registry_url.rstrip("/")
    prefix = f"{registry}/"
    if not package.repository.startswith(prefix):
        raise ValueError("CLIFE package repository must be below the configured registry")
    return {
        "repoURL": registry,
        "chart": package.repository.removeprefix(prefix),
        "targetRevision": package.version,
        "helm": {"values": yaml.safe_dump(values, sort_keys=False)},
    }


def _clife_package_annotations(package) -> dict[str, str]:
    return {
        api_label("clife-package-digest"): package.oci_digest,
        api_label("clife-receipt-sha256"): package.receipt_sha256,
        api_label("activation"): "manual-maintenance-window",
    }


def _management_clife_values(cluster: ResolvedOCPCluster) -> dict:
    package = cluster.release.clife_package
    scenario = package.scenarios[cluster.management_clife.package_scenario]
    values = scenario["values"]
    return {
        "mode": "management",
        "clife": {
            "image": package.controller_image,
            "kubernetesServiceHost": values["k8sServiceHost"],
            "kubernetesServicePort": values["k8sServicePort"],
            "developmentMode": False,
        },
        "ciliumConfig": {},
        "networkOperator": {},
    }


def _hosted_clife_values(cluster: ResolvedOCPCluster,
                         hcp: ResolvedHCPCluster) -> dict:
    package = cluster.tenant_guest_cni.package
    scenario_name = cluster.tenant_guest_cni.package_scenarios[hcp.name]
    scenario = package.scenarios[scenario_name]
    values = copy.deepcopy(scenario["values"])
    values.setdefault("cluster", {})["name"] = hcp.name
    values["k8sServiceHost"] = _hcp_service_hostname(cluster, hcp, "APIServer")
    if hcp.cluster_networks:
        operator = values.setdefault("ipam", {}).setdefault("operator", {})
        operator["clusterPoolIPv4PodCIDRList"] = [cn["cidr"] for cn in hcp.cluster_networks]
        operator["clusterPoolIPv4MaskSize"] = (
            hcp.cluster_networks[0].get("hostPrefix", 23)
            if hcp.cluster_networks else 23
        )
    service_networks = [
        item["cidr"] if isinstance(item, dict) else item
        for item in hcp.service_networks
    ]
    return {
        "mode": "hosted",
        "clife": {
            "image": package.controller_image,
            "kubernetesServiceHost": values["k8sServiceHost"],
            "kubernetesServicePort": values["k8sServicePort"],
            "developmentMode": False,
        },
        "ciliumConfig": values,
        "networkOperator": {
            "managementState": "Managed",
            "clusterNetwork": copy.deepcopy(hcp.cluster_networks),
            "serviceNetwork": service_networks,
            "defaultNetwork": {"type": "Cilium"},
            "deployKubeProxy": False,
        },
    }

def _append_management_exporter_parameters(patches, parameters):
    matches = [p for p in patches if p.get("target", {}).get("kind") == "Application"
               and p.get("target", {}).get("name") == "vector-exporter"]
    if len(matches) != 1:
        raise ValueError("managementExporter requires exactly one owning Vector Application patch")
    operations = yaml.safe_load(matches[0]["patch"])
    if not isinstance(operations, list):
        raise ValueError("managementExporter owning patch must contain JSON patch operations")
    operations.extend({"op": "add", "path": "/spec/source/helm/parameters/-", "value": p}
                      for p in parameters)
    matches[0]["patch"] = yaml.safe_dump(operations, sort_keys=False)


def _render_cluster_entrypoints(cluster: ResolvedOCPCluster,
                                gitops_repo_url: str,
                                gitops_target_revision: str,
                                defaults: ResolvedDefaults) -> dict[str, str]:
    """Render Argo CD Application entrypoints under clusters/."""
    files: dict[str, str] = {}
    parent = cluster.name
    gitops_ns = defaults.namespace("gitops")
    marketplace_ns = defaults.namespace("marketplace")
    timescape_ns = defaults.namespace("timescape")
    package = cluster.release.clife_package if cluster.release else None

    def hcp_app(name: str, path: str, wave: int, sync_options: list[str] | None = None) -> dict:
        options = ["SkipDryRunOnMissingResource=true"]
        if sync_options:
            options.extend(sync_options)
        return {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": f"{parent}-{name}",
                "namespace": gitops_ns,
                "annotations": _annotation(wave),
            },
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": gitops_repo_url,
                    "targetRevision": gitops_target_revision,
                    "path": path,
                },
                "destination": {
                    "server": "https://kubernetes.default.svc",
                    "namespace": gitops_ns,
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": options,
                    "retry": {
                        "limit": 10,
                        "backoff": {
                            "duration": "30s",
                            "factor": 2,
                            "maxDuration": "10m",
                        },
                    },
                },
            },
        }

    release_contract_spec = {
        "releaseRef": cluster.release_ref,
        "ocpVersion": cluster.ocp_version,
        "networkType": cluster.network_type,
        "imagePolicyBundles": {
            "platformDay2": {
                "profile": cluster.release.platform_day2_profile,
                "targetRevision": defaults.platform_day2_target_revision(cluster.release.platform_day2_profile),
            },
        },
    }
    if package:
        release_contract_spec["packages"] = {
            "clife": {
                "repository": package.repository,
                "version": package.version,
                "ociDigest": package.oci_digest,
                "packageSha256": package.package_sha256,
                "contract": {
                    "path": package.contract_path,
                    "sha256": package.contract_sha256,
                },
                "receipt": {
                    "repository": package.receipt_repository,
                    "revision": package.receipt_revision,
                    "path": package.receipt_path,
                    "sha256": package.receipt_sha256,
                },
            }
        }
    else:
        release_contract_spec["manifestBundles"] = {
            "clife": cluster.release.clife_manifest_bundle,
        }

    release_contract = {
        "apiVersion": api_label("v1alpha1"),
        "kind": "ClusterReleaseContract",
        "metadata": {"name": cluster.name},
        "spec": release_contract_spec,
    }
    files[f"clusters/{parent}/release-contract.yaml"] = HEADER + yaml.safe_dump(release_contract, sort_keys=False)

    management_apps = {}
    if cluster.management_clife.enabled:
        management_clife_app = hcp_app(
            "hcp-clife",
            f"apps/hcp/management/clife/overlays/{parent}",
            defaults.sync_wave("hcpOperandsApplication"),
        )
        if cluster.management_clife.lifecycle == "oci":
            management_clife_app["metadata"]["annotations"].update(
                _clife_package_annotations(cluster.release.clife_package)
            )
            management_clife_app["spec"]["source"] = _clife_package_source(
                cluster.release.clife_package, defaults, _management_clife_values(cluster)
            )
            management_clife_app["spec"]["destination"]["namespace"] = "cilium"
            management_clife_app["spec"]["syncPolicy"].pop("automated", None)
        management_apps["hcp-clife.yaml"] = management_clife_app

    hosted_clusters_app = hcp_app(
        "hcp-hosted-clusters",
        f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{parent}",
        defaults.sync_wave("hostedClustersApplication"),
    )
    management_apps.update({
        "hcp-operators.yaml": hcp_app(
            "hcp-operators",
            f"apps/hcp/management/operators/overlays/{parent}",
            defaults.sync_wave("hcpOperatorsApplication"),
        ),
        "hcp-operands.yaml": hcp_app(
            "hcp-operands",
            f"apps/hcp/management/operands/overlays/{parent}",
            defaults.sync_wave("hcpOperandsApplication"),
        ),
        "hcp-parent-network.yaml": hcp_app(
            "hcp-parent-network",
            f"apps/hcp/management/parent-network/overlays/{parent}",
            defaults.sync_wave("parentNetworkApplication"),
        ),
        "hcp-hosted-clusters.yaml": hosted_clusters_app,
    })
    if cluster.hosted_cluster_lifecycle.get("managementPodCapacity"):
        for filename, suffix, path, sync_options in (
            (
                "hcp-pod-density-policy.yaml", "hcp-pod-density-policy",
                f"apps/hcp/management/operators/overlays/{parent}/pod-density-policy",
                ["ServerSideApply=true"],
            ),
            (
                "hcp-pod-density.yaml", "hcp-pod-density",
                f"apps/hcp/management/operators/overlays/{parent}/pod-density",
                [],
            ),
        ):
            app = hcp_app(suffix, path, defaults.sync_wave("hcpOperatorsApplication"), sync_options)
            app["spec"]["syncPolicy"].pop("automated", None)
            app["metadata"]["annotations"][api_label("activation")] = (
                "manual-pause-policy-observed-before-kubelet"
            )
            management_apps[filename] = app
    if cluster.local_registry:
        registry_namespace = cluster.local_registry["namespace"]
        registry_app = hcp_app(
            "hcp-local-registry",
            f"apps/hcp/management/local-registry/overlays/{parent}",
            defaults.sync_wave("localRegistryApplication"),
            ["ServerSideApply=true"],
        )
        registry_app["spec"]["destination"]["namespace"] = registry_namespace
        registry_app["spec"]["syncPolicy"]["automated"] = {"prune": False, "selfHeal": True}
        management_apps["hcp-local-registry.yaml"] = registry_app
        trial = cluster.local_registry.get("ocMirror") or {}
        if trial.get("publish") is True:
            trial_app = hcp_app(
                trial["applicationName"],
                f"apps/hcp/management/oc-mirror-trial/overlays/{parent}",
                defaults.sync_wave("localRegistryApplication"),
                ["ServerSideApply=true"],
            )
            trial_app["spec"]["destination"]["namespace"] = trial["namespace"]
            # Publishing the Application does not start cluster-wide CRDs or
            # image writes. The operator owner approves its exact resource sync separately.
            trial_app["spec"]["syncPolicy"].pop("automated", None)
            management_apps["hcp-oc-mirror-trial.yaml"] = trial_app
    if cluster.in_cluster_load_balancer.enabled:
        for filename, application_name, stage, wave_name in (
            (
                "hcp-ilb-router-t1-placement.yaml",
                f"{parent}-ilb-router-t1-placement",
                "router-t1",
                "ilbRouterPlacementApplication",
            ),
            (
                "hcp-ilb-t2-placement.yaml",
                f"{parent}-ilb-t2-placement",
                "t2",
                "ilbT2PlacementApplication",
            ),
        ):
            app = hcp_app(
                application_name.removeprefix(f"{parent}-"),
                f"apps/hcp/management/ilb-install/overlays/{parent}/{stage}",
                defaults.sync_wave(wave_name),
            )
            app["metadata"]["annotations"].update({
                api_label("activation"): "manual-maintenance-window",
                api_label("availability"): "single-t2-non-ha",
            })
            app["spec"]["syncPolicy"].pop("automated", None)
            management_apps[filename] = app
    published_management_apps = set(management_apps)
    for fname, app in management_apps.items():
        files[f"clusters/{parent}/{fname}"] = HEADER + _k8s_yaml(app)

    hosted_entrypoints = []
    for hcp in sorted(cluster.hosted_clusters, key=lambda item: item.name):
        hosted_entrypoints.append(f"{hcp.name}-platform-catalogsources.yaml")
        if hcp.network_type == "Cilium":
            hosted_entrypoints.append(f"{hcp.name}-sealed-secrets-controller.yaml")
            hosted_entrypoints.append(f"{hcp.name}-sealed-secrets.yaml")
            # Guest-CNI installer is a separate entitled/runtime prerequisite
            # in this public candidate; do not reference an un-emitted object.
            if hcp.timescape_exporter_enrollment != "excluded":
                hosted_entrypoints.append(f"{hcp.name}-timescape-exporter-scc.yaml")
                hosted_entrypoints.append(f"{hcp.name}-hubble-k8s-exporter.yaml")
                hosted_entrypoints.append(f"{hcp.name}-timescape-s3-exporter.yaml")
        hosted_entrypoints.append(f"{hcp.name}-tenant-networking.yaml")
    management_bond = cluster.management_interface.bond
    cilium_devices = legacy_management_cilium_devices(cluster)
    package = cluster.release.clife_package if cluster.release else None
    cilium_parameters = [
        ("clusterName", cluster.name),
        ("l2.device", management_bond),
        ("ciliumConfig.restartJobImage", defaults.gitops_image_reference("oseTools")),
    ]
    if package:
        cilium_parameters.append(("ciliumConfig.enabled", "false"))
    else:
        cilium_parameters.extend([
            ("ciliumConfig.apiHost", f"api-int.{cluster.name}.{cluster.base_domain}"),
            ("ciliumConfig.directRoutingDevice", management_bond),
        ])
        for idx, device in enumerate(cilium_devices):
            cilium_parameters.append((f"ciliumConfig.devices[{idx}]", device))
        if defaults.ocp_cluster_network:
            cilium_parameters.append(("ciliumConfig.ipam.cidr", defaults.ocp_cluster_network[0]["cidr"]))
    management_lb_pool = cluster.parent_networking.management_lb_pool if cluster.parent_networking else None
    if management_lb_pool:
        cilium_parameters.extend([
            ("ipPool.enabled", "true"),
            ("ipPool.name", management_lb_pool.name),
            ("ipPool.start", management_lb_pool.start),
            ("ipPool.stop", management_lb_pool.stop),
        ])
    else:
        cilium_parameters.append(("ipPool.enabled", "false"))
    cilium_parameters.extend([
        ("bgp.enabled", "true"),
        ("bgp.routeImport.enabled", "true"),
        ("bgp.bfd.enabled", "true"),
    ])
    if not package and cluster.cilium_config.gateway_api_enabled is not None:
        cilium_parameters.append(("gatewayAPI.enabled", str(cluster.cilium_config.gateway_api_enabled).lower()))
    for component, override in cluster.cilium_config.image_overrides.items():
        if not override.enabled:
            continue
        base = f"imageOverride.{component}"
        cilium_parameters.extend([
            (f"{base}.enabled", "true"),
            (f"{base}.repository", override.repository),
            (f"{base}.tag", override.tag),
        ])
        if component == "operator":
            cilium_parameters.append((f"{base}.genericDigest", override.generic_digest))
            if override.suffix:
                cilium_parameters.append((f"{base}.suffix", override.suffix))
        else:
            cilium_parameters.append((f"{base}.digest", override.digest))

    cilium_patch = [
        {
            "op": "add",
            "path": "/spec/source/helm/parameters/-",
            "value": {"name": name, "value": str(value)},
        }
        for name, value in cilium_parameters
    ]
    if package:
        management_spec = resolved_management_cilium_config_spec(cluster)
        ilb_profile = cluster.in_cluster_load_balancer.cilium_config_profile
        cilium_patch.extend([
            {
                "op": "replace",
                "path": "/metadata/annotations/argocd.argoproj.io~1sync-wave",
                "value": str(defaults.sync_wave("ciliumReleaseApplication")),
            },
            {
                "op": "add",
                "path": "/spec/source/helm/valuesObject",
                "value": {
                    **copy.deepcopy(cluster.target_cilium_config_profile.chart_values),
                    "l2": {
                        **copy.deepcopy(cluster.target_cilium_config_profile.chart_values.get("l2", {})),
                        "serviceSelector": copy.deepcopy(management_lb_pool.service_selector),
                    },
                    "ipPool": {"serviceSelector": copy.deepcopy(management_lb_pool.service_selector)},
                    "identity": {"apiGroup": api_group()},
                    "renderedCiliumConfig": {
                        "enabled": True,
                        "packageDigest": package.oci_digest,
                        "receiptSha256": package.receipt_sha256,
                        "profileRef": ilb_profile.name,
                        "profileSha256": ilb_profile.source_sha256,
                        "spec": management_spec,
                        "restartJobImage": cluster.in_cluster_load_balancer.placement_job_image,
                    }
                },
            },
            {
                "op": "add",
                "path": f"/metadata/annotations/{api_group().replace('/', '~1')}~1clife-package-digest",
                "value": package.oci_digest,
            },
            {
                "op": "add",
                "path": f"/metadata/annotations/{api_group().replace('/', '~1')}~1clife-receipt-sha256",
                "value": package.receipt_sha256,
            },
            {
                "op": "add",
                "path": f"/metadata/annotations/{api_group().replace('/', '~1')}~1cilium-config-profile",
                "value": ilb_profile.name,
            },
            {
                "op": "add",
                "path": f"/metadata/annotations/{api_group().replace('/', '~1')}~1cilium-config-profile-sha256",
                "value": ilb_profile.source_sha256,
            },
            {
                "op": "add",
                "path": f"/metadata/annotations/{api_group().replace('/', '~1')}~1activation",
                "value": "manual-maintenance-window",
            },
            {"op": "remove", "path": "/spec/syncPolicy/automated"},
        ])

    hubble_timescape_patch_lines = []
    central_timescape = cluster.observability.get("centralTimescape", {})
    cilium_tenants = timescape_ingest_hosts(cluster)
    if cilium_tenants:
        object_store = defaults.gitops_timescape["objectStore"]
        bucket_uri = (
            f"s3://{object_store['bucket']}?endpoint={object_store['endpoint']}"
            f"&disableSSL={str(object_store['disableSSL']).lower()}"
            f"&s3ForcePathStyle={str(object_store['forcePathStyle']).lower()}"
        )
        bucket_sources = ["hubble.log", "k8s-events.log", "connectionlog.log"]
        hubble_timescape_parameters = [
            {"name": "config.buckets[0].uri", "value": bucket_uri},
            {"name": "config.buckets[0].flows.pattern", "value": "hubble\\.log"},
            {"name": "config.buckets[0].flows.disabled", "value": "false"},
            {"name": "config.buckets[0].k8sEvents.pattern", "value": "k8s-events\\.log"},
            {"name": "config.buckets[0].k8sEvents.disabled", "value": "false"},
            {"name": "config.buckets[0].connectionLogs.pattern", "value": "connectionlog\\.log"},
            {"name": "config.buckets[0].connectionLogs.disabled", "value": "false"},
        ]
        path_idx = 0
        for hcp_name in cilium_tenants:
            for source_name in bucket_sources:
                hubble_timescape_parameters.extend([
                    {
                        "name": f"config.buckets[0].paths[{path_idx}].prefixPattern",
                        "value": f"hubble-events/{hcp_name}/%Y/%m/%d/{source_name}/",
                    },
                    {
                        "name": f"config.buckets[0].paths[{path_idx}].overrideClusterName",
                        "value": hcp_name,
                    },
                ])
                path_idx += 1
        if central_timescape.get("enabled"):
            hubble_timescape_parameters.append({
                "name": "clickhouse.cluster.volumes.data.storageClassName",
                "value": central_timescape["storageClass"],
            })
            foundation = central_timescape.get("foundation", {})
            for component in foundation.get("serviceMonitors", []):
                key = ("clickhouse.cluster.prometheus.serviceMonitor.enabled"
                       if component == "clickhouse" else
                       f"{component}.prometheus.serviceMonitor.enabled")
                hubble_timescape_parameters.append({"name": key, "value": "true"})
            for component in foundation.get("alertRules", []):
                key = ("clickhouse.cluster.prometheus.prometheusRule.enabled"
                       if component == "clickhouse" else
                       f"{component}.prometheus.prometheusRule.enabled")
                hubble_timescape_parameters.append({"name": key, "value": "true"})
            retention = foundation.get("retention", {})
            if retention:
                hubble_timescape_parameters.extend([
                    {"name": "migrate.ttl", "value": retention["ttl"]},
                    {"name": "trimmer.flowsCount", "value": str(retention["flowRowLimit"])},
                ])
            management_pool = (cluster.parent_networking.management_lb_pool
                               if cluster.parent_networking else None)
            if management_pool:
                for label, value in sorted(
                    management_pool.service_selector.get("matchLabels", {}).items()
                ):
                    hubble_timescape_parameters.append({
                        "name": "ui.service.labels." + label.replace("\\", "\\\\").replace(".", "\\."),
                        "value": value,
                    })
        hubble_timescape_patch_lines = [
            "- op: add",
            "  path: /spec/source/helm/parameters",
            "  value:",
            *[f"    {line}" for line in yaml.safe_dump(hubble_timescape_parameters, sort_keys=False).rstrip().splitlines()],
        ]

    activation = cluster.storage.get("activation")
    patches = [
        {
            "target": {"kind": "Application", "name": "cilium-config"},
            "patch": yaml.safe_dump(cilium_patch, sort_keys=False),
        },
        {
            "target": {"kind": "Application", "name": "vector-exporter"},
            "patch": "\n".join([
                "- op: add",
                "  path: /spec/source/helm/parameters/-",
                "  value:",
                "    name: clusterName",
                f"    value: \"{parent}\"",
            ]),
        },
    ]
    if "managementExporter" in cluster.observability:
        parameters = management_exporter_parameters(cluster.observability["managementExporter"])
        _append_management_exporter_parameters(patches, parameters)
    if activation != "staged" and not (
        activation == "provider-cutover" and central_timescape.get("enabled")
    ):
        patches.append({
            "target": {"kind": "Application", "name": "hubble-timescape"},
            "patch": "\n".join([
                "apiVersion: argoproj.io/v1alpha1",
                "kind: Application",
                "metadata:",
                "  name: hubble-timescape",
                "  namespace: openshift-gitops",
                "$patch: delete",
            ]),
        })
    if hubble_timescape_patch_lines and (
        activation == "staged" or (activation == "provider-cutover" and central_timescape.get("enabled"))
    ):
        patches.append({
            "target": {"kind": "Application", "name": "hubble-timescape"},
            "patch": "\n".join(hubble_timescape_patch_lines),
        })

    root_resources = ["../_types/hcp-management"]
    if activation == "provider-cutover":
        # Portworx remains manual until the commander explicitly synchronizes it.
        root_resources.append("storage-portworx.yaml")
    root_resources.extend(hosted_entrypoints)
    root_resources.extend(sorted(published_management_apps))
    files[f"clusters/{parent}/kustomization.yaml"] = _kustomization(
        root_resources, patches=patches
    )

    # Tenant cluster entrypoints
    for hcp in cluster.hosted_clusters:
        tenant_catalogs_app = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": f"{hcp.name}-platform-catalogsources",
                "namespace": gitops_ns,
                "annotations": _annotation(defaults.sync_wave("tenantCatalogsApplication")),
                "finalizers": ["resources-finalizer.argocd.argoproj.io"],
            },
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": gitops_repo_url,
                    "targetRevision": gitops_target_revision,
                    "path": "apps/platform-catalogsources",
                },
                "destination": {
                    "name": hcp.name,
                    "namespace": marketplace_ns,
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["SkipDryRunOnMissingResource=true"],
                    "retry": {
                        "limit": 10,
                        "backoff": {
                            "duration": "30s",
                            "factor": 2,
                            "maxDuration": "10m",
                        },
                    },
                },
            },
        }
        files[f"clusters/{parent}/{hcp.name}-platform-catalogsources.yaml"] = HEADER + _k8s_yaml(tenant_catalogs_app)
        files[f"clusters/{hcp.name}/platform-catalogsources.yaml"] = HEADER + _k8s_yaml(tenant_catalogs_app)

        if hcp.network_type == "Cilium":
            sealed_secrets_controller_app_name = f"{hcp.name}-sealed-secrets-controller"
            sealed_secrets_controller_app = {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": sealed_secrets_controller_app_name,
                    "namespace": gitops_ns,
                    "annotations": _annotation(defaults.sync_wave("tenantSealedSecretsApplication")),
                    "finalizers": ["resources-finalizer.argocd.argoproj.io"],
                },
                "spec": {
                    "project": "default",
                    "source": {
                        "repoURL": gitops_repo_url,
                        "targetRevision": gitops_target_revision,
                        "path": "apps/sealed-secrets-controller",
                    },
                    "destination": {"name": hcp.name},
                    "syncPolicy": {
                        "automated": {"prune": False, "selfHeal": True},
                        "syncOptions": ["SkipDryRunOnMissingResource=true"],
                        "retry": {
                            "limit": 10,
                            "backoff": {
                                "duration": "30s",
                                "factor": 2,
                                "maxDuration": "10m",
                            },
                        },
                    },
                },
            }
            files[f"clusters/{parent}/{sealed_secrets_controller_app_name}.yaml"] = HEADER + _k8s_yaml(sealed_secrets_controller_app)
            files[f"clusters/{hcp.name}/{sealed_secrets_controller_app_name}.yaml"] = HEADER + _k8s_yaml(sealed_secrets_controller_app)

            sealed_secrets_app_name = f"{hcp.name}-sealed-secrets"
            sealed_secrets_app = {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {
                    "name": sealed_secrets_app_name,
                    "namespace": gitops_ns,
                    "annotations": _annotation(defaults.sync_wave("tenantSealedSecretsApplication")),
                    "finalizers": ["resources-finalizer.argocd.argoproj.io"],
                },
                "spec": {
                    "project": "default",
                    "source": {
                        "repoURL": gitops_repo_url,
                        "targetRevision": gitops_target_revision,
                        "path": f"apps/sealed-secrets/{hcp.name}/hubble-timescape",
                        "directory": {
                            "recurse": True,
                            "include": "*.yaml",
                            "exclude": "README.md",
                        },
                    },
                    "destination": {"name": hcp.name},
                    "syncPolicy": {
                        "automated": {"prune": True, "selfHeal": True},
                        "syncOptions": ["CreateNamespace=true"],
                        "retry": {
                            "limit": 10,
                            "backoff": {
                                "duration": "30s",
                                "factor": 2,
                                "maxDuration": "10m",
                            },
                        },
                    },
                },
            }
            files[f"clusters/{parent}/{sealed_secrets_app_name}.yaml"] = HEADER + _k8s_yaml(sealed_secrets_app)
            files[f"clusters/{hcp.name}/{sealed_secrets_app_name}.yaml"] = HEADER + _k8s_yaml(sealed_secrets_app)

            if hcp.timescape_exporter_enrollment != "excluded":
                scc_app_name = f"{hcp.name}-timescape-exporter-scc"
                scc_app = {
                    "apiVersion": "argoproj.io/v1alpha1",
                    "kind": "Application",
                    "metadata": {
                        "name": scc_app_name,
                        "namespace": gitops_ns,
                        "annotations": _annotation(defaults.sync_wave("tenantObservabilityApplication")),
                        "finalizers": ["resources-finalizer.argocd.argoproj.io"],
                    },
                    "spec": {
                        "project": "default",
                        "source": {
                            "repoURL": gitops_repo_url,
                            "targetRevision": gitops_target_revision,
                            "path": f"apps/hcp/tenant/observability-scc/overlays/{hcp.name}",
                        },
                        "destination": {"name": hcp.name},
                        "syncPolicy": {
                            "automated": {"prune": True, "selfHeal": True},
                            "syncOptions": ["SkipDryRunOnMissingResource=true"],
                            "retry": {
                                "limit": 10,
                                "backoff": {
                                    "duration": "30s",
                                    "factor": 2,
                                    "maxDuration": "10m",
                                },
                            },
                        },
                    },
                }
                files[f"clusters/{parent}/{scc_app_name}.yaml"] = HEADER + _k8s_yaml(scc_app)
                files[f"clusters/{hcp.name}/{scc_app_name}.yaml"] = HEADER + _k8s_yaml(scc_app)
                scc_root = f"apps/hcp/tenant/observability-scc/overlays/{hcp.name}"
                files[f"{scc_root}/scc-rolebinding.yaml"] = HEADER + _k8s_yaml(
                    _tenant_exporter_scc_binding(hcp.name, defaults)
                )
                files[f"{scc_root}/kustomization.yaml"] = _kustomization(["scc-rolebinding.yaml"])

                pull_endpoint = selected_registry_endpoint(cluster, hcp)
                exporter_image_pull = hcp.timescape_exporter_image_pull
                if cluster.cache_production.get("mode") == "preceding-registry":
                    exporter_image_pull = ""
                if pull_endpoint:
                    exporter_image_pull = registry_exposure(cluster)["consumer"]["exporterImagePull"]
                if cluster.cache_production.get("roots"):
                    exporter_image_pull = ""
                tenant_observability_apps = [
                    (
                        f"{hcp.name}-hubble-k8s-exporter",
                        "charts/hubble-timescape/charts/hubble-k8s-exporter",
                        _tenant_k8s_exporter_values(hcp.name, defaults),
                    ),
                    (
                        f"{hcp.name}-timescape-s3-exporter",
                        "charts/vector",
                        _vector_s3_exporter_values(hcp.name, defaults, exporter_image_pull,
                                                   hcp.timescape_exporter_metrics,
                                                   pull_endpoint),
                    ),
                ]
                if cluster.cache_production.get("roots"):
                    from hcp_cache import reference as cache_reference
                    image = defaults.gitops_image_values("vector")
                    selected_image = cache_reference(cluster.cache_production, image["repository"] + ":" + str(image["tag"]), consumer=hcp.name in cluster.cache_production["subjects"])
                    repo, digest = selected_image.rsplit("@", 1)
                    tenant_observability_apps[-1][2]["image"] = {"repository": repo, "sha": digest}
                for app_name, chart_path, values in tenant_observability_apps:
                    exporter_manual = (app_name.endswith("-timescape-s3-exporter")
                                       and hcp.timescape_exporter_activation == "manual")
                    app = {
                        "apiVersion": "argoproj.io/v1alpha1",
                        "kind": "Application",
                        "metadata": {
                            "name": app_name,
                            "namespace": gitops_ns,
                            "annotations": _annotation(defaults.sync_wave("tenantObservabilityApplication")),
                            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
                        },
                        "spec": {
                            "project": "default",
                            "source": {
                                "repoURL": gitops_repo_url,
                                "targetRevision": gitops_target_revision,
                                "path": chart_path,
                                "helm": {"values": yaml.safe_dump(values, sort_keys=False)},
                            },
                            "destination": {"name": hcp.name, "namespace": timescape_ns},
                            "syncPolicy": {
                                **({} if exporter_manual else
                                   {"automated": {"prune": True, "selfHeal": True}}),
                                "syncOptions": ["CreateNamespace=true", "SkipDryRunOnMissingResource=true"],
                                "retry": {
                                    "limit": 10,
                                    "backoff": {
                                        "duration": "30s",
                                        "factor": 2,
                                        "maxDuration": "10m",
                                    },
                                },
                            },
                        },
                    }
                    files[f"clusters/{parent}/{app_name}.yaml"] = HEADER + _k8s_yaml(app)
                    files[f"clusters/{hcp.name}/{app_name}.yaml"] = HEADER + _k8s_yaml(app)

        tenant_app = {
            "apiVersion": "argoproj.io/v1alpha1",
            "kind": "Application",
            "metadata": {
                "name": f"{hcp.name}-tenant-networking",
                "namespace": gitops_ns,
                "annotations": _annotation(defaults.sync_wave("tenantApplication")),
            },
            "spec": {
                "project": "default",
                "source": {
                    "repoURL": gitops_repo_url,
                    "targetRevision": gitops_target_revision,
                    "path": f"apps/hcp/tenant/tenant-datapath/overlays/{hcp.name}",
                },
                "destination": {
                    "name": hcp.name,
                    "namespace": "cilium",
                },
                "syncPolicy": {
                    "automated": {"prune": True, "selfHeal": True},
                    "syncOptions": ["SkipDryRunOnMissingResource=true"],
                },
            },
        }
        files[f"clusters/{parent}/{hcp.name}-tenant-networking.yaml"] = HEADER + _k8s_yaml(tenant_app)
        files[f"clusters/{hcp.name}/tenant-networking.yaml"] = HEADER + _k8s_yaml(tenant_app)

        tenant_resources = ["../_types/hcp-tenant", "platform-catalogsources.yaml", "tenant-networking.yaml"]
        if hcp.network_type == "Cilium":
            tenant_resources.extend([
                f"{hcp.name}-sealed-secrets-controller.yaml",
                f"{hcp.name}-sealed-secrets.yaml",
            ])
            if hcp.timescape_exporter_enrollment != "excluded":
                tenant_resources.extend([
                    f"{hcp.name}-timescape-exporter-scc.yaml",
                    f"{hcp.name}-hubble-k8s-exporter.yaml",
                    f"{hcp.name}-timescape-s3-exporter.yaml",
                ])
        files[f"clusters/{hcp.name}/kustomization.yaml"] = _kustomization(
            sorted(tenant_resources)
        )

    return files
