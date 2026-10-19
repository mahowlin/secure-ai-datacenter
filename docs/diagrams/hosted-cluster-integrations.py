#!/usr/bin/env python3
"""Generate hosted-cluster reconciliation contracts for INTEGRATIONS.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "repo", "x": 70, "y": 165, "width": 360, "height": 155, "kind": "target",
     "title": "secure-ai-gitops", "bullets": ["clusters/example-management entrypoints", "operators, network + lifecycle paths", "guest application paths"]},
    {"id": "argo", "x": 770, "y": 165, "width": 360, "height": 155, "kind": "runtime",
     "title": "Argo CD on management OpenShift", "bullets": ["pulls desired state from Git", "sync waves order Applications", "local and named remote targets"]},
    {"id": "management-api", "x": 410, "y": 425, "width": 380, "height": 165, "kind": "runtime",
     "title": "Management Kubernetes API", "bullets": ["server: kubernetes.default.svc", "accepts management desired state", "controllers own readiness"]},
    {"id": "operators", "x": 45, "y": 690, "width": 340, "height": 185, "kind": "target",
     "title": "Operators and operands", "bullets": ["Subscriptions + operands", "CNV, MCE/HyperShift + NMState", "OLM and operator controllers reconcile"]},
    {"id": "network", "x": 430, "y": 690, "width": 340, "height": 185, "kind": "target",
     "title": "Parent network desired state", "bullets": ["NNCP, NAD + Cilium resources", "NMState and Cilium reconcile", "runtime health remains separate"]},
    {"id": "lifecycle", "x": 815, "y": 690, "width": 340, "height": 185, "kind": "target",
     "title": "HostedCluster and NodePool", "bullets": ["selected lifecycle namespace", "HyperShift + KubeVirt watch APIs", "bootstrap + runtime are controller-owned"]},
    {"id": "kubeconfig", "x": 90, "y": 990, "width": 420, "height": 165, "kind": "runtime",
     "title": "HyperShift admin kubeconfig Secret", "bullets": ["created in selected lifecycle namespace", "contains tenant API endpoint + trust", "secret value never enters Git"]},
    {"id": "registration", "x": 690, "y": 990, "width": 420, "height": 165, "kind": "runtime",
     "title": "Argo cluster registration", "bullets": ["Sync hook Job reads the kubeconfig", "creates named cluster Secret", "registers destination selected tenant"]},
    {"id": "tenant-api", "x": 90, "y": 1290, "width": 420, "height": 175, "kind": "runtime",
     "title": "selected tenant Kubernetes API", "bullets": ["separate remote API endpoint", "selected by destination.name", "available only after HCP bootstrap"]},
    {"id": "guest-apps", "x": 690, "y": 1290, "width": 420, "height": 175, "kind": "target",
     "title": "Guest Day 2 desired state", "bullets": ["Cilium + catalogs", "policy, secrets + observability", "reconciled through tenant API"]},
]

EDGES = [
    {"relation": "delivery", "points": [(430, 242), (770, 242)], "directed": True, "label": "Git pull", "label_at": (600, 225)},
    {"relation": "delivery", "points": [(950, 320), (950, 365), (750, 365), (750, 425)], "directed": True, "label": "local reconciliation", "label_at": (775, 360)},
    {"relation": "api", "points": [(500, 590), (500, 635), (215, 635), (215, 690)], "directed": True, "label": "Kubernetes objects", "label_at": (357, 630)},
    {"relation": "api", "points": [(600, 590), (600, 690)], "directed": True, "label": "Kubernetes objects", "label_at": (600, 630)},
    {"relation": "api", "points": [(700, 590), (700, 635), (985, 635), (985, 690)], "directed": True, "label": "Kubernetes objects", "label_at": (842, 630)},
    {"relation": "api", "points": [(985, 875), (985, 930), (300, 930), (300, 990)], "directed": True, "label": "admin kubeconfig", "label_at": (642, 925)},
    {"relation": "trust", "points": [(510, 1072), (690, 1072)], "directed": True, "label": "least-privilege read", "label_at": (600, 1055)},
    {"relation": "delivery", "points": [(950, 320), (1170, 320), (1170, 1240), (300, 1240), (300, 1290)], "directed": True, "label": "remote reconciliation after registration", "label_at": (735, 1235)},
    {"relation": "delivery", "points": [(510, 1377), (690, 1377)], "directed": True, "label": "destination: selected tenant", "label_at": (600, 1360)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "hosted-cluster-integrations.svg",
        title="Hosted-cluster reconciliation contracts: local API, registration and tenant API",
        subtitle="Argo CD uses the management API for platform state and a separately registered tenant API for guest Day 2 state.",
        description="Argo CD reads cluster entrypoints from secure-ai-gitops and reconciles management desired state to the local Kubernetes API. Management controllers realize operators, parent networking, HostedCluster and NodePool resources. HyperShift produces an admin kubeconfig Secret; a least-privilege Sync hook Job registers the tenant API with Argo CD. Guest Applications then use destination name selected tenant. These are desired-state and API contracts, not workload traffic paths.",
        width=1200, height=1535,
        zones=[
            {"id": "source", "x": 35, "y": 105, "width": 1130, "height": 245, "label": "Git desired-state source and reconciler"},
            {"id": "management", "x": 20, "y": 385, "width": 1160, "height": 520, "label": "Management-cluster Kubernetes API and controller contracts"},
            {"id": "registration", "x": 55, "y": 940, "width": 1090, "height": 245, "label": "Tenant API registration without committing kubeconfig material"},
            {"id": "guest", "x": 55, "y": 1240, "width": 1090, "height": 255, "label": "Guest-cluster Day 2 reconciliation"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "api", "trust"],
    )
