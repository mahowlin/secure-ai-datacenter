#!/usr/bin/env python3
"""Generate the declared Multus, DHCP and direct-worker-BGP composition."""

from diagram_theme import write_diagram


NODES = [
    {"id": "aci", "x": 250, "y": 145, "width": 600, "height": 120, "kind": "external",
     "title": "ACI tenant / VRF", "bullets": ["tenant VLAN, gateway and route-policy boundary", "leaf SVI peers with guest Cilium workers"]},
    {"id": "dhcp", "x": 60, "y": 335, "width": 430, "height": 145, "kind": "runtime",
     "title": "KubeVirt DHCP service", "bullets": ["management-cluster VM", "DORA supplies address, route, DNS and MTU"]},
    {"id": "bgp", "x": 610, "y": 335, "width": 430, "height": 145, "kind": "runtime",
     "title": "Direct worker BGP", "bullets": ["guest Cilium advertises Service /32s", "ACI imports only approved tenant prefixes"]},
    {"id": "control", "x": 60, "y": 590, "width": 430, "height": 170, "kind": "runtime",
     "title": "Hosted control-plane pods", "bullets": ["management pod network", "API, Ignition, Konnectivity and OAuth", "never peer with ACI"]},
    {"id": "workers", "x": 610, "y": 590, "width": 430, "height": 170, "kind": "runtime",
     "title": "KubeVirt worker VMs", "bullets": ["Multus L2 bridge tenant NIC", "DHCP guest configuration", "guest Cilium peers with ACI"], "badge": "declared model"},
    {"id": "ocp", "x": 250, "y": 855, "width": 600, "height": 130, "kind": "target",
     "title": "Management OpenShift", "bullets": ["runs HyperShift and KubeVirt", "hosts control pods, VM lifecycle and tenant bridge"]},
    {"id": "metal", "x": 250, "y": 1065, "width": 600, "height": 115, "kind": "external",
     "title": "Bare metal", "bullets": ["management-cluster nodes", "tenant VLAN transport reaches the host bridge"]},
]

EDGES = [
    {"relation": "protocol", "points": [(400, 265), (400, 300), (275, 300), (275, 335)], "label": "tenant L2", "label_at": (335, 294)},
    {"relation": "protocol", "points": [(700, 265), (700, 300), (825, 300), (825, 335)], "label": "eBGP", "label_at": (762, 294)},
    {"relation": "protocol", "points": [(275, 480), (275, 530), (825, 530), (825, 590)], "directed": True, "label": "DHCP DORA", "label_at": (550, 522)},
    {"relation": "protocol", "points": [(825, 480), (825, 590)], "directed": True, "label": "workers only", "label_at": (895, 545)},
    {"relation": "api", "points": [(400, 855), (400, 810), (275, 810), (275, 760)], "directed": True, "label": "hosts pods", "label_at": (337, 805)},
    {"relation": "api", "points": [(700, 855), (700, 810), (825, 810), (825, 760)], "directed": True, "label": "hosts VMs", "label_at": (762, 805)},
    {"relation": "api", "points": [(490, 675), (610, 675)], "directed": False, "label": "API + Konnectivity", "label_at": (550, 659)},
    {"relation": "delivery", "points": [(550, 1065), (550, 985)], "directed": True, "label": "runs on", "label_at": (615, 1035)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "nested-cluster-l2bridge.svg",
        title="HCP with Multus, DHCP and direct worker BGP",
        subtitle="Validated composition: hosted control-plane pods stay on management networking; only guest workers join tenant BGP.",
        description="Management OpenShift hosts separate HyperShift control-plane pods and KubeVirt workers. An in-cluster KubeVirt DHCP service supplies guest networking over the Multus tenant VLAN, and guest Cilium workers peer directly with ACI for application Service VIPs.",
        width=1100, height=1240,
        zones=[
            {"id": "fabric", "x": 210, "y": 105, "width": 680, "height": 200, "label": "Tenant fabric"},
            {"id": "services", "x": 20, "y": 295, "width": 1060, "height": 215, "label": "Distinct address and Service owners"},
            {"id": "tenant", "x": 20, "y": 540, "width": 1060, "height": 250, "label": "Sibling HCP and KubeVirt services"},
            {"id": "substrate", "x": 210, "y": 805, "width": 680, "height": 405, "label": "OpenShift substrate"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "api", "protocol"],
    )
