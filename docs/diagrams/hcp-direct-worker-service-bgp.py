#!/usr/bin/env python3
"""Generate direct guest-worker Service BGP control and application paths."""

from diagram_theme import write_diagram

NODES = [
    {"id": "svc", "x": 45, "y": 175, "width": 235, "height": 145, "kind": "target", "title": "Guest Service", "lines": ["type: LoadBalancer", "selects workload pods"]},
    {"id": "pool", "x": 330, "y": 175, "width": 235, "height": 145, "kind": "runtime", "title": "Guest LB-IPAM", "lines": ["tenant Service pool", "allocates the VIP /32"]},
    {"id": "policy", "x": 615, "y": 175, "width": 260, "height": 145, "kind": "runtime", "title": "Cilium BGP policy", "lines": ["selects the Service", "selects peers + interfaces"]},
    {"id": "route", "x": 925, "y": 175, "width": 270, "height": 145, "kind": "external", "title": "ACI route import", "lines": ["accept approved Service /32", "install route in tenant VRF"]},
    {"id": "client", "x": 45, "y": 505, "width": 235, "height": 145, "kind": "external", "title": "Tenant client", "lines": ["destination: Service VIP", "receives backend reply"]},
    {"id": "aci", "x": 330, "y": 505, "width": 235, "height": 145, "kind": "external", "title": "ACI tenant / VRF", "lines": ["route to advertised VIP", "tenant return routing"]},
    {"id": "worker", "x": 615, "y": 490, "width": 260, "height": 175, "kind": "runtime", "title": "Advertising guest worker", "lines": ["Cilium owns Service VIP", "selects workload backend", "returns traffic to ACI"]},
    {"id": "backend", "x": 925, "y": 505, "width": 270, "height": 145, "kind": "target", "title": "Workload backend", "lines": ["selected guest pod", "reply through its worker"]},
]

EDGES = [
    {"relation": "api", "points": [(280, 247), (330, 247)], "directed": True},
    {"relation": "api", "points": [(565, 247), (615, 247)], "directed": True},
    {"relation": "protocol", "points": [(875, 247), (925, 247)], "directed": True},
    {"relation": "api", "points": [(162, 320), (162, 405), (745, 405), (745, 490)], "directed": True, "label": "Service + backend state", "label_at": (455, 397)},
    {"relation": "protocol", "points": [(1060, 320), (1060, 420), (835, 420), (835, 490)], "directed": True, "label": "route selects worker", "label_at": (947, 412)},
    {"relation": "protocol", "points": [(280, 545), (330, 545)], "directed": True},
    {"relation": "protocol", "points": [(565, 545), (615, 545)], "directed": True},
    {"relation": "protocol", "points": [(875, 545), (925, 545)], "directed": True},
    {"relation": "protocol", "points": [(925, 615), (875, 615)], "directed": True},
    {"relation": "protocol", "points": [(615, 615), (565, 615)], "directed": True},
    {"relation": "protocol", "points": [(330, 615), (280, 615)], "directed": True},
]

if __name__ == "__main__":
    write_diagram(
        __file__,
        "hcp-direct-worker-service-bgp.svg",
        title="Direct application Service BGP",
        subtitle="The guest advertises the VIP; the application request and reply stay in the guest-worker and tenant-fabric path.",
        description="Guest Service and Cilium BGP objects advertise a Service VIP. A tenant client sends a request through ACI to an advertising guest worker and workload backend, whose reply returns through the worker and ACI.",
        width=1240,
        height=740,
        zones=[
            {"id": "control", "x": 20, "y": 110, "width": 1200, "height": 255, "label": "Service configuration and route advertisement"},
            {"id": "application", "x": 20, "y": 440, "width": 1200, "height": 245, "label": "Request left to right; reply right to left"},
        ],
        nodes=NODES,
        edges=EDGES,
        legend=["api", "protocol"],
    )
