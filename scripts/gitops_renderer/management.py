from .selected_inputs import api_label, bind_defaults

import json

from .common import *


def _guest_default_network_refs(hcp: ResolvedHCPCluster) -> set[str]:
    return {
        attachment.network_ref
        for attachment in hcp.worker_network_attachments
        if attachment.guest_default_route
    }


def _subscription_manifest(defaults: ResolvedDefaults, subscription: dict) -> dict:
    spec = {
        "channel": subscription["channel"],
        "name": subscription["name"],
        "source": subscription["source"],
        "sourceNamespace": subscription["sourceNamespace"],
        "installPlanApproval": subscription["installPlanApproval"],
    }
    if subscription.get("startingCSV"):
        spec["startingCSV"] = subscription["startingCSV"]
    annotations = _annotation(defaults.sync_wave("subscription"))
    options = subscription.get("resourceSyncOptions")
    if options:
        annotations["argocd.argoproj.io/sync-options"] = options
    return {
        "apiVersion": "operators.coreos.com/v1alpha1",
        "kind": "Subscription",
        "metadata": {
            "name": subscription["name"],
            "namespace": subscription["namespace"],
            "annotations": annotations,
        },
        "spec": spec,
    }


def _adopted_operator_overlays(defaults: ResolvedDefaults) -> list[dict]:
    """Subscriptions that join the existing management operators Application."""
    adopted = []
    for name in sorted(defaults.gitops_operator_subscriptions):
        subscription = defaults.operator_subscription(name)
        if subscription.get("placement") != "management-operators":
            continue
        if subscription.get("manageInstallNamespace") is not False:
            raise ValueError(f"{name} joins an existing operator namespace and must not render one")
        overlay = subscription.get("overlay")
        options = subscription.get("resourceSyncOptions")
        if not isinstance(overlay, str) or not overlay:
            raise ValueError(f"{name} requires an overlay directory")
        if not isinstance(options, str) or not options:
            raise ValueError(f"{name} requires resourceSyncOptions")
        forbidden = {item.strip() for item in options.split(",")} & {"Replace", "Replace=true", "Force", "Force=true"}
        if forbidden:
            raise ValueError(f"{name} adoption must not replace or force the operator")
        adopted.append(subscription)
    return adopted


def _render_prerequisites_operators(cluster: ResolvedOCPCluster,
                                    defaults: ResolvedDefaults) -> dict[str, str]:
    """Operator apps with Namespace + OperatorGroup + Subscription."""
    files: dict[str, str] = {}
    base = f"apps/hcp/management/operators/overlays/{cluster.name}"
    kubevirt = defaults.operator_subscription("kubevirt-hyperconverged")
    mce = defaults.operator_subscription("multicluster-engine")
    nmstate = defaults.operator_subscription("kubernetes-nmstate-operator")
    capacity = cluster.hosted_cluster_lifecycle.get("managementPodCapacity")
    if capacity:
        pool = capacity["machineConfigPool"]
        density_dir = f"{base}/pod-density"
        files[f"{density_dir}/kubeletconfig.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "machineconfiguration.openshift.io/v1",
            "kind": "KubeletConfig",
            "metadata": {
                "name": f"{cluster.name}-pod-density",
                "annotations": {"machineconfiguration.openshift.io/mc-name-suffix": ""},
            },
            "spec": {
                "machineConfigPoolSelector": {
                    "matchLabels": {
                        f"pools.operator.machineconfiguration.openshift.io/{pool}": "",
                    },
                },
                "kubeletConfig": {
                    "maxPods": capacity["maxPods"],
                    "podsPerCore": capacity["podsPerCore"],
                },
            },
        })
        files[f"{density_dir}/kustomization.yaml"] = _kustomization(["kubeletconfig.yaml"])

        policy = capacity["restartPolicy"]
        policy_dir = f"{base}/pod-density-policy"
        files[f"{policy_dir}/machineconfiguration.yaml"] = HEADER + _k8s_yaml({
            "apiVersion": "operator.openshift.io/v1",
            "kind": "MachineConfiguration",
            "metadata": {
                "name": "cluster",
                "annotations": {
                    "argocd.argoproj.io/sync-options": "ServerSideApply=true,Prune=false,Delete=false",
                },
            },
            "spec": {"nodeDisruptionPolicy": {"files": [{
                "path": policy["path"],
                "actions": [{"type": "Restart", "restart": {
                    "serviceName": policy["serviceName"],
                }}],
            }]}},
        })
        files[f"{policy_dir}/kustomization.yaml"] = _kustomization(["machineconfiguration.yaml"])

    # --- OpenShift Virtualization ---
    cnv_dir = f"{base}/openshift-virtualization"
    files[f"{cnv_dir}/namespace.yaml"] = HEADER + _k8s_yaml(
        _namespace(defaults, kubevirt["namespace"], {"app.kubernetes.io/name": "openshift-virtualization"}))
    files[f"{cnv_dir}/operatorgroup.yaml"] = HEADER + _k8s_yaml(
        _operatorgroup(defaults, kubevirt["name"], kubevirt["namespace"], [kubevirt["namespace"]]))
    files[f"{cnv_dir}/subscription.yaml"] = HEADER + _k8s_yaml(_subscription_manifest(defaults, kubevirt))
    files[f"{cnv_dir}/kustomization.yaml"] = _kustomization([
        "namespace.yaml", "operatorgroup.yaml", "subscription.yaml",
    ])

    # --- MCE / HyperShift ---
    mce_dir = f"{base}/mce-hypershift"
    files[f"{mce_dir}/namespace.yaml"] = HEADER + _k8s_yaml(
        _namespace(defaults, mce["namespace"], {"app.kubernetes.io/name": "multicluster-engine"}))
    files[f"{mce_dir}/operatorgroup.yaml"] = HEADER + _k8s_yaml(
        _operatorgroup(defaults, mce["name"], mce["namespace"], [mce["namespace"]]))
    files[f"{mce_dir}/subscription.yaml"] = HEADER + _k8s_yaml(_subscription_manifest(defaults, mce))
    files[f"{mce_dir}/kustomization.yaml"] = _kustomization([
        "namespace.yaml", "operatorgroup.yaml", "subscription.yaml",
    ])

    # LVMS is provided by the core GitOps baseline. HCP-specific operator
    # prerequisites start at CNV/MCE/NMState to avoid duplicate ownership of
    # openshift-storage resources.

    # --- kubernetes-nmstate ---
    nmstate_dir = f"{base}/kubernetes-nmstate"
    files[f"{nmstate_dir}/namespace.yaml"] = HEADER + _k8s_yaml(
        _namespace(defaults, nmstate["namespace"], {"app.kubernetes.io/name": "kubernetes-nmstate"}))
    files[f"{nmstate_dir}/operatorgroup.yaml"] = HEADER + _k8s_yaml(
        _operatorgroup(defaults, "kubernetes-nmstate", nmstate["namespace"], [nmstate["namespace"]]))
    files[f"{nmstate_dir}/subscription.yaml"] = HEADER + _k8s_yaml(_subscription_manifest(defaults, nmstate))
    files[f"{nmstate_dir}/kustomization.yaml"] = _kustomization([
        "namespace.yaml", "operatorgroup.yaml", "subscription.yaml",
    ])

    adopted = _adopted_operator_overlays(defaults)
    for subscription in adopted:
        overlay_dir = f"{base}/{subscription['overlay']}"
        files[f"{overlay_dir}/subscription.yaml"] = HEADER + _k8s_yaml(
            _subscription_manifest(defaults, subscription))
        files[f"{overlay_dir}/kustomization.yaml"] = _kustomization(["subscription.yaml"])

    # Root operators kustomization
    files[f"{base}/kustomization.yaml"] = _kustomization([
        "openshift-virtualization",
        "mce-hypershift",
        "kubernetes-nmstate",
        *[subscription["overlay"] for subscription in adopted],
    ])

    return files


# ---------------------------------------------------------------------------
# Management app: prerequisites — parent network
# ---------------------------------------------------------------------------

def _render_prerequisites_nmstate(cluster: ResolvedOCPCluster,
                                  bond_interface: str,
                                  defaults: ResolvedDefaults) -> dict[str, str]:
    """NMState NNCP for tenant transport bond creation, kubeAPI VLANs, and tenant bridges.

    tenant bond:   Dedicated tenant transport bond from UCS vNIC pair (Day-2/NMState).
    kubeAPI VLANs: IP-enabled VLAN sub-interface on tenant bond (BGP source).
                   No bridge — Cilium peers directly from host L3 interface.
    Tenant VLANs:  IP-disabled VLAN sub-interface + Linux bridge for KubeVirt NAD.
    """
    files: dict[str, str] = {}
    base = f"apps/hcp/management/parent-network/overlays/{cluster.name}/nmstate"
    network_mtus = [n.mtu for n in [*cluster.kube_api_networks, *cluster.tenant_networks] if n.mtu is not None]
    # --- tenant transport bond creation NNCP: bond the UCS tenant vNIC pair ---
    if cluster.parent_networking and cluster.parent_networking.tenant_members:
        members = cluster.parent_networking.tenant_members
        bond_nncp = {
            "apiVersion": "nmstate.io/v1",
            "kind": "NodeNetworkConfigurationPolicy",
                "metadata": {
                    "name": f"{bond_interface}-tenant-transport",
                    "annotations": _annotation(defaults.sync_wave("nmstate")),
                },
            "spec": {
                "nodeSelector": {"kubernetes.io/os": "linux"},
                "desiredState": {
                    "interfaces": [
                        {
                            "name": bond_interface,
                            **({"mtu": max(network_mtus)} if network_mtus else {}),
                            "type": "bond",
                            "state": "up",
                            "ipv4": {"enabled": False},
                            "ipv6": {"enabled": False},
                            "link-aggregation": {
                                "mode": "active-backup",
                                "options": {"miimon": "100", "primary": members[0]},
                                "port": list(members),
                            },
                        },
                    ]
                },
            },
        }
        files[f"{base}/{bond_interface}-bond.yaml"] = HEADER + _k8s_yaml(bond_nncp)

    # Build lookup of peering IPs per kubeAPI network
    peering_ip_map: dict[str, dict[str, str]] = {}  # {networkRef: {node: ip}}
    if cluster.parent_networking:
        for pn in cluster.parent_networking.peering_networks:
            if pn.purpose == "hostedClusterApi" and pn.parent_node_peering_ips:
                peering_ip_map[pn.network_ref] = pn.parent_node_peering_ips

    # kubeAPI networks — IP-enabled sub-interface, per-node IP via NMState variable substitution
    for net in cluster.kube_api_networks:
        slug = slugify(net.name)
        node_ips = peering_ip_map.get(net.name, {})
        # Per-node NNCPs with static IPs for BGP source interface
        for node in cluster.nodes:
            node_ip = node_ips.get(node.name)
            if not node_ip:
                continue
            # Determine prefix length from peering subnet
            prefix_len = 29  # default /29
            if net.peering_subnet:
                prefix_len = int(net.peering_subnet.split("/")[1])
            nncp = {
                "apiVersion": "nmstate.io/v1",
                "kind": "NodeNetworkConfigurationPolicy",
                "metadata": {
                    "name": f"vlan-{net.vlan}-{slug}-{node.name}",
                    "annotations": _annotation(defaults.sync_wave("nmstate")),
                },
                "spec": {
                    "nodeSelector": {"kubernetes.io/hostname": node.hostname},
                    "desiredState": {
                        "interfaces": [
                            {
                                "name": f"{bond_interface}.{net.vlan}",
                                **({"mtu": net.mtu} if net.mtu is not None else {}),
                                "type": "vlan",
                                "state": "up",
                                "vlan": {"base-iface": bond_interface, "id": net.vlan},
                                "ipv4": {
                                    "enabled": True,
                                    "address": [{"ip": node_ip, "prefix-length": prefix_len}],
                                    "dhcp": False,
                                },
                                "ipv6": {"enabled": False},
                            },
                        ]
                    }
                }
            }
            files[f"{base}/{slug}-{node.name}.yaml"] = HEADER + _k8s_yaml(nncp)

    # Tenant carriers are factual shared infrastructure. Multus workers may
    # intentionally use the same bridge without sharing attachment ownership.
    for net in cluster.tenant_networks:
        slug = slugify(net.name)
        nncp = {
            "apiVersion": "nmstate.io/v1",
            "kind": "NodeNetworkConfigurationPolicy",
            "metadata": {
                "name": f"vlan-{net.vlan}-{slug}",
                "annotations": _annotation(defaults.sync_wave("nmstate")),
            },
            "spec": {
                "desiredState": {
                    "interfaces": [
                        {
                            "name": f"{bond_interface}.{net.vlan}",
                            **({"mtu": net.mtu} if net.mtu is not None else {}),
                            "type": "vlan",
                            "state": "up",
                            "vlan": {"base-iface": bond_interface, "id": net.vlan},
                            "ipv4": {"enabled": False},
                            "ipv6": {"enabled": False},
                        },
                        {
                            "name": f"br-{net.vlan}",
                            **({"mtu": net.mtu} if net.mtu is not None else {}),
                            "type": "linux-bridge",
                            "state": "up",
                            "bridge": {
                                "port": [{"name": f"{bond_interface}.{net.vlan}"}],
                            },
                            "ipv4": {"enabled": False},
                            "ipv6": {"enabled": False},
                        },
                    ]
                }
            }
        }
        files[f"{base}/{slug}.yaml"] = HEADER + _k8s_yaml(nncp)

    return files


def _render_prerequisites_nads(cluster: ResolvedOCPCluster,
                               defaults: ResolvedDefaults) -> dict[str, str]:
    """NetworkAttachmentDefinitions for tenant worker networks."""
    files: dict[str, str] = {}
    base = f"apps/hcp/management/parent-network/overlays/{cluster.name}/network-attachments"
    seen: set[str] = set()
    server_nads: dict[str, dict[str, dict]] = {}
    for hcp in cluster.hosted_clusters:
        if not hcp.uses_multus_workers:
            continue
        hcp_ns = f"clusters-{hcp.name}"
        guest_default_network_refs = _guest_default_network_refs(hcp)
        for net in hcp.resolved_worker_networks:
            # NAD per (network, namespace) — each HCP namespace gets its own NAD
            key = f"{net.name}:{hcp.name}"
            if key in seen:
                continue
            seen.add(key)
            slug = slugify(net.name)
            cni_config = {
                "cniVersion": "0.3.1",
                "name": slug,
                "type": "cnv-bridge",
                "bridge": f"br-{net.vlan}",
            }
            if not net.worker_peering.dhcp:
                raise ValueError(
                    f"HCP {hcp.name} worker network {net.name} must set subnets.workers.dhcp; "
                    "Whereabouts direct worker IPAM is not migration-safe for KubeVirt workers"
                )
            nad = {
                "apiVersion": "k8s.cni.cncf.io/v1",
                "kind": "NetworkAttachmentDefinition",
                "metadata": {
                    "name": slug,
                    "namespace": f"{hcp_ns}-{hcp.name}",
                    "annotations": {
                        **_annotation(defaults.sync_wave("networkAttachmentDefinition")),
                        api_label("peering-subnet-dhcp"): str(net.worker_peering.dhcp).lower(),
                        api_label("guest-default-route"): str(net.name in guest_default_network_refs).lower(),
                    },
                },
                "spec": {
                    "config": json.dumps(cni_config, separators=(",", ":")),
                },
            }
            fname = f"{slug}-{hcp.name}.yaml" if len(cluster.hosted_clusters) > 1 else f"{slug}.yaml"
            files[f"{base}/{fname}"] = HEADER + _k8s_yaml(nad)

            # The management attachment renderer also owns the identical NAD in
            # the central DHCP namespace.  The DHCP renderer references it and
            # never derives VLAN or bridge facts itself.
            server_nads.setdefault(hcp.name, {}).setdefault(net.name, {
                "apiVersion": "k8s.cni.cncf.io/v1",
                "kind": "NetworkAttachmentDefinition",
                "metadata": {
                    "name": slug,
                    "namespace": defaults.namespace("hcpWorkerDhcp"),
                    "annotations": {
                        **_annotation(defaults.sync_wave("networkAttachmentDefinition")),
                        api_label("peering-subnet-dhcp"): "true",
                    },
                },
                "spec": {"config": json.dumps(cni_config, separators=(",", ":"))},
            })

    if server_nads:
        dhcp_root = f"apps/hcp/management/parent-network/overlays/{cluster.name}/worker-dhcp"
        for hcp_name, hcp_nads in sorted(server_nads.items()):
            files[f"{dhcp_root}/{slugify(hcp_name)}/network-attachments.yaml"] = (
                HEADER + "".join(
                    _k8s_yaml(hcp_nads[name]) for name in sorted(hcp_nads)
                )
            )

    return files


def _render_prerequisites_bgp(cluster: ResolvedOCPCluster,
                              fabric_asn: int,
                              defaults: ResolvedDefaults) -> dict[str, str]:
    """Parent Cilium BGP for tenant management-services peering using IEP APIs.

    Renders:
      - IsovalentBGPClusterConfig (one per cluster, multiple bgpInstances)
      - IsovalentBGPPeerConfig (one per tenant management-services network)
      - IsovalentBGPAdvertisement (one per HCP router LoadBalancerIP)
      - CiliumLoadBalancerIPPool (one per HCP control-plane ingress /32)
    """
    files: dict[str, str] = {}
    base = f"apps/hcp/management/parent-network/overlays/{cluster.name}/cilium-bgp"
    bond_interface = _tenant_bond_interface(cluster, defaults)
    peering_entries = {
        entry.network_ref: entry
        for entry in (cluster.parent_networking.peering_networks if cluster.parent_networking else [])
        if entry.purpose == "hostedClusterApi"
    }

    # --- IsovalentBFDProfile: shared by all peers ---
    bfd_profile = {
        "apiVersion": "isovalent.com/v1alpha1",
        "kind": "IsovalentBFDProfile",
        "metadata": {
            "name": "aci-bfd-profile",
            "annotations": _annotation(defaults.sync_wave("bgp")),
        },
        "spec": {
            "detectMultiplier": 5,
            "receiveIntervalMilliseconds": 999,
            "transmitIntervalMilliseconds": 999,
        },
    }
    files[f"{base}/bfd-profile.yaml"] = HEADER + _k8s_yaml(bfd_profile)

    # --- IsovalentBGPClusterConfig: one cluster-wide resource with per-kubeAPI bgpInstances ---
    bgp_instances = []
    for net in cluster.kube_api_networks:
        slug = slugify(net.name)
        peers = []
        for i, peer in enumerate(net.svi_peers):
            peer_ip = peer.ip.split("/")[0]
            peers.append({
                "name": f"aci-vlan{net.vlan}-peer-{i+1}",
                "peerASN": fabric_asn,
                "peerAddress": peer_ip,
                "peerConfigRef": {"name": f"mgmt-{slug}-peer-config"},
            })
        bgp_instances.append({
            "name": f"mgmt-{slug}",
            "localASN": net.cilium_asn,
            "peers": peers,
        })

    cluster_config = {
        "apiVersion": "isovalent.com/v1",
        "kind": "IsovalentBGPClusterConfig",
        "metadata": {
            "name": "mgmt-kubeapi-bgp",
            "annotations": _annotation(defaults.sync_wave("bgp")),
        },
        "spec": {
            "nodeSelector": {"matchLabels": {"kubernetes.io/os": "linux"}},
            "bgpInstances": bgp_instances,
        },
    }
    files[f"{base}/cluster-config.yaml"] = HEADER + _k8s_yaml(cluster_config)

    # --- IsovalentBGPPolicy + IsovalentBGPPeerConfig: one per kubeAPI network ---
    for net in cluster.kube_api_networks:
        slug = slugify(net.name)
        peering_entry = peering_entries.get(net.name)
        import_policy_name = f"mgmt-{slug}-import-policy"
        if net.import_destinations:
            import_policy = {
                "apiVersion": "isovalent.com/v1",
                "kind": "IsovalentBGPPolicy",
                "metadata": {
                    "name": import_policy_name,
                    "annotations": _annotation(defaults.sync_wave("bgp")),
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
            files[f"{base}/import-policy-{slug}.yaml"] = HEADER + _k8s_yaml(import_policy)

        family = {
            "afi": "ipv4",
            "safi": "unicast",
            "advertisements": {"matchLabels": {"advertise": f"control-plane-ingress-{slug}"}},
        }
        if net.import_destinations:
            family["importPolicyRef"] = {"name": import_policy_name}

        peer_spec = {}
        if not peering_entry or peering_entry.bgp_source_address_mode == "sourceInterface":
            peer_spec["transport"] = {"sourceInterface": f"{bond_interface}.{net.vlan}"}
        peer_spec.update({
            "timers": {
                "connectRetryTimeSeconds": 5,
                "holdTimeSeconds": 9,
                "keepAliveTimeSeconds": 3,
            },
            "bfdProfileRef": "aci-bfd-profile",
            "gracefulRestart": {
                "enabled": True,
                "restartTimeSeconds": 120,
            },
            "families": [family],
        })

        peer_config = {
            "apiVersion": "isovalent.com/v1",
            "kind": "IsovalentBGPPeerConfig",
            "metadata": {
                "name": f"mgmt-{slug}-peer-config",
                "annotations": _annotation(defaults.sync_wave("bgp")),
            },
            "spec": peer_spec,
        }
        files[f"{base}/peer-config-{slug}.yaml"] = HEADER + _k8s_yaml(peer_config)

    # A peer-config sourceInterface is intentionally unsuitable when Cilium also
    # assigns a movable Egress IP to that interface. Pin the BGP source address
    # explicitly on every participating node through the documented per-node API.
    override_instances: dict[str, list[dict]] = {}
    for net in cluster.kube_api_networks:
        peering_entry = peering_entries.get(net.name)
        if not peering_entry or peering_entry.bgp_source_address_mode != "nodeConfigOverride":
            continue
        slug = slugify(net.name)
        for node in cluster.nodes:
            local_address = peering_entry.parent_node_peering_ips.get(node.name)
            if not local_address:
                continue
            override_instances.setdefault(node.hostname, []).append({
                "name": f"mgmt-{slug}",
                "peers": [
                    {
                        "name": f"aci-vlan{net.vlan}-peer-{i+1}",
                        "localAddress": local_address,
                    }
                    for i, _peer in enumerate(net.svi_peers)
                ],
            })
    for hostname, instances in sorted(override_instances.items()):
        node_name = hostname.split(".", 1)[0]
        node_override = {
            "apiVersion": "isovalent.com/v1",
            "kind": "IsovalentBGPNodeConfigOverride",
            "metadata": {
                "name": hostname,
                "annotations": _annotation(defaults.sync_wave("bgp")),
            },
            "spec": {"bgpInstances": instances},
        }
        files[f"{base}/node-config-override-{node_name}.yaml"] = (
            HEADER + _k8s_yaml(node_override)
        )

    # --- IsovalentBGPAdvertisement + CiliumLoadBalancerIPPool: per HCP ---
    # HyperShift owns the router Service, so the hosted-cluster lifecycle overlay
    # labels it after creation. These selectors must match that real Service
    # label. The selected Cilium source also supplies synthetic namespace/name
    # labels, but the explicit real label makes this allocation intent visible.
    for hcp in cluster.hosted_clusters:
        if not hcp.control_plane_ingress_load_balancer_vip or not hcp.control_plane_ingress_network:
            continue
        vip = hcp.control_plane_ingress_load_balancer_vip
        net = hcp.control_plane_ingress_network
        slug = slugify(net.name)
        vip_ip = vip.cidr.split("/")[0]

        svc_selector = {
            "matchLabels": _control_plane_ingress_service_labels(hcp),
        }

        advertisement = {
            "apiVersion": "isovalent.com/v1",
            "kind": "IsovalentBGPAdvertisement",
            "metadata": {
                "name": f"control-plane-ingress-{hcp.name}-advertisement",
                "labels": {"advertise": f"control-plane-ingress-{slug}"},
                "annotations": _annotation(defaults.sync_wave("bgpAdvertisement")),
            },
            "spec": {
                "advertisements": [{
                    "advertisementType": "Service",
                    "service": {"addresses": ["LoadBalancerIP"]},
                    "selector": svc_selector,
                }],
            },
        }
        files[f"{base}/advertisement-{hcp.name}.yaml"] = HEADER + _k8s_yaml(advertisement)

        pool = {
            "apiVersion": "cilium.io/v2",
            "kind": "CiliumLoadBalancerIPPool",
            "metadata": {
                "name": f"control-plane-ingress-{hcp.name}-vip-pool",
                "annotations": _annotation(defaults.sync_wave("bgpAdvertisement")),
            },
            "spec": {
                "blocks": [{"cidr": f"{vip_ip}/32"}],
                "serviceSelector": svc_selector,
            },
        }
        files[f"{base}/lb-pool-{hcp.name}.yaml"] = HEADER + _k8s_yaml(pool)

    return files


def _render_prerequisites_operands(cluster: ResolvedOCPCluster,
                                   defaults: ResolvedDefaults) -> dict[str, str]:
    """Operator operand CRs that must be applied after OLM creates their CRDs."""
    files: dict[str, str] = {}
    base = f"apps/hcp/management/operands/overlays/{cluster.name}"
    cnv_ns = defaults.namespace("cnv")
    hypershift_ns = defaults.namespace("hypershift")
    ose_tools_image = defaults.gitops_image_reference("oseTools")
    files[f"{base}/mce.yaml"] = HEADER + _k8s_yaml({
        "apiVersion": "multicluster.openshift.io/v1",
        "kind": "MultiClusterEngine",
        "metadata": {
            "name": "multiclusterengine",
            "annotations": _annotation(defaults.sync_wave("subscription", 1)),
        },
        "spec": {},
    })
    files[f"{base}/nmstate.yaml"] = HEADER + _k8s_yaml({
        "apiVersion": "nmstate.io/v1",
        "kind": "NMState",
        "metadata": {
            "name": "nmstate",
            "annotations": _annotation(defaults.sync_wave("subscription", 1)),
        },
        "spec": {},
    })
    files[f"{base}/openshift-virtualization.yaml"] = HEADER + _k8s_yaml({
        "apiVersion": "hco.kubevirt.io/v1beta1",
        "kind": "HyperConverged",
        "metadata": {
            "name": "kubevirt-hyperconverged",
            "namespace": cnv_ns,
            "annotations": _annotation(defaults.sync_wave("subscription", 1)),
        },
        "spec": {
            "enableCommonBootImageImport": defaults.gitops_virtualization["enableCommonBootImageImport"],
        },
    })
    files[f"{base}/hypershift-seccomp-fix-rbac.yaml"] = HEADER + _k8s_yaml({
        "apiVersion": "v1",
        "kind": "ServiceAccount",
        "metadata": {
            "name": "hypershift-seccomp-patcher",
            "namespace": hypershift_ns,
            "annotations": _annotation(defaults.sync_wave("subscription", 2)),
        },
    }) + _k8s_yaml({
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {
            "name": "hypershift-seccomp-patcher",
            "namespace": hypershift_ns,
            "annotations": _annotation(defaults.sync_wave("subscription", 2)),
        },
        "rules": [{
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "verbs": ["get", "list", "watch", "patch"],
        }],
    }) + _k8s_yaml({
        "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {
                "name": "hypershift-seccomp-patcher",
                "namespace": hypershift_ns,
                "annotations": _annotation(defaults.sync_wave("subscription", 2)),
            },
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Role",
            "name": "hypershift-seccomp-patcher",
        },
            "subjects": [{
                "kind": "ServiceAccount",
                "name": "hypershift-seccomp-patcher",
                "namespace": hypershift_ns,
            }],
    })
    files[f"{base}/hypershift-seccomp-fix-hook.yaml"] = HEADER + _k8s_yaml({
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": "hypershift-seccomp-fix",
            "namespace": hypershift_ns,
            "annotations": {
                "argocd.argoproj.io/hook": "PostSync",
                "argocd.argoproj.io/hook-delete-policy": "BeforeHookCreation",
            },
        },
        "spec": {
            "backoffLimit": 5,
            "activeDeadlineSeconds": 600,
            "template": {
                "metadata": {"labels": {"app": "hypershift-seccomp-fix"}},
                "spec": {
                    "serviceAccountName": "hypershift-seccomp-patcher",
                    "restartPolicy": "Never",
                    "containers": [{
                        "name": "patch",
                        "image": ose_tools_image,
                        "command": ["/bin/bash", "-c"],
                        "args": ["""set -euo pipefail

ATTEMPTS=0
MAX_ATTEMPTS=60
while [ $ATTEMPTS -lt $MAX_ATTEMPTS ]; do
  if oc get deployment operator -n __HYPERSHIFT_NAMESPACE__ >/dev/null 2>&1; then
    break
  fi
  ATTEMPTS=$((ATTEMPTS + 1))
  sleep 10
done

if [ $ATTEMPTS -eq $MAX_ATTEMPTS ]; then
  echo \"ERROR: Timed out waiting for HyperShift operator deployment\"
  exit 1
fi

CURRENT=$(oc get deployment operator -n __HYPERSHIFT_NAMESPACE__ \\
  -o jsonpath='{.spec.template.spec.securityContext.seccompProfile.type}' 2>/dev/null || true)

if [ \"$CURRENT\" = \"RuntimeDefault\" ]; then
  echo \"Pod-level seccompProfile already set.\"
  exit 0
fi

oc patch deployment operator -n __HYPERSHIFT_NAMESPACE__ \\
  --type=merge \\
  -p='{\"spec\":{\"template\":{\"spec\":{\"securityContext\":{\"seccompProfile\":{\"type\":\"RuntimeDefault\"}}}}}}'
oc rollout status deployment/operator -n __HYPERSHIFT_NAMESPACE__ --timeout=120s
""".replace("__HYPERSHIFT_NAMESPACE__", hypershift_ns)],
                        "resources": {
                            "requests": {"cpu": "10m", "memory": "64Mi"},
                            "limits": {"cpu": "100m", "memory": "128Mi"},
                        },
                    }],
                },
            },
        },
    })
    files[f"{base}/kustomization.yaml"] = _kustomization([
        "mce.yaml",
        "nmstate.yaml",
        "openshift-virtualization.yaml",
        "hypershift-seccomp-fix-rbac.yaml",
        "hypershift-seccomp-fix-hook.yaml",
    ])
    return files


def _render_prerequisites_kustomizations(cluster: ResolvedOCPCluster,
                                         defaults: ResolvedDefaults) -> dict[str, str]:
    """Kustomization files for prerequisites tree."""
    files: dict[str, str] = {}
    base = f"apps/hcp/management/parent-network/overlays/{cluster.name}"
    pn_resources = []
    if cluster.kube_api_networks or cluster.tenant_networks:
        pn_resources.append("nmstate")
    seen_tenant = set()
    for hcp in cluster.hosted_clusters:
        if not hcp.uses_multus_workers:
            continue
        for net in hcp.resolved_worker_networks:
            seen_tenant.add(net.name)
    if seen_tenant:
        pn_resources.append("network-attachments")
    if any(hcp.uses_central_dhcp for hcp in cluster.hosted_clusters):
        pn_resources.append("worker-dhcp")
    if cluster.kube_api_networks:
        pn_resources.append("cilium-bgp")
    files[f"{base}/kustomization.yaml"] = _kustomization(pn_resources)

    # nmstate kustomization — bond first, kubeAPI networks have per-node NNCPs, tenant networks have one NNCP each
    nmstate_resources: list[str] = []
    if cluster.parent_networking and cluster.parent_networking.tenant_members:
        bond_interface = _tenant_bond_interface(cluster, defaults)
        nmstate_resources.append(f"{bond_interface}-bond.yaml")
    # Build peering IP map to know which nodes get NNCPs
    peering_ip_map: dict[str, dict[str, str]] = {}
    if cluster.parent_networking:
        for pn in cluster.parent_networking.peering_networks:
            if pn.purpose == "hostedClusterApi" and pn.parent_node_peering_ips:
                peering_ip_map[pn.network_ref] = pn.parent_node_peering_ips
    for net in cluster.kube_api_networks:
        slug = slugify(net.name)
        node_ips = peering_ip_map.get(net.name, {})
        for node in cluster.nodes:
            if node.name in node_ips:
                nmstate_resources.append(f"{slug}-{node.name}.yaml")
    for net in cluster.tenant_networks:
        nmstate_resources.append(f"{slugify(net.name)}.yaml")
    if nmstate_resources:
        files[f"{base}/nmstate/kustomization.yaml"] = _kustomization(sorted(nmstate_resources))

    # network-attachments kustomization
    nad_fnames: list[str] = []
    seen_nad: set[str] = set()
    for hcp in cluster.hosted_clusters:
        if hcp.uses_multus_workers:
            for net in hcp.resolved_worker_networks:
                key = f"{net.name}:{hcp.name}"
                if key in seen_nad:
                    continue
                seen_nad.add(key)
                slug = slugify(net.name)
                fname = f"{slug}-{hcp.name}.yaml" if len(cluster.hosted_clusters) > 1 else f"{slug}.yaml"
                nad_fnames.append(fname)
    if nad_fnames:
        files[f"{base}/network-attachments/kustomization.yaml"] = _kustomization(sorted(nad_fnames))

    # cilium-bgp kustomization — collect all rendered BGP files
    bgp_resources = ["bfd-profile.yaml", "cluster-config.yaml"]
    for net in cluster.kube_api_networks:
        slug = slugify(net.name)
        if net.import_destinations:
            bgp_resources.append(f"import-policy-{slug}.yaml")
        bgp_resources.append(f"peer-config-{slug}.yaml")
    if cluster.parent_networking:
        override_nodes = {
            node.name
            for entry in cluster.parent_networking.peering_networks
            if entry.network_ref in {net.name for net in cluster.kube_api_networks}
            and entry.purpose == "hostedClusterApi"
            and entry.bgp_source_address_mode == "nodeConfigOverride"
            for node in cluster.nodes
            if node.name in entry.parent_node_peering_ips
        }
        bgp_resources.extend(
            f"node-config-override-{node}.yaml" for node in sorted(override_nodes)
        )
    for hcp in cluster.hosted_clusters:
        if hcp.control_plane_ingress_load_balancer_vip and hcp.control_plane_ingress_network:
            bgp_resources.append(f"advertisement-{hcp.name}.yaml")
            bgp_resources.append(f"lb-pool-{hcp.name}.yaml")
    files[f"{base}/cilium-bgp/kustomization.yaml"] = _kustomization(sorted(bgp_resources))

    return files
