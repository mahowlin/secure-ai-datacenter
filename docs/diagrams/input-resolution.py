#!/usr/bin/env python3
"""Generate the current-versus-v2 input resolution diagram for COMPOSITION_MODEL.md."""

from diagram_theme import write_diagram


NODES = [
    {"id": "current", "x": 60, "y": 170, "width": 260, "height": 160, "kind": "author", "title": "Example inputs", "lines": ["data/clusters + networks", "data/defaults", "platform-release.yaml"], "badge": "what you edit"},
    {"id": "catalog-check", "x": 390, "y": 170, "width": 230, "height": 160, "kind": "resolve", "title": "Profile loader", "lines": ["look for data/catalogs/", "directory is absent", "select legacy adapter"]},
    {"id": "legacy", "x": 690, "y": 170, "width": 230, "height": 160, "kind": "resolve", "title": "Strict legacy adapter", "lines": ["validate current shape", "preserve value origins", "build typed intent"]},
    {"id": "resolved", "x": 1010, "y": 282, "width": 240, "height": 170, "kind": "resolve", "title": "Resolved intent", "lines": ["clusters + releases", "networks + profiles", "renderer authorization"]},
    {"id": "renderers", "x": 1320, "y": 282, "width": 220, "height": 170, "kind": "target", "title": "Current renderers", "lines": ["ACI · UCS · OCP", "GitOps", "repo-shaped output"]},
    {"id": "v2", "x": 60, "y": 470, "width": 260, "height": 178, "kind": "planned", "title": "Versioned schema", "lines": ["schemas/saif-platform-v2", "separate release documents", "not loaded by the render"], "badge": "not an input"},
    {"id": "schema", "x": 390, "y": 470, "width": 230, "height": 178, "kind": "planned", "title": "V2 schema", "lines": ["saif-platform-v2", "independent OCP + IEP", "strict document shapes"]},
    {"id": "tests", "x": 690, "y": 470, "width": 230, "height": 178, "kind": "planned", "title": "Profile test suite", "lines": ["schema + compatibility", "authorization + transitions", "resolver behavior"]},
]

EDGES = [
    {"relation": "delivery", "points": [(320, 250), (390, 250)], "directed": True, "label": "load", "label_at": (355, 232)},
    {"relation": "api", "points": [(620, 250), (690, 250)], "directed": True, "label": "fallback", "label_at": (655, 232)},
    {"relation": "api", "points": [(920, 250), (965, 250), (965, 367), (1010, 367)], "directed": True, "label": "typed model", "label_at": (966, 348), "label_size": 11.0},
    {"relation": "delivery", "points": [(1250, 367), (1320, 367)], "directed": True, "label": "authorize", "label_at": (1285, 349), "label_size": 11.0},
    {"relation": "api", "points": [(320, 559), (390, 559)], "directed": False, "label": "validates", "label_at": (355, 541), "label_size": 11.0},
    {"relation": "api", "points": [(620, 559), (690, 559)], "directed": True, "label": "tests", "label_at": (655, 541)},
    {"relation": "api", "points": [(920, 559), (965, 559), (965, 432), (1010, 432)], "directed": True, "label": "test-only", "label_at": (965, 500)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "input-resolution.svg",
        title="How the example inputs become a render",
        subtitle="The data tree is what the renderers read. The versioned schema is checked, and it is not an input.",
        description="Cluster, network and release files go through the legacy adapter into a typed intent. The versioned schema describes a separate document shape the current render does not load.",
        width=1600, height=730,
        zones=[
            {"id": "current", "x": 32, "y": 112, "width": 1518, "height": 280, "label": "Current composition path", "note": "Maintained input can authorize target rendering"},
            {"id": "future", "x": 32, "y": 418, "width": 928, "height": 260, "label": "Versioned schema, not rendered"},
        ],
        nodes=NODES, edges=EDGES, legend=["delivery", "api"],
    )
