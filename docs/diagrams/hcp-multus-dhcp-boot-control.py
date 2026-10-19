#!/usr/bin/env python3
"""Generate the validated Multus/DHCP HCP worker boot sequence."""

from diagram_theme import write_diagram

NODES = [
    {"id": "objects", "x": 50, "y": 185, "width": 260, "height": 150, "kind": "target", "title": "HCP + KubeVirt", "lines": ["NodePool + Machine", "VM, disk + user data"]},
    {"id": "multus", "x": 420, "y": 185, "width": 260, "height": 150, "kind": "runtime", "title": "tenant-x-workers NAD", "lines": ["management-rendered Multus NIC", "attachment owns bridge/VLAN"]},
    {"id": "dhcp", "x": 790, "y": 185, "width": 260, "height": 150, "kind": "runtime", "title": "DHCP on same NAD", "lines": ["direct broadcast DORA", "isolated reservation scope"]},
    {"id": "ignition", "x": 50, "y": 470, "width": 260, "height": 150, "kind": "runtime", "title": "Ignition endpoint", "lines": ["worker-originated TLS", "full worker config"]},
    {"id": "api", "x": 420, "y": 470, "width": 260, "height": 150, "kind": "runtime", "title": "Hosted API", "lines": ["bootstrap + CSR", "Node registration"]},
    {"id": "ready", "x": 800, "y": 490, "width": 250, "height": 110, "kind": "target", "title": "Worker Ready", "lines": ["guest Cilium"]},
    {"id": "services", "x": 40, "y": 810, "width": 300, "height": 150, "kind": "external", "title": "Boot dependencies", "lines": ["DNS, NTP + registry", "tenant route through ACI"]},
    {"id": "publication", "x": 400, "y": 800, "width": 300, "height": 170, "kind": "runtime", "title": "Management publication", "lines": ["API + Ignition VIP", "management Service BGP", "separate from worker BGP"]},
    {"id": "konnect", "x": 760, "y": 810, "width": 300, "height": 150, "kind": "runtime", "title": "Konnectivity after boot", "lines": ["worker agent dials out", "reverse logical requests"]},
]

EDGES = [
    {"relation": "api", "points": [(310, 260), (420, 260)], "directed": True},
    {"relation": "protocol", "points": [(680, 260), (790, 260)], "directed": True, "label": "DORA", "label_at": (735, 244)},
    {"relation": "protocol", "points": [(920, 335), (920, 400), (180, 400), (180, 470)], "directed": True, "label": "GET", "label_at": (550, 393)},
    {"relation": "protocol", "points": [(310, 545), (420, 545)], "directed": True, "label": "TLS", "label_at": (365, 529)},
    {"relation": "api", "points": [(680, 545), (800, 545)], "directed": True, "label": "join", "label_at": (740, 529)},
    {"relation": "protocol", "points": [(190, 810), (190, 740), (180, 740), (180, 620)], "directed": True, "label": "required before Ready", "label_at": (315, 733)},
    {"relation": "protocol", "points": [(550, 800), (550, 620)], "directed": True, "label": "published VIP", "label_at": (620, 710)},
    {"relation": "protocol", "points": [(910, 810), (910, 700), (925, 700), (925, 600)], "directed": True, "label": "after join", "label_at": (980, 690)},
]

if __name__ == "__main__":
    write_diagram(__file__, "hcp-multus-dhcp-boot-control.svg",
        title="Multus and DHCP worker boot ordering",
        subtitle="The worker and DHCP endpoint share a pre-rendered NAD; boot still needs DORA, Ignition and API before Konnectivity.",
        description="Validated direct-L2 worker boot ordering from HCP objects through the management-owned Multus NAD, scope-bound DHCP, Ignition, API registration and post-boot Konnectivity.",
        width=1100, height=1020,
        zones=[
            {"id": "boot", "x": 20, "y": 110, "width": 1060, "height": 545, "label": "Worker-originated boot sequence"},
            {"id": "owners", "x": 20, "y": 750, "width": 1060, "height": 230, "label": "Supporting path owners"},
        ], nodes=NODES, edges=EDGES, legend=["api", "protocol"])
