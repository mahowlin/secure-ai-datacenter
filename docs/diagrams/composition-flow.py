#!/usr/bin/env python3
"""Generate the reference composition input-to-deployment overview."""

from diagram_theme import write_diagram


NODES = [
    {
        "id": "clusters",
        "x": 50,
        "y": 170,
        "width": 310,
        "height": 160,
        "kind": "author",
        "title": "Cluster and tenant selection",
        "bullets": [
            "clusters/example-sno/cluster.yaml",
            "clusters/example-management/",
            "selected hosted clusters",
        ],
    },
    {
        "id": "network-defaults",
        "x": 395,
        "y": 170,
        "width": 310,
        "height": 160,
        "kind": "author",
        "title": "Named networks and defaults",
        "bullets": [
            "networks/example-federal.yaml",
            "defaults/example, ACI and UCS",
            "parent, network and pool names",
        ],
    },
    {
        "id": "release",
        "x": 740,
        "y": 170,
        "width": 310,
        "height": 160,
        "kind": "author",
        "title": "Release and asset selection",
        "bullets": [
            "platform-release.yaml",
            "bundles and CRD catalogues",
            "selected by releaseRef",
        ],
    },
    {
        "id": "combine",
        "x": 170,
        "y": 445,
        "width": 760,
        "height": 195,
        "kind": "resolve",
        "title": "Combine and validate the reference composition",
        "bullets": [
            "Select the cluster and its hosted tenants.",
            "Resolve parent, network, pool and release references.",
            "Apply the example site, ACI and UCS defaults.",
            "Reject missing names, duplicates and incompatible combinations.",
            "Authorize only the selected target outputs.",
            "Executable contract: scripts/resolver.py",
        ],
    },
    {
        "id": "aci-target",
        "x": 170,
        "y": 770,
        "width": 760,
        "height": 175,
        "kind": "target",
        "title": "ACI target",
        "bullets": [
            "Render: render-aci.py creates the review copy.",
            "Destination: secure-ai-fabric → data/ tenant + access",
            "Deploy: owning ACI pipeline validates, plans and applies reviewed NAC-ACI intent.",
            "Outcome: tenant, VRF, bridge-domain and contract configuration.",
        ],
    },
    {
        "id": "ucs-target",
        "x": 170,
        "y": 970,
        "width": 760,
        "height": 175,
        "kind": "target",
        "title": "UCS target",
        "bullets": [
            "Render: render-ucs.py creates the review copy.",
            "Destination: secure-ai-compute → data/ucs/*.ezi.yaml",
            "Deploy: GitHub Actions sends Easy-IMM intent to Intersight.",
            "Outcome: UCS policies and server profiles on the selected hardware.",
        ],
    },
    {
        "id": "ocp-target",
        "x": 170,
        "y": 1170,
        "width": 760,
        "height": 175,
        "kind": "target",
        "title": "OpenShift installation: portable or UCS",
        "bullets": [
            "Render: render-ocp.py creates the review copy.",
            "Destination: secure-ai-compute → data/openshift/<cluster>/",
            "Portable: Agent ISO → site-owned boot or virtual media.",
            "UCS variant: GitHub Actions → Intersight vMedia.",
            "Outcome: selected bare-metal servers boot and install OpenShift.",
        ],
    },
    {
        "id": "gitops-target",
        "x": 170,
        "y": 1370,
        "width": 760,
        "height": 175,
        "kind": "target",
        "title": "Kubernetes and Argo CD target",
        "bullets": [
            "Render: render-gitops.py creates the review copy.",
            "Destination: secure-ai-gitops → apps/ and clusters/",
            "Deploy: Argo CD follows cluster entrypoints and reconciles the selected applications.",
            "Outcome: Kubernetes desired state converges continuously.",
        ],
    },
]


EDGES = [
    {"relation": "delivery", "points": [(205, 330), (205, 360), (500, 360), (500, 445)], "directed": True},
    {"relation": "delivery", "points": [(550, 330), (550, 445)], "directed": True},
    {"relation": "delivery", "points": [(895, 330), (895, 375), (675, 375), (675, 445)], "directed": True},
    {
        "relation": "delivery",
        "points": [(550, 640), (550, 700), (120, 700), (120, 1458)],
        "label": "four parallel target deliveries",
        "label_at": (360, 695),
    },
    {"relation": "delivery", "points": [(120, 858), (170, 858)], "directed": True},
    {"relation": "delivery", "points": [(120, 1058), (170, 1058)], "directed": True},
    {"relation": "delivery", "points": [(120, 1258), (170, 1258)], "directed": True},
    {"relation": "delivery", "points": [(120, 1458), (170, 1458)], "directed": True},
]


if __name__ == "__main__":
    write_diagram(
        __file__,
        "composition-flow.svg",
        title="Reference composition to deployed configuration",
        subtitle="Maintained intent becomes exact target files; each destination owns how those files are applied.",
        description="Example site cluster, network, default and release input is combined into one resolved composition, then translated in parallel into exact ACI, UCS, OpenShift and GitOps repository paths. Each target repository owns its deployment mechanism. Portable installation hands the Agent ISO to the site-owned boot process; the UCS automation variant uses Intersight vMedia.",
        width=1100,
        height=1610,
        zones=[
            {
                "id": "input",
                "x": 25,
                "y": 105,
                "width": 1050,
                "height": 250,
                "label": "1. Example site input",
                "note": "Choose a composition; its names select the related data.",
            },
            {
                "id": "resolve",
                "x": 145,
                "y": 375,
                "width": 810,
                "height": 285,
                "label": "2. Combine and validate",
                "note": "One complete model.",
            },
            {
                "id": "targets",
                "x": 145,
                "y": 700,
                "width": 810,
                "height": 870,
                "label": "3. Translate, review and deploy in each owning system",
                "note": "These four deliveries are parallel outputs of the same resolved composition.",
            },
        ],
        nodes=NODES,
        edges=EDGES,
        legend=["delivery"],
    )
