#!/usr/bin/env python3
"""Generate management-push and hosted-object Timescape integrations."""

from diagram_theme import write_diagram


NODES = [
    {"id": "parent", "x": 35, "y": 165, "width": 235, "height": 155, "kind": "runtime",
     "title": "Management Cilium", "bullets": ["Hubble flow logs", "Kubernetes events", "connection logs"]},
    {"id": "parent-vector", "x": 300, "y": 165, "width": 235, "height": 155, "kind": "runtime",
     "title": "Management Vector", "bullets": ["reads hostPath logs", "adds cluster identity", "uses an HTTP push sink"]},
    {"id": "push", "x": 565, "y": 165, "width": 235, "height": 155, "kind": "external",
     "title": "Timescape push API", "bullets": ["credentialed HTTP", "newline-delimited data", "management-cluster path"]},
    {"id": "tenant", "x": 35, "y": 465, "width": 235, "height": 155, "kind": "runtime",
     "title": "Each hosted Cilium", "bullets": ["Hubble flow logs", "Kubernetes events", "connection logs"]},
    {"id": "exporters", "x": 300, "y": 465, "width": 235, "height": 155, "kind": "runtime",
     "title": "Hosted exporters", "bullets": ["hubble-k8s-exporter", "Vector S3 exporter", "cluster-specific prefix"]},
    {"id": "object", "x": 565, "y": 465, "width": 235, "height": 155, "kind": "external",
     "title": "RustFS storage", "bullets": ["shared service host", "saif-timescape bucket", "hubble-events/<cluster>/"]},
    {"id": "ingestion", "x": 835, "y": 285, "width": 235, "height": 215, "kind": "target",
     "title": "Timescape ingestion", "bullets": ["accepts push API data", "reads RustFS objects", "three object patterns", "restores tenant identity", "indexes multi-cluster data"]},
    {"id": "timescape", "x": 360, "y": 755, "width": 380, "height": 150, "kind": "target",
     "title": "Hubble Timescape query and UI", "bullets": ["queries only ingested data", "preserves the source cluster", "supports multi-cluster investigation"]},
]

EDGES = [
    {"relation": "telemetry", "points": [(270, 242), (300, 242)], "directed": True},
    {"relation": "telemetry", "points": [(535, 242), (565, 242)], "directed": True},
    {"relation": "telemetry", "points": [(800, 242), (820, 242), (820, 340), (835, 340)], "directed": True},
    {"relation": "telemetry", "points": [(270, 542), (300, 542)], "directed": True},
    {"relation": "trust", "points": [(535, 542), (565, 542)], "directed": True},
    {"relation": "telemetry", "points": [(800, 542), (820, 542), (820, 445), (835, 445)], "directed": True},
    {"relation": "telemetry", "points": [(952, 500), (952, 680), (550, 680), (550, 755)], "directed": True, "label": "indexed data", "label_at": (750, 675)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "observability-integrations.svg",
        title="Multi-cluster observability integrations",
        subtitle="Management data is pushed; hosted data is stored in RustFS and read before it reaches Timescape queries.",
        description="Management Hubble logs use Vector and the Timescape push API. Hosted clusters use exporters that write cluster-prefixed objects to object storage on the shared service host. Timescape ingestion reads both paths and indexes them before the query interface can use them.",
        width=1100, height=965,
        zones=[
            {"id": "management", "x": 20, "y": 105, "width": 800, "height": 245, "label": "Management-cluster push path"},
            {"id": "hosted", "x": 20, "y": 405, "width": 800, "height": 245, "label": "Per-hosted-cluster object path"},
            {"id": "ingest", "x": 810, "y": 105, "width": 270, "height": 545, "label": "Common ingestion"},
            {"id": "query", "x": 325, "y": 705, "width": 450, "height": 220, "label": "Query and investigation"},
        ], nodes=NODES, edges=EDGES, legend=["telemetry", "trust"],
    )
