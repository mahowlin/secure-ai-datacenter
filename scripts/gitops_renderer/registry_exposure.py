"""Optional external registry contract, derived only from selected inventory."""
import ipaddress
import re

from resolver import slugify
from .selected_inputs import api_label


def registry_exposure(cluster):
    value = (cluster.local_registry or {}).get("externalPull")
    if value is None:
        return None
    if not isinstance(value, dict) or type(value.get("enabled")) is not bool:
        raise ValueError("local registry externalPull.enabled must be an explicit boolean")
    if not value["enabled"]:
        return None
    required = ("name", "networkRef", "poolRef", "vip", "allocationEvidence", "hostnamePrefix",
                "servicePort", "listenerPort", "externalTrafficPolicy", "tlsSecret", "runtimeOwner",
                "pullClientSecret", "consumer")
    unknown = set(value) - set(required) - {"enabled"}
    if unknown:
        raise ValueError(f"externalPull contains undeclared fields: {sorted(unknown)}")
    for key in required:
        if not value.get(key):
            raise ValueError(f"enabled local registry externalPull.{key} is required")
    for key in ("networkRef", "poolRef", "vip", "allocationEvidence", "runtimeOwner"):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f"externalPull.{key} must be a nonempty input string")
    listeners = cluster.local_registry.get("listeners")
    if (not isinstance(listeners, dict) or set(listeners) != {"distribution", "pull", "sync"}
            or any(type(p) is not int or not 1 <= p <= 65535 for p in listeners.values())):
        raise ValueError("local registry listeners must declare distribution/pull/sync TCP ports")
    for key in ("servicePort", "listenerPort"):
        if type(value[key]) is not int or not 1 <= value[key] <= 65535:
            raise ValueError(f"externalPull.{key} must be a TCP port")
    if value["listenerPort"] in cluster.local_registry["listeners"].values():
        raise ValueError("externalPull.listenerPort must preserve the existing internal listeners")
    if value["externalTrafficPolicy"] not in ("Cluster", "Local"):
        raise ValueError("externalPull.externalTrafficPolicy must be Cluster or Local")
    for key in ("name", "tlsSecret", "hostnamePrefix"):
        if not isinstance(value[key], str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", value[key]):
            raise ValueError(f"externalPull.{key} must be a DNS label")
    credential = value["pullClientSecret"]
    if not isinstance(credential, dict) or set(credential) != {"namespace", "name"} or not all(credential.get(k) for k in ("namespace", "name")):
        raise ValueError("externalPull.pullClientSecret requires namespace/name references")
    if value["tlsSecret"] in (cluster.local_registry["runtimeSecrets"]["pullTls"],
                               cluster.local_registry["runtimeSecrets"]["syncTls"]):
        raise ValueError("externalPull.tlsSecret must not replace internal ServiceCA secrets")
    networks = {n.name: n for n in cluster.kube_api_networks}
    network = networks.get(value["networkRef"])
    if network is None or not network.cilium_asn:
        raise ValueError("externalPull.networkRef must select an existing management BGP network")
    pools = {p.name: p for p in network.hcp_management_services}
    pool = pools.get(value["poolRef"])
    if pool is None:
        raise ValueError("externalPull.poolRef must select a management services pool in networkRef")
    try:
        vip = ipaddress.IPv4Address(value["vip"])
        subnet = ipaddress.IPv4Network(pool.cidr)
    except ValueError as exc:
        raise ValueError("externalPull requires an IPv4 VIP and services subnet") from exc
    if vip not in subnet or vip in (subnet.network_address, subnet.broadcast_address):
        raise ValueError("externalPull.vip must be a usable address in the declared services pool")
    occupied = [v.cidr.split('/')[0] for n in networks.values()
                for v in n.control_plane_ingress_load_balancer_vips]
    occupied += [v.cidr.split('/')[0] for n in networks.values() for v in n.api_vips]
    if str(vip) in occupied:
        raise ValueError("externalPull.vip conflicts with an inventoried control-plane VIP")
    consumer = value["consumer"]
    if not isinstance(consumer, dict) or not all(consumer.get(k) for k in
            ("workerAttachmentKinds", "deploymentSubjectRoles", "pullSecret", "trustBundle", "exporterImagePull")):
        raise ValueError("externalPull.consumer requires attachment kinds, pullSecret and trustBundle")
    if set(consumer) != {"workerAttachmentKinds", "deploymentSubjectRoles", "pullSecret", "trustBundle", "exporterImagePull"}:
        raise ValueError("externalPull.consumer contains undeclared fields")
    if consumer["exporterImagePull"] != "hosted-mirror":
        raise ValueError("externalPull.consumer.exporterImagePull must select hosted-mirror")
    for ref in (credential["namespace"], credential["name"], consumer["pullSecret"], consumer["trustBundle"]):
        if not isinstance(ref, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", ref):
            raise ValueError("externalPull runtime references must be Kubernetes names")
    kinds = consumer["workerAttachmentKinds"]
    if not isinstance(kinds, list) or not all(isinstance(k, str) and k for k in kinds):
        raise ValueError("externalPull.consumer.workerAttachmentKinds must be a nonempty list")
    roles = consumer["deploymentSubjectRoles"]
    declared = cluster.hosted_cluster_lifecycle.get("deploymentSubjects") or {}
    if (not isinstance(roles, list) or not roles
            or any(not isinstance(role, str) or not isinstance(declared.get(role), str) for role in roles)):
        raise ValueError("externalPull.consumer.deploymentSubjectRoles must select active inventory roles")
    names = {declared[role] for role in roles}
    subjects = [h for h in cluster.hosted_clusters if h.name in names and h.worker_attachment_kind in kinds
                and h.control_plane_ingress_network
                and h.control_plane_ingress_network.name == network.name]
    if {h.name for h in subjects} != names:
        raise ValueError("externalPull.consumer must select hosted worker inventory")
    records = []
    for hcp in subjects:
        ingress = hcp.application_ingress
        if not ingress or not ingress.dns_authority or ingress.dns_zone != cluster.base_domain:
            raise ValueError("externalPull consumers require matching declared applicationIngress DNS authority/zone")
        records.append({"name": f"{value['hostnamePrefix']}.{hcp.name}.{cluster.base_domain}",
                        "type": "A", "address": str(vip), "authority": ingress.dns_authority,
                        "hostedCluster": hcp.name})
    authorities = {r["authority"] for r in records}
    if len(authorities) != 1:
        raise ValueError("one external registry frontend requires one declared DNS authority")
    primary = f"{value['hostnamePrefix']}.{cluster.name}.{cluster.base_domain}"
    records.insert(0, {"name": primary, "type": "A", "address": str(vip),
                       "authority": records[0]["authority"]})
    return {**value, "hostname": primary, "endpoint": f"{primary}:{value['servicePort']}",
            "records": records, "sans": [r["name"] for r in records],
            "subjects": [h.name for h in subjects],
            "serviceLabels": {api_label("registry-exposure"): value["name"]},
            "advertisementLabels": {"advertise": f"control-plane-ingress-{slugify(network.name)}"}}


def selected_registry_endpoint(cluster, hcp):
    if (cluster.local_registry or {}).get("cacheProduction", {}).get("mode") == "preceding-registry":
        return None
    exposure = registry_exposure(cluster)
    if exposure and hcp.name in exposure["subjects"]:
        return exposure["endpoint"]
    return None
