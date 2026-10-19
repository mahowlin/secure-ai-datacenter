#!/usr/bin/env python3
"""Show worker bootstrap dependencies and later Konnectivity direction."""

from diagram_theme import write_diagram


NODES = [
    {
        "id": "worker-boot",
        "x": 45,
        "y": 160,
        "width": 175,
        "height": 115,
        "kind": "target",
        "title": "Booting worker",
        "lines": ["worker network IP", "not registered yet", "no Konnectivity yet"],
    },
    {
        "id": "service-edge",
        "x": 300,
        "y": 150,
        "width": 190,
        "height": 135,
        "kind": "runtime",
        "title": "Provider service path",
        "lines": ["worker network route", "provider endpoints", "external routing"],
    },
    {
        "id": "hosted-services",
        "x": 640,
        "y": 115,
        "width": 240,
        "height": 95,
        "kind": "author",
        "title": "Hosted endpoints",
        "lines": ["Ignition", "Kubernetes API"],
    },
    {
        "id": "external-services",
        "x": 640,
        "y": 235,
        "width": 240,
        "height": 95,
        "kind": "external",
        "title": "External dependencies",
        "lines": ["DNS · NTP", "container registry"],
    },
    {
        "id": "worker-agent",
        "x": 55,
        "y": 425,
        "width": 210,
        "height": 100,
        "kind": "target",
        "title": "Ready worker",
        "lines": ["Konnectivity agent", "initiates the channel"],
    },
    {
        "id": "konnectivity",
        "x": 355,
        "y": 425,
        "width": 210,
        "height": 100,
        "kind": "runtime",
        "title": "Konnectivity server",
        "lines": ["accepts outbound mTLS", "relays later requests"],
    },
    {
        "id": "control-client",
        "x": 655,
        "y": 425,
        "width": 210,
        "height": 100,
        "kind": "author",
        "title": "Control-plane client",
        "lines": ["uses existing channel", "never dials worker directly"],
    },
]


EDGES = [
    {
        "relation": "protocol",
        "points": [(220, 217), (300, 217)],
        "directed": True,
    },
    {
        "relation": "protocol",
        "points": [(490, 195), (555, 195), (555, 162), (640, 162)],
        "directed": True,
        "label": "Ignition + API",
        "label_at": (596, 154),
        "label_size": 10.0,
    },
    {
        "relation": "protocol",
        "points": [(490, 245), (555, 245), (555, 282), (640, 282)],
        "directed": True,
        "label": "DNS · NTP · images",
        "label_at": (596, 274),
        "label_size": 9.5,
    },
    {
        "relation": "protocol",
        "points": [(265, 460), (355, 460)],
        "directed": True,
        "label": "outbound mTLS",
        "label_at": (310, 452),
        "label_size": 10.0,
    },
    {
        "relation": "api",
        "points": [(655, 495), (565, 495)],
        "directed": True,
        "label": "logical request",
        "label_at": (610, 487),
        "label_size": 10.0,
    },
    {
        "relation": "api",
        "points": [(355, 495), (265, 495)],
        "directed": True,
    },
]


if __name__ == "__main__":
    write_diagram(
        __file__,
        "hcp-konnectivity-direction.svg",
        title="Worker bootstrap and control-plane communication",
        subtitle="Bootstrap dependencies must work first; Konnectivity carries later requests over a worker-established channel.",
        description="Before registration, a worker needs a routed service path to Ignition, the hosted Kubernetes API, DNS, NTP, and the container registry. After bootstrap, the Konnectivity agent opens an outbound mTLS channel to the hosted server. Control-plane clients then use that existing channel for logical requests to the worker.",
        width=920,
        height=590,
        zones=[
            {
                "id": "bootstrap",
                "x": 20,
                "y": 105,
                "width": 880,
                "height": 255,
                "label": "Before Node registration",
                "fill": "#F2ECFF",
                "stroke": "#8B6BD9",
            },
            {
                "id": "established",
                "x": 20,
                "y": 390,
                "width": 880,
                "height": 160,
                "label": "After bootstrap and Node registration",
                "fill": "#E8F2FF",
                "stroke": "#5B8DEF",
            },
        ],
        nodes=NODES,
        edges=EDGES,
        legend=["protocol", "api"],
    )
