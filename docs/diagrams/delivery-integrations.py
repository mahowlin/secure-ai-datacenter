#!/usr/bin/env python3
"""Generate disconnected image, Agent ISO and IDMS integrations."""

from diagram_theme import write_diagram


NODES = [
    {"id": "release", "x": 40, "y": 160, "width": 240, "height": 175, "kind": "author",
     "title": "Release composition", "bullets": ["platform-release.yaml", "OpenShift and IEP artifacts", "image-policy bundles", "profile authorization"]},
    {"id": "upstream", "x": 305, "y": 160, "width": 240, "height": 175, "kind": "external",
     "title": "Upstream sources", "bullets": ["Red Hat release images", "Red Hat operator indexes", "Isovalent Artifactory images", "OCI platform charts"]},
    {"id": "mirror", "x": 570, "y": 160, "width": 240, "height": 175, "kind": "target",
     "title": "secure-ai-airgap", "bullets": ["authenticated mirroring", "immutable bundle evidence", "credentials remain external"]},
    {"id": "registry", "x": 835, "y": 160, "width": 240, "height": 175, "kind": "external",
     "title": "Internal registry", "bullets": ["shared service host", "OpenShift + Isovalent images", "operator catalogs", "OCI platform charts"]},
    {"id": "install-data", "x": 40, "y": 520, "width": 240, "height": 145, "kind": "runtime",
     "title": "Rendered install data", "bullets": ["secure-ai-compute", "data/openshift/<cluster>/", "install and AgentConfig files"]},
    {"id": "agent", "x": 305, "y": 520, "width": 240, "height": 145, "kind": "runtime",
     "title": "Agent ISO build", "bullets": ["openshift-install agent", "create image", "embeds Day 1 image policy"]},
    {"id": "http", "x": 570, "y": 520, "width": 240, "height": 145, "kind": "external",
     "title": "HTTP ISO server", "bullets": ["shared service host", "receives the Agent ISO", "serves the ISO URL"]},
    {"id": "vmedia", "x": 835, "y": 520, "width": 240, "height": 145, "kind": "target",
     "title": "Intersight vMedia", "bullets": ["mounts the HTTP ISO URL", "attaches ISO to servers", "servers boot the Agent ISO"]},
    {"id": "idms", "x": 80, "y": 840, "width": 430, "height": 190, "kind": "runtime",
     "title": "Management ImageDigestMirrorSet", "bullets": ["platform-idms Application", "rewrites public source references", "pulls matching digests from the internal registry", "applies to the management cluster"]},
    {"id": "hosted-policy", "x": 590, "y": 840, "width": 430, "height": 190, "kind": "runtime",
     "title": "HostedCluster imageContentSources", "bullets": ["field on each HostedCluster", "maps public sources to internal mirrors", "used for hosted-worker bootstrap", "not a tenant-side IDMS Application"]},
    {"id": "management", "x": 80, "y": 1130, "width": 430, "height": 150, "kind": "target",
     "title": "Management cluster consumer", "bullets": ["boots from Agent ISO", "pulls mirrored Day 1 and Day 2 images", "reconciles the platform IDMS"]},
    {"id": "hosted", "x": 590, "y": 1130, "width": 430, "height": 150, "kind": "target",
     "title": "Hosted worker consumer", "bullets": ["HCP bootstrap follows imageContentSources", "pulls release images from the internal mirror", "policy is per HostedCluster"]},
]

EDGES = [
    {"relation": "delivery", "points": [(280, 245), (305, 245)], "directed": True},
    {"relation": "trust", "points": [(545, 245), (570, 245)], "directed": True},
    {"relation": "supply", "points": [(810, 245), (835, 245)], "directed": True},
    {"relation": "delivery", "points": [(160, 335), (160, 390), (8, 390), (8, 550), (40, 550)], "directed": True},
    {"relation": "delivery", "points": [(280, 592), (305, 592)], "directed": True},
    {"relation": "delivery", "points": [(545, 592), (570, 592)], "directed": True},
    {"relation": "delivery", "points": [(810, 592), (835, 592)], "directed": True},
    {"relation": "delivery", "points": [(955, 665), (1075, 665), (1075, 1070), (20, 1070), (20, 1205), (80, 1205)], "directed": True, "label": "Agent ISO boot", "label_at": (1010, 700)},
    {"relation": "supply", "points": [(955, 335), (1085, 335), (1085, 760), (550, 760)], "label": "mirror endpoint", "label_at": (850, 755)},
    {"relation": "supply", "points": [(550, 760), (295, 760), (295, 840)], "directed": True},
    {"relation": "supply", "points": [(550, 760), (805, 760), (805, 840)], "directed": True},
    {"relation": "delivery", "points": [(295, 1030), (295, 1130)], "directed": True},
    {"relation": "api", "points": [(805, 1030), (805, 1130)], "directed": True, "label": "HCP bootstrap", "label_at": (805, 1090)},
]

if __name__ == "__main__":
    write_diagram(
        __file__, "delivery-integrations.svg",
        title="Disconnected image supply and bare-metal installation",
        subtitle="The registry image path and the HTTP Agent ISO path are separate deliveries with different consumers.",
        description="Release intent authorizes authenticated mirroring to the configured registry on the shared service host. Separately, rendered install data becomes an Agent ISO. The UCS automation variant shown uploads it to an HTTP container and mounts it through Intersight vMedia. Portable installation hands the same ISO to the site-owned boot process, without requiring Intersight or a supplied BMC adapter. Management IDMS and HostedCluster imageContentSources redirect different consumers to the mirror.",
        width=1100, height=1340,
        zones=[
            {"id": "supply", "x": 20, "y": 105, "width": 1060, "height": 260, "label": "1. Authenticated software supply"},
            {"id": "install", "x": 20, "y": 445, "width": 1060, "height": 250, "label": "2. Agent ISO delivery — UCS automation variant", "note": "Portable installs: hand the same ISO to your site’s boot process; no Intersight required."},
            {"id": "policy", "x": 45, "y": 785, "width": 1010, "height": 275, "label": "3. Two image-source mapping mechanisms"},
            {"id": "consumers", "x": 45, "y": 1080, "width": 1010, "height": 230, "label": "4. Cluster consumers"},
        ], nodes=NODES, edges=EDGES, legend=["delivery", "supply", "trust", "api"],
    )
