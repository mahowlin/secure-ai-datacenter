#!/usr/bin/env python3
"""Generate the compact management realization for COMPOSITION_MODEL.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "intent", "x": 45, "y": 165, "width": 300, "height": 175, "kind": "author",
     "title": "Compact composition", "bullets": ["example-management", "example-node-2, 3 and 4", "selected platform layers"]},
    {"id": "aci", "x": 450, "y": 165, "width": 300, "height": 175, "kind": "external",
     "title": "ACI network foundation", "bullets": ["management VLAN 2101", "tenant VLAN allowance", "routing remains fabric-owned"]},
    {"id": "ucs", "x": 855, "y": 165, "width": 300, "height": 175, "kind": "target",
     "title": "UCS Day 0 compute", "bullets": ["three server profiles", "management vNICs eth0 + eth1", "tenant vNICs eth2 + eth3", "does not create host bond1"]},
    {"id": "day1", "x": 80, "y": 450, "width": 440, "height": 170, "kind": "target",
     "title": "Agent-based Day 1", "bullets": ["AgentConfig creates bond0 only", "install-config selects Cilium", "Agent ISO boots three servers"]},
    {"id": "ocp", "x": 680, "y": 450, "width": 440, "height": 170, "kind": "runtime",
     "title": "Compact management OpenShift", "bullets": ["three control-plane and worker nodes", "bond0 carries management", "platform services are selected layers"]},
    {"id": "gitops", "x": 45, "y": 755, "width": 330, "height": 200, "kind": "target",
     "title": "Argo CD desired state", "bullets": ["operator + operand Applications", "parent network + tenant lifecycle", "guest apps use a named target"]},
    {"id": "management-api", "x": 435, "y": 755, "width": 330, "height": 200, "kind": "runtime",
     "title": "Management Kubernetes API", "bullets": ["destination: kubernetes.default.svc", "receives management Applications", "controller readiness stays separate"]},
    {"id": "controllers", "x": 825, "y": 755, "width": 330, "height": 200, "kind": "runtime",
     "title": "Selected platform controllers", "bullets": ["OLM + CNV/KubeVirt", "MCE + HyperShift", "NMState + Cilium"]},
    {"id": "nmstate", "x": 120, "y": 1045, "width": 410, "height": 180, "kind": "runtime",
     "title": "NMState Day 2 host intent", "bullets": ["NNCP bond1-tenant-transport", "eth2 + eth3 become bond1", "VLAN interfaces follow after install"]},
    {"id": "lifecycle", "x": 670, "y": 1045, "width": 410, "height": 180, "kind": "author",
     "title": "Hosted-cluster desired state", "bullets": ["HostedCluster + NodePool", "release, mirror, storage + network", "bootstrap is controller-owned"]},
    {"id": "control", "x": 90, "y": 1335, "width": 450, "height": 180, "kind": "runtime",
     "title": "Hosted control-plane pods", "bullets": ["created by HyperShift", "run on management pod networking", "serve the hosted cluster API"]},
    {"id": "workers", "x": 660, "y": 1335, "width": 450, "height": 180, "kind": "runtime",
     "title": "KubeVirt worker VMs", "bullets": ["created from the NodePool", "root volume + worker interfaces", "register as guest nodes"]},
    {"id": "guest-api", "x": 90, "y": 1630, "width": 400, "height": 175, "kind": "target",
     "title": "Tenant Kubernetes API", "bullets": ["registered in Argo CD as selected tenant", "separate remote destination", "available after HCP bootstrap"]},
    {"id": "guest-apps", "x": 710, "y": 1630, "width": 400, "height": 175, "kind": "target",
     "title": "Guest Day 2 applications", "bullets": ["Cilium + catalogs", "policy, secrets + observability", "reconciled through tenant API"]},
]

EDGES = [
    {"relation": "delivery", "points": [(345, 252), (450, 252)], "directed": True},
    {"relation": "protocol", "points": [(750, 252), (855, 252)]},
    {"relation": "delivery", "points": [(195, 340), (195, 450)], "directed": True},
    {"relation": "protocol", "points": [(600, 340), (600, 400), (800, 400), (800, 450)], "label": "fabric contract", "label_at": (700, 395)},
    {"relation": "delivery", "points": [(1005, 340), (1005, 400), (1000, 400), (1000, 450)], "directed": True, "label": "profiles + vNICs", "label_at": (1002, 395)},
    {"relation": "delivery", "points": [(520, 535), (680, 535)], "directed": True, "label": "Agent ISO", "label_at": (600, 518)},
    {"relation": "api", "points": [(900, 620), (900, 680), (210, 680), (210, 755)], "label": "hosts Argo CD", "label_at": (555, 675)},
    {"relation": "delivery", "points": [(375, 855), (435, 855)], "directed": True},
    {"relation": "api", "points": [(765, 855), (825, 855)], "directed": True},
    {"relation": "api", "points": [(545, 955), (545, 1005), (325, 1005), (325, 1045)], "directed": True, "label": "NNCP", "label_at": (435, 1000)},
    {"relation": "delivery", "points": [(655, 955), (655, 1005), (875, 1005), (875, 1045)], "directed": True, "label": "HostedCluster + NodePool", "label_at": (765, 1000)},
    {"relation": "api", "points": [(775, 1225), (775, 1270), (315, 1270), (315, 1335)], "directed": True, "label": "HyperShift", "label_at": (545, 1265)},
    {"relation": "api", "points": [(975, 1225), (975, 1335)], "directed": True, "label": "NodePool + KubeVirt", "label_at": (975, 1265)},
    {"relation": "api", "points": [(315, 1515), (315, 1580), (290, 1580), (290, 1630)], "directed": True, "label": "serves API", "label_at": (302, 1575)},
    {"relation": "api", "points": [(885, 1515), (885, 1570), (400, 1570), (400, 1630)], "directed": True, "label": "nodes register", "label_at": (642, 1565)},
    {"relation": "delivery", "points": [(210, 955), (25, 955), (25, 1717), (90, 1717)], "directed": True},
    {"relation": "delivery", "points": [(490, 1717), (710, 1717)], "directed": True, "label": "remote reconciliation", "label_at": (600, 1700)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "compact-management-realization.svg",
        title="Compact management cluster: installation, platform layers and tenant handoff",
        subtitle="UCS creates vNICs; AgentConfig creates bond0; GitOps and NMState create bond1 and hosted-cluster state after installation.",
        description="The reference composition separates UCS vNIC provisioning from OpenShift installation and Day 2 host networking. Argo CD reconciles management Applications through the local Kubernetes API. HyperShift and KubeVirt create hosted control-plane pods and worker VMs, while guest Applications use a separately registered tenant Kubernetes API.",
        width=1200, height=1870,
        zones=[
            {"id": "physical", "x": 20, "y": 105, "width": 1160, "height": 265, "label": "Physical composition: fabric and server interfaces"},
            {"id": "day1", "x": 40, "y": 405, "width": 1120, "height": 245, "label": "Day 1: management OpenShift installation"},
            {"id": "day2", "x": 20, "y": 700, "width": 1160, "height": 555, "label": "Day 2: GitOps and management controllers"},
            {"id": "runtime", "x": 45, "y": 1285, "width": 1110, "height": 260, "label": "Hosted-cluster runtime on management OpenShift"},
            {"id": "guest", "x": 45, "y": 1580, "width": 1110, "height": 255, "label": "Guest-cluster API and Day 2 reconciliation"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "protocol", "api"],
    )
