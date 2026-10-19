#!/usr/bin/env python3
"""Generate the external-HCP ILB object and control model."""

from diagram_theme import write_diagram


NODES = [
    {
        "id": "remote-api",
        "x": 45,
        "y": 180,
        "width": 270,
        "height": 150,
        "kind": "target",
        "title": "Hosted kube-apiserver Service",
        "lines": ["backed by HCP API pods", "opted-in Services + Nodes", "Service status"],
    },
    {
        "id": "remote-rbac",
        "x": 45,
        "y": 410,
        "width": 270,
        "height": 125,
        "kind": "target",
        "title": "Dedicated RBAC",
        "lines": ["ServiceAccount", "read + status permissions"],
    },
    {
        "id": "registration",
        "x": 370,
        "y": 200,
        "width": 320,
        "height": 150,
        "kind": "target",
        "title": "Cluster registration",
        "lines": ["LBK8sBackendCluster", "kubeconfig Secret", "one per hosted cluster"],
    },
    {
        "id": "controller",
        "x": 740,
        "y": 200,
        "width": 320,
        "height": 150,
        "kind": "runtime",
        "title": "External-cluster controller",
        "lines": ["uses registration", "watches remote objects", "updates Service status"],
    },
    {
        "id": "generated",
        "x": 740,
        "y": 430,
        "width": 320,
        "height": 180,
        "kind": "planned",
        "title": "Generated ILB state",
        "lines": ["per-cluster namespace", "LBVIP + LBService", "LBBackendPool"],
        "badge": "must validate",
    },
    {
        "id": "vip-pool",
        "x": 370,
        "y": 450,
        "width": 280,
        "height": 125,
        "kind": "runtime",
        "title": "ILB VIP pool",
        "lines": ["allocates service VIP", "tenant-scoped range"],
    },
    {
        "id": "bgp-policy",
        "x": 740,
        "y": 680,
        "width": 320,
        "height": 125,
        "kind": "planned",
        "title": "ILB BGP policy",
        "lines": ["selects ILB VIP", "publishes service /32"],
        "badge": "must validate",
    },
]


EDGES = [
    {"relation": "api", "points": [(180, 410), (180, 330)], "directed": True},
    {"relation": "api", "points": [(690, 275), (740, 275)], "directed": True},
    {"relation": "api", "points": [(900, 350), (900, 430)], "directed": True},
    {
        "relation": "api",
        "points": [(740, 320), (710, 320), (710, 375), (180, 375), (180, 330)],
        "directed": True,
        "label": "watch objects + patch status",
        "label_at": (445, 368),
    },
    {
        "relation": "api",
        "points": [(650, 512), (695, 512), (695, 535), (740, 535)],
        "directed": True,
        "label": "allocate VIP",
        "label_at": (695, 505),
    },
    {"relation": "api", "points": [(900, 680), (900, 610)], "directed": True},
]


if __name__ == "__main__":
    write_diagram(
        __file__,
        "hcp-ilb-object-control.svg",
        title="Management ILB discovery and generated state",
        subtitle="The controller reaches hosted kube-apiserver; object reconciliation precedes backend traffic proof.",
        description="The management ILB controller reaches the hosted kube-apiserver Service backed by HCP API pods. Dedicated RBAC exposes opted-in Services and Nodes. A per-cluster registration and kubeconfig let the controller create ILB state, allocate a VIP and select it for BGP publication. The generated state and route remain subject to validation.",
        width=1100,
        height=900,
        zones=[
            {
                "id": "remote",
                "x": 20,
                "y": 110,
                "width": 310,
                "height": 710,
                "label": "Hosted tenant API",
                "note": "Discovery endpoint; not an ILB proxy node",
            },
            {
                "id": "management",
                "x": 345,
                "y": 110,
                "width": 735,
                "height": 710,
                "label": "Management cluster / ILB",
                "note": "Registration, controller and generated service-edge objects",
            },
        ],
        nodes=NODES,
        edges=EDGES,
        legend=["api"],
    )
