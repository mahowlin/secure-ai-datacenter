#!/usr/bin/env python3
"""Generate the SNO realization diagram for COMPOSITION_MODEL.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "intent", "x": 40, "y": 165, "width": 235, "height": 155, "kind": "author",
     "title": "SNO composition", "bullets": ["example-sno", "mode: sno", "one rendezvous node"]},
    {"id": "aci", "x": 300, "y": 165, "width": 235, "height": 155, "kind": "external",
     "title": "ACI VLAN 2101", "bullets": ["management network", "gateway 198.18.1.1", "existing fabric attachment"]},
    {"id": "ucs", "x": 560, "y": 165, "width": 235, "height": 155, "kind": "target",
     "title": "UCS vNIC identity", "bullets": ["selected server profile", "vNICs eth0 + eth1", "VLAN 2101 allowed"]},
    {"id": "inputs", "x": 820, "y": 165, "width": 235, "height": 155, "kind": "target",
     "title": "Install inputs", "bullets": ["install-config.yaml", "AgentConfig bond0", "metadata + manifests"]},
    {"id": "iso", "x": 35, "y": 470, "width": 320, "height": 205, "kind": "runtime",
     "title": "Agent ISO generation", "bullets": ["install-config + AgentConfig", "runtime secrets + hardware facts", "Day 1 + image-policy manifests", "openshift-install build command", "output: Agent ISO"]},
    {"id": "media", "x": 390, "y": 470, "width": 320, "height": 205, "kind": "external",
     "title": "HTTP and vMedia delivery", "bullets": ["upload to the file server", "Intersight vMedia policy", "ISO mounted as boot media"]},
    {"id": "metal", "x": 745, "y": 470, "width": 320, "height": 205, "kind": "external",
     "title": "Selected bare-metal server", "bullets": ["UCS profile attached", "eth0 + eth1 become bond0", "one-time boot from Agent ISO"]},
    {"id": "services", "x": 70, "y": 825, "width": 390, "height": 165, "kind": "external",
     "title": "Shared service dependencies", "bullets": ["DNS and NTP", "configured registry", "release images and catalogs"]},
    {"id": "sno", "x": 640, "y": 825, "width": 390, "height": 165, "kind": "runtime",
     "title": "Single-node OpenShift", "bullets": ["example-sno · 198.18.1.69", "OVN-Kubernetes initially", "Cilium Day-2 migration selected"]},
]

EDGES = [
    {"relation": "delivery", "points": [(275, 242), (300, 242)], "directed": True},
    {"relation": "protocol", "points": [(535, 242), (560, 242)]},
    {"relation": "delivery", "points": [(795, 242), (820, 242)], "directed": True},
    {"relation": "delivery", "points": [(938, 320), (938, 380), (195, 380), (195, 470)], "directed": True, "label": "installer workspace", "label_at": (565, 375)},
    {"relation": "delivery", "points": [(355, 572), (390, 572)], "directed": True, "label": "Agent ISO", "label_at": (372, 455)},
    {"relation": "delivery", "points": [(710, 572), (745, 572)], "directed": True, "label": "mount", "label_at": (727, 455)},
    {"relation": "protocol", "points": [(677, 320), (677, 410), (905, 410), (905, 470)], "label": "profile + vNICs", "label_at": (790, 405)},
    {"relation": "delivery", "points": [(905, 675), (905, 750), (835, 750), (835, 825)], "directed": True, "label": "boots and installs", "label_at": (870, 745)},
    {"relation": "supply", "points": [(460, 907), (640, 907)], "directed": True, "label": "DNS · time · images", "label_at": (550, 890)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "sno-realization.svg",
        title="SNO composition realized on one example server",
        subtitle="Installation input becomes an Agent ISO before HTTP delivery, vMedia mounting and bare-metal boot.",
        description="The reference composition selects ACI VLAN 2101 and a UCS vNIC profile. Rendered install-config, AgentConfig and selected manifests are enriched by the target workflow, built into an Agent ISO, uploaded to the file server, mounted through Intersight vMedia and booted on the selected server.",
        width=1100, height=1050,
        zones=[
            {"id": "contract", "x": 20, "y": 105, "width": 1060, "height": 245, "label": "Composition and installation contract"},
            {"id": "installation", "x": 15, "y": 420, "width": 1070, "height": 285, "label": "Agent ISO build and delivery"},
            {"id": "runtime", "x": 40, "y": 775, "width": 1020, "height": 245, "label": "Installed runtime and shared dependencies"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "protocol", "supply"],
    )
