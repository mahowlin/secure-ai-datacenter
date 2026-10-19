from .selected_inputs import api_label, domain_label

from .common import *


def _render_tenant_apps(cluster: ResolvedOCPCluster,
                        fabric_asn: int,
                        defaults: ResolvedDefaults) -> dict[str, str]:
    """Tenant Cilium BGP, LB-IPAM, EGW under tenants/<name>/networking/cilium/.

    Uses native Cilium BGP/LB-IPAM APIs where hosted clusters expose them and
    IsovalentEgressGatewayPolicy for IEP egress gateway HA.
    """
    files: dict[str, str] = {}

    for hcp in cluster.hosted_clusters:
        base = f"apps/hcp/tenant/tenant-datapath/overlays/{hcp.name}"
        cilium_root = f"{base}/networking/cilium"
        cilium_resources = []

        if not hcp.uses_multus_workers:
            files[f"{cilium_root}/kustomization.yaml"] = _kustomization([])
            files[f"{base}/networking/kustomization.yaml"] = _kustomization(["cilium"])
            files[f"{base}/kustomization.yaml"] = _kustomization(["networking"])
            continue

        # --- CiliumBGPClusterConfig: one per HCP with bgpInstances per worker network ---
        if hcp.cilium_bgp_enabled and hcp.resolved_worker_networks:
            bgp_instances = []
            for net in hcp.resolved_worker_networks:
                slug = slugify(net.name)
                peers = []
                for i, peer in enumerate(net.svi_peers):
                    peer_ip = peer.ip.split("/")[0]
                    peers.append({
                        "name": f"aci-vlan{net.vlan}-peer-{i+1}",
                        "peerASN": fabric_asn,
                        "peerAddress": peer_ip,
                        "peerConfigRef": {"name": f"{hcp.name}-{slug}-peer-config"},
                    })
                bgp_instances.append({
                    "name": f"{hcp.name}-{slug}",
                    "localASN": net.cilium_asn,
                    "peers": peers,
                })

            cluster_config = {
                "apiVersion": "isovalent.com/v1",
                "kind": "IsovalentBGPClusterConfig",
                "metadata": {
                    "name": f"{hcp.name}-bgp",
                    "annotations": _annotation(defaults.sync_wave("tenantBgp")),
                },
                "spec": {
                    "nodeSelector": {"matchLabels": {"kubernetes.io/os": "linux"}},
                    "bgpInstances": bgp_instances,
                },
            }
            files[f"{cilium_root}/cluster-config.yaml"] = HEADER + _k8s_yaml(cluster_config)
            cilium_resources.append("cluster-config.yaml")

        # --- CiliumBGPPeerConfig: one per worker network ---
        for net in hcp.resolved_worker_networks if hcp.cilium_bgp_enabled else []:
            slug = slugify(net.name)
            family = {
                "afi": "ipv4",
                "safi": "unicast",
                "advertisements": {"matchLabels": {"advertise": f"{hcp.name}-{slug}"}},
            }
            if cluster.tenant_guest_cni.route_import_enabled and net.import_destinations:
                family["importPolicyRef"] = {"name": f"{hcp.name}-{slug}-import-policy"}

                import_policy = {
                    "apiVersion": "isovalent.com/v1",
                    "kind": "IsovalentBGPPolicy",
                    "metadata": {
                        "name": f"{hcp.name}-{slug}-import-policy",
                        "annotations": _annotation(defaults.sync_wave("tenantBgp")),
                    },
                    "spec": {
                        "import": {
                            "statements": [{
                                "conditions": {
                                    "prefixesV4": {
                                        "matchType": "Or",
                                        "matches": [
                                            _bgp_prefix_match(dest)
                                            for dest in net.import_destinations
                                        ],
                                    },
                                },
                                "actions": {"routeAction": "Accept"},
                            }],
                        },
                    },
                }
                files[f"{cilium_root}/import-policy-{slug}.yaml"] = HEADER + _k8s_yaml(import_policy)
                cilium_resources.append(f"import-policy-{slug}.yaml")

            peer_config = {
                "apiVersion": "isovalent.com/v1",
                "kind": "IsovalentBGPPeerConfig",
                "metadata": {
                    "name": f"{hcp.name}-{slug}-peer-config",
                    "annotations": _annotation(defaults.sync_wave("tenantBgp")),
                },
                "spec": {
                    "timers": {
                        "connectRetryTimeSeconds": 5,
                        "holdTimeSeconds": 9,
                        "keepAliveTimeSeconds": 3,
                    },
                    "gracefulRestart": {
                        "enabled": True,
                        "restartTimeSeconds": 120,
                    },
                    "families": [family],
                },
            }
            files[f"{cilium_root}/peer-config-{slug}.yaml"] = HEADER + _k8s_yaml(peer_config)
            cilium_resources.append(f"peer-config-{slug}.yaml")

        # --- CiliumBGPAdvertisement: one per worker network ---
        for net in hcp.resolved_worker_networks if hcp.cilium_bgp_enabled else []:
            slug = slugify(net.name)
            adv_items = []

            # Advertise LB IPs (ingress)
            if hcp.cilium_advertise_lb:
                for pool in hcp.resolved_lb_pools:
                    if pool.owning_network == net.name:
                        adv_items.append({
                            "advertisementType": "Service",
                            "service": {"addresses": ["LoadBalancerIP"]},
                            "selector": {
                                "matchExpressions": [{
                                    "key": "io.cilium/lb-pool",
                                    "operator": "In",
                                    "values": [pool.name],
                                }],
                            },
                            "attributes": {
                                "communities": {"standard": [f"{net.cilium_asn}:100"]},
                            },
                        })

            # Advertise egress IPs
            if hcp.cilium_advertise_egress:
                for pool in hcp.resolved_egress_pools:
                    if pool.owning_network == net.name:
                        adv_items.append({
                            "advertisementType": "Service",
                            "service": {"addresses": ["LoadBalancerIP"]},
                            "selector": {
                                "matchLabels": {"io.cilium/lb-pool": pool.name},
                            },
                            "attributes": {
                                "communities": {"standard": [f"{net.cilium_asn}:200"]},
                            },
                        })

            # Advertise pod CIDRs
            if hcp.cilium_advertise_pods:
                adv_items.append({"advertisementType": "PodCIDR"})

            if adv_items:
                advertisement = {
                    "apiVersion": "isovalent.com/v1",
                    "kind": "IsovalentBGPAdvertisement",
                    "metadata": {
                        "name": f"{hcp.name}-{slug}-advertisement",
                        "labels": {"advertise": f"{hcp.name}-{slug}"},
                        "annotations": _annotation(defaults.sync_wave("tenantBgp")),
                    },
                    "spec": {"advertisements": adv_items},
                }
                files[f"{cilium_root}/advertisement-{slug}.yaml"] = HEADER + _k8s_yaml(advertisement)
                cilium_resources.append(f"advertisement-{slug}.yaml")

        # LB-IPAM per pool (ingress)
        for pool in hcp.resolved_lb_pools:
            slug = slugify(pool.name)
            lb = {
                "apiVersion": "cilium.io/v2",
                "kind": "CiliumLoadBalancerIPPool",
                "metadata": {
                    "name": f"lb-ipam-{slug}",
                    "annotations": _annotation(defaults.sync_wave("tenantLoadBalancerIpam")),
                    "labels": {
                        "io.cilium/lb-pool": pool.name,
                        "traffic-class": "ingress",
                    },
                },
                "spec": {
                    "blocks": [
                        {"start": pool.allocation_start, "stop": pool.allocation_stop}
                        if pool.allocation_start and pool.allocation_stop
                        else {"cidr": pool.cidr}
                    ],
                    "serviceSelector": {
                        "matchExpressions": [{
                            "key": "io.cilium/lb-pool",
                            "operator": "In",
                            "values": [pool.name],
                        }],
                    },
                },
            }
            fname = f"lb-ipam-{slug}.yaml"
            files[f"{cilium_root}/{fname}"] = HEADER + _k8s_yaml(lb)
            cilium_resources.append(fname)

        # Separately owned hosted application router publication. The
        # IngressController keeps its product-managed NodePort Service; this
        # Service selects its Pods without modifying that controller or Service.
        if hcp.application_ingress:
            exposure = hcp.application_ingress
            app_router_service = {
                "apiVersion": "v1",
                "kind": "Service",
                "metadata": {
                    "name": "router-apps-default",
                    "namespace": "openshift-ingress",
                    "annotations": {
                        **_annotation(defaults.sync_wave("tenantLoadBalancerIpam") + 1),
                        "lbipam.cilium.io/ips": exposure.vip,
                        api_label("apps-wildcard"): exposure.dns_wildcard,
                    },
                    "labels": {
                        "app.kubernetes.io/managed-by": "saif-platform",
                        "io.cilium/lb-pool": exposure.pool_ref,
                    },
                },
                "spec": {
                    "type": "LoadBalancer",
                    "loadBalancerClass": "io.cilium/bgp-control-plane",
                    "externalTrafficPolicy": "Local",
                    "selector": {
                        "ingresscontroller.operator.openshift.io/deployment-ingresscontroller": "default",
                    },
                    "ports": [
                        {"name": "http", "protocol": "TCP", "port": 80, "targetPort": "http"},
                        {"name": "https", "protocol": "TCP", "port": 443, "targetPort": "https"},
                    ],
                },
            }
            files[f"{cilium_root}/router-apps-lb.yaml"] = HEADER + _k8s_yaml(app_router_service)
            cilium_resources.append("router-apps-lb.yaml")

        # LB-IPAM for egress pools (required so BGP advertisements have something to announce)
        for pool in hcp.resolved_egress_pools:
            slug = slugify(pool.name)
            egress_lb = {
                "apiVersion": "cilium.io/v2",
                "kind": "CiliumLoadBalancerIPPool",
                "metadata": {
                    "name": f"lb-ipam-egress-{slug}",
                    "annotations": _annotation(defaults.sync_wave("tenantLoadBalancerIpam")),
                    "labels": {
                        "io.cilium/lb-pool": pool.name,
                        "traffic-class": "egress",
                    },
                },
                "spec": {
                    "blocks": [{"cidr": pool.cidr}],
                    "serviceSelector": {
                        "matchLabels": {"io.cilium/lb-pool": pool.name},
                    },
                },
            }
            fname = f"lb-ipam-egress-{slug}.yaml"
            files[f"{cilium_root}/{fname}"] = HEADER + _k8s_yaml(egress_lb)
            cilium_resources.append(fname)

        # EGW per pool — IsovalentEgressGatewayPolicy with egressCIDRs and egressGroups
        # Destination CIDRs are derived from the owning network's import_destinations
        # (same endpointNetworks source as route import). The destination determines
        # which tenant egress pool SNATs the traffic — a single pod can serve
        # multiple tenants, with each destination triggering the correct SNAT.
        for pool in hcp.resolved_egress_pools:
            slug = slugify(pool.name)
            # Find the owning network to derive destination CIDRs
            owning_net = next(
                (n for n in hcp.resolved_worker_networks if n.name == pool.owning_network),
                None,
            )
            if owning_net and owning_net.egress_destinations:
                dest_cidrs = [d["cidr"] for d in owning_net.egress_destinations]
                annotations = _annotation(defaults.sync_wave("tenantEgressGateway"))
            else:
                # Fallback scaffold if no endpoint networks defined for this tenant
                dest_cidrs = ["0.0.0.0/0"]
                annotations = {
                    **_annotation(defaults.sync_wave("tenantEgressGateway")),
                    domain_label("scaffold", "scaffold"): "true",
                }
            egw = {
                "apiVersion": "isovalent.com/v1",
                "kind": "IsovalentEgressGatewayPolicy",
                "metadata": {
                    "name": f"egw-{slug}",
                    "annotations": annotations,
                },
                "spec": {
                    "selectors": [{"podSelector": {"matchLabels": {}}}],
                    "destinationCIDRs": dest_cidrs,
                    "egressCIDRs": [pool.cidr],
                    "egressGroups": [{
                        "nodeSelector": {"matchLabels": {"egress-gateway": "true"}},
                    }],
                },
            }
            fname = f"egress-gateway-{slug}.yaml"
            files[f"{cilium_root}/{fname}"] = HEADER + _k8s_yaml(egw)
            cilium_resources.append(fname)

        # Kustomizations
        files[f"{cilium_root}/kustomization.yaml"] = _kustomization(sorted(cilium_resources))
        files[f"{base}/networking/kustomization.yaml"] = _kustomization(["cilium"])
        files[f"{base}/kustomization.yaml"] = _kustomization(["networking"])

    return files
