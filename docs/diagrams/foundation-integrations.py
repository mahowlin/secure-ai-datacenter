#!/usr/bin/env python3
"""Generate infrastructure-foundation integrations for INTEGRATIONS.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "core", "x": 35, "y": 165, "width": 235, "height": 160, "kind": "external",
     "title": "Example site core", "bullets": ["northbound routing", "tenant external prefixes", "site-owned upstream protocol"]},
    {"id": "aci", "x": 300, "y": 165, "width": 235, "height": 160, "kind": "external",
     "title": "ACI fabric", "bullets": ["tenant, VRF and L3Out", "floating SVI and BGP", "physical domain and AAEP"]},
    {"id": "ucs", "x": 565, "y": 165, "width": 235, "height": 160, "kind": "target",
     "title": "Optional UCS profiles", "bullets": ["VLAN and ENG policy", "LAN connectivity", "management + tenant vNICs"]},
    {"id": "host", "x": 830, "y": 165, "width": 235, "height": 160, "kind": "runtime",
     "title": "OpenShift host", "bullets": ["bond0 management", "bond1 tenant transport", "NMState interfaces"]},
    {"id": "control", "x": 180, "y": 485, "width": 340, "height": 165, "kind": "runtime",
     "title": "Hosted control-plane pods", "bullets": ["management pod network", "API and controller services", "no tenant worker NIC"]},
    {"id": "workers", "x": 580, "y": 485, "width": 340, "height": 165, "kind": "runtime",
     "title": "KubeVirt worker VMs", "bullets": ["root volume and VM lifecycle", "Multus worker NIC", "become hosted-cluster nodes"]},
    {"id": "service-vm", "x": 300, "y": 760, "width": 500, "height": 170, "kind": "external",
     "title": "Shared service host", "bullets": ["configured registry", "HTTP file server", "object-storage container", "shared DNS and NTP dependencies"]},
]

EDGES = [
    {"relation": "protocol", "points": [(270, 245), (300, 245)]},
    {"relation": "protocol", "points": [(535, 245), (565, 245)]},
    {"relation": "protocol", "points": [(800, 245), (830, 245)]},
    {"relation": "api", "points": [(900, 325), (900, 390), (350, 390), (350, 485)], "directed": True, "label": "hosts pods", "label_at": (620, 385)},
    {"relation": "api", "points": [(950, 325), (950, 430), (750, 430), (750, 485)], "directed": True, "label": "hosts VMs", "label_at": (850, 425)},
    {"relation": "supply", "points": [(550, 760), (550, 705), (350, 705), (350, 650)], "directed": True, "label": "platform dependencies", "label_at": (450, 700)},
    {"relation": "supply", "points": [(550, 760), (550, 705), (750, 705), (750, 650)], "directed": True},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "foundation-integrations.svg",
        title="UCS integration variant: infrastructure boundaries",
        subtitle="Routing, VLAN, vNIC and host contracts reach two distinct hosted-cluster runtime substrates.",
        description="The example site integrates with ACI, UCS and OpenShift host networking. Management-hosted control-plane pods and KubeVirt worker VMs are separate consumers. The registry, the file server and object storage share one service host.",
        width=1100, height=990,
        zones=[
            {"id": "physical", "x": 20, "y": 105, "width": 1060, "height": 250, "label": "Physical network and compute chain"},
            {"id": "runtime", "x": 145, "y": 435, "width": 810, "height": 245, "label": "Hosted-cluster runtime"},
            {"id": "services", "x": 265, "y": 710, "width": 570, "height": 250, "label": "Shared services"},
        ], nodes=NODES, edges=EDGES, legend=["protocol", "api", "supply"],
    )
