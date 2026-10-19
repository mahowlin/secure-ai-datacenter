#!/usr/bin/env python3
"""Generate the hosted-tenant drill-down for COMPOSITION_MODEL.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "intent", "x": 55, "y": 165, "width": 300, "height": 165, "kind": "author",
     "title": "Tenant composition", "bullets": ["selected tenant input", "replicas, CIDRs + release", "network, pool + storage refs"]},
    {"id": "lifecycle", "x": 420, "y": 165, "width": 625, "height": 165, "kind": "target",
     "title": "selected lifecycle namespace lifecycle resources", "bullets": ["HostedCluster selected tenant defines the hosted API and control plane", "NodePool selected worker NodePool defines KubeVirt compute", "HyperShift and CNV controllers own realization"]},
    {"id": "control", "x": 55, "y": 475, "width": 475, "height": 260, "kind": "runtime",
     "title": "Hosted control-plane namespace", "bullets": ["HyperShift-owned HCP namespace", "etcd + kube-apiserver", "controller-manager + scheduler", "OAuth + Konnectivity services", "management pod network", "created and owned by HyperShift"]},
    {"id": "workers", "x": 570, "y": 475, "width": 475, "height": 260, "kind": "runtime",
     "title": "KubeVirt worker realization", "bullets": ["NodePool worker intent", "VirtualMachine objects", "virt-launcher pods", "DataVolume root disks", "selected worker interfaces", "VMs register as guest nodes"]},
    {"id": "guest", "x": 170, "y": 885, "width": 760, "height": 205, "kind": "target",
     "title": "Logical hosted tenant OpenShift cluster", "bullets": ["API endpoint is served by hosted control-plane components", "schedulable nodes are KubeVirt worker VMs", "guest Cilium, catalogs, policy, secrets + observability", "runtime health requires separate validation"]},
]

EDGES = [
    {"relation": "delivery", "points": [(355, 247), (420, 247)], "directed": True, "label": "renders", "label_at": (387, 230)},
    {"relation": "api", "points": [(620, 330), (620, 390), (292, 390), (292, 475)], "directed": True, "label": "HostedCluster", "label_at": (456, 385)},
    {"relation": "api", "points": [(845, 330), (845, 475)], "directed": True, "label": "NodePool + KubeVirt", "label_at": (845, 385)},
    {"relation": "api", "points": [(292, 735), (292, 810), (450, 810), (450, 885)], "directed": True, "label": "hosted API", "label_at": (371, 805)},
    {"relation": "api", "points": [(807, 735), (807, 810), (650, 810), (650, 885)], "directed": True, "label": "node registration", "label_at": (728, 805)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "hosted-tenant-realization.svg",
        title="Hosted tenant cluster drill-down: control plane, workers and guest boundary",
        subtitle="Hosted control-plane pods and KubeVirt worker VMs are sibling runtime building blocks with different owners.",
        description="The selected tenant composition renders HostedCluster and NodePool resources. HyperShift creates a dedicated hosted-control-plane namespace containing representative API, etcd, controller, OAuth and Konnectivity components. The KubeVirt provider creates VirtualMachines, virt-launcher pods and DataVolume-backed worker disks. The hosted API and registered worker VMs form the logical guest cluster.",
        width=1100, height=1160,
        zones=[
            {"id": "desired", "x": 25, "y": 105, "width": 1050, "height": 255, "label": "Tenant desired state on management OpenShift"},
            {"id": "runtime", "x": 25, "y": 420, "width": 1050, "height": 345, "label": "Controller-created runtime inside management OpenShift", "note": "Detailed control-plane-to-worker communication belongs to the network architecture stage."},
            {"id": "guest", "x": 145, "y": 835, "width": 810, "height": 285, "label": "Logical guest-cluster boundary"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "api"],
    )
