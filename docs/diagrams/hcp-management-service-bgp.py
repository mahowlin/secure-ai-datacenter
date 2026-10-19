#!/usr/bin/env python3
"""Generate management Service BGP for hosted control endpoints."""

from diagram_theme import write_diagram

NODES = [
    {"id": "services", "x": 60, "y": 175, "width": 280, "height": 165, "kind": "runtime", "title": "Hosted control Services", "lines": ["API + Ignition", "Konnectivity + OAuth", "management pod network"]},
    {"id": "router", "x": 420, "y": 165, "width": 300, "height": 185, "kind": "runtime", "title": "Management router Service", "lines": ["LoadBalancer VIP", "SNI routes to control Services", "VIP from maintained input"]},
    {"id": "ipam", "x": 60, "y": 420, "width": 280, "height": 155, "kind": "runtime", "title": "Management LB-IPAM", "lines": ["control VIP pool", "Service status"]},
    {"id": "bgp", "x": 400, "y": 410, "width": 300, "height": 175, "kind": "runtime", "title": "Management Cilium BGP", "lines": ["advertisement policy", "selected control-plane VLAN", "control VIP /32 only"]},
    {"id": "aci", "x": 760, "y": 420, "width": 280, "height": 155, "kind": "external", "title": "ACI tenant / VRF", "lines": ["prefix filter", "control VIP route"]},
    {"id": "worker", "x": 760, "y": 665, "width": 280, "height": 125, "kind": "target", "title": "Hosted worker", "lines": ["API/Ignition client", "not a BGP peer here"]},
]

EDGES = [
    {"relation": "api", "points": [(340, 257), (420, 257)], "directed": False},
    {"relation": "api", "points": [(570, 350), (570, 385), (200, 385), (200, 420)], "directed": True},
    {"relation": "api", "points": [(340, 497), (400, 497)], "directed": True},
    {"relation": "protocol", "points": [(700, 497), (760, 497)], "directed": True},
    {"relation": "protocol", "points": [(900, 665), (900, 575)], "directed": True, "label": "control traffic", "label_at": (970, 625)},
]

if __name__ == "__main__":
    write_diagram(__file__, "hcp-management-service-bgp.svg",
        title="Management Service BGP for hosted control endpoints",
        subtitle="Management Cilium publishes the control VIP; hosted workers consume it but do not advertise it.",
        description="Hosted control Services feed a management router LoadBalancer Service. Management LB-IPAM and Cilium BGP advertise the control VIP to ACI. This BGP domain is separate from direct guest-worker application Service BGP.",
        width=1100, height=850,
        zones=[
            {"id": "control", "x": 20, "y": 110, "width": 1060, "height": 505, "label": "Management-cluster ownership"},
            {"id": "consumer", "x": 720, "y": 625, "width": 360, "height": 185, "label": "Worker-originated consumption"},
        ], nodes=NODES, edges=EDGES, legend=["api", "protocol"])
