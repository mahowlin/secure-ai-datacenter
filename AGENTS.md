# Secure AI Datacenter: agent entry point

Compose declared site and product inputs into reproducible Fabric, Compute and GitOps artifacts. See [README](README.md) for the human composition.

## Route the task

| Goal | Authoritative editable input | Procedure | Actual output | Relevant check | Next owner |
|---|---|---|---|---|---|
| Install a standalone cluster | `data/clusters/`, `data/networks/`, `data/defaults/`, `platform-release.yaml` | [installation](agents/installation.md) | Agent raw files + artifact manifest | repeat render / manifest verifier | Compute |
| Compose hosted clusters and shared services | cluster Day 2/HCP inputs, `repo-map.yaml` | [integrated](agents/integrated.md) | cluster roots, Applications, overlays | selected root/child builds; replica checks | GitOps |
| Prepare optional mirror operator | local-registry selection + `data/package-contracts/oc-mirror-operator-v0.1.7.json` | [operator](agents/mirror-operator.md) | verified external input selection + manual OLM resources | prepared-input hashes / operator tests | Airgap prepares inputs; GitOps installs |
| Render/import fabric | network/access inputs | [fabric](agents/fabric.md) | selected YAML and exact handoff | staged scan / input closure | Fabric |
| Change generated ownership or runtime enrichment | owning inputs; transport/CNI/attestation code | [artifact checks](agents/generated-artifacts.md) | exact manifest-listed payloads | targeted transport/CNI/commit tests | affected consumer owner |
| Plan cache population | selected image inventory/cache policy | [hosted cache](agents/hosted-cache.md) | inventory and consumer configuration | selected-cache closure checks | Airgap and GitOps |

## Toolchain and execution boundary

Use Python 3 with `requirements.txt` in `.venv` (or the existing requirements environment), Git, SHA-256 tools and the selected procedure toolchain. `PLATFORM_ROOT` is code; `SITE_ROOT` is editable declared input. Prepare external operator inputs explicitly before enabled-operator resolution. Never hand-edit `generated/`. Local render/hash checks are offline. Transport may commit/push; activation uses its owning pipeline. Pins and render tests do not establish installed support.

Keep exact executable sequences in the linked procedure; update its input/output/check handoff when an interface changes. Record unknown runtime facts as unknown rather than inferring them from local success.
