#!/usr/bin/env python3
"""Generate the management-cluster construct for COMPOSITION_MODEL.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "gitops", "x": 170, "y": 210, "width": 760, "height": 155, "kind": "target",
     "title": "openshift-gitops namespace", "bullets": ["Argo CD Applications + sync waves", "management and remote cluster targets", "desired-state ordering; readiness stays controller-owned"]},
    {"id": "cnv", "x": 55, "y": 475, "width": 310, "height": 175, "kind": "runtime",
     "title": "openshift-cnv", "bullets": ["HyperConverged operand", "KubeVirt + CDI controllers", "VM + DataVolume APIs"]},
    {"id": "mce", "x": 395, "y": 475, "width": 310, "height": 175, "kind": "runtime",
     "title": "multicluster-engine", "bullets": ["MultiClusterEngine operand", "enables HyperShift controllers", "HostedCluster + NodePool APIs"]},
    {"id": "network", "x": 735, "y": 475, "width": 310, "height": 175, "kind": "runtime",
     "title": "Network platform", "bullets": ["openshift-nmstate + Cilium", "NNCP, NAD + CiliumConfig", "prepares host + cluster networking"]},
    {"id": "handoff", "x": 170, "y": 770, "width": 760, "height": 155, "kind": "author",
     "title": "Tenant lifecycle handoff", "bullets": ["HostedCluster + NodePool desired state", "CNV, MCE + network APIs required", "tenant realization continues in the next view"]},
]

EDGES = [
    {"relation": "delivery", "points": [(350, 365), (350, 415), (210, 415), (210, 475)], "directed": True, "label": "operator + operand", "label_at": (280, 410)},
    {"relation": "delivery", "points": [(550, 365), (550, 475)], "directed": True, "label": "operator + operand", "label_at": (550, 410)},
    {"relation": "delivery", "points": [(750, 365), (750, 415), (890, 415), (890, 475)], "directed": True, "label": "platform apps", "label_at": (820, 410)},
    {"relation": "api", "points": [(210, 650), (210, 710), (390, 710), (390, 770)], "directed": True, "label": "VM APIs", "label_at": (300, 705)},
    {"relation": "api", "points": [(550, 650), (550, 770)], "directed": True, "label": "HCP APIs", "label_at": (550, 705)},
    {"relation": "api", "points": [(890, 650), (890, 710), (710, 710), (710, 770)], "directed": True, "label": "network prerequisites", "label_at": (800, 705)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "management-tenant-architecture.svg",
        title="Management cluster construct before a hosted tenant is created",
        subtitle="Four management areas provide GitOps, virtual-machine, hosted-control-plane and network capabilities.",
        description="The management OpenShift cluster contains Argo CD, OpenShift Virtualization, MultiCluster Engine with HyperShift, and the NMState and Cilium network platform. Together their APIs and controllers establish the prerequisites for HostedCluster and NodePool desired state. Tenant runtime internals are intentionally shown in the following tenant drill-down instead of duplicated here.",
        width=1100, height=1005,
        zones=[
            {"id": "management", "x": 20, "y": 105, "width": 1060, "height": 860, "label": "Management OpenShift cluster boundary", "note": "Internal tenant runtime is intentionally deferred to the tenant drill-down."},
            {"id": "gitops", "x": 145, "y": 160, "width": 810, "height": 235, "label": "Desired-state owner"},
            {"id": "platform", "x": 30, "y": 425, "width": 1040, "height": 255, "label": "Installed namespaces, operands and APIs"},
            {"id": "handoff", "x": 145, "y": 720, "width": 810, "height": 245, "label": "High-level handoff to tenant realization"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "api"],
    )
