# Renderer and validator reference

The maintained inputs are in [data/](../data/README.md), selected releases in [platform-release.yaml](../platform-release.yaml), and destination ownership in [repo-map.yaml](../repo-map.yaml). [AGENTS](../AGENTS.md) links exact procedures; this reference explains the implementation and contracts.

## Input ownership

`agentConfig.inventoryMode: declared` supplies actual NIC names/MACs, boot-disk hints and rendezvous identity in the cluster input. This portable mode does not contact UCS. `runtime` inventory is a separately selected UCS enrichment path; missing declared facts do not silently fall back to it. Policy/defaults are explicit inputs. Runtime credentials, trusted CAs, pull secrets and observed hardware facts retain their private owner and are enriched outside raw source outputs.

## Derivation and outputs

| Source | Implementation | Output / consumer |
|---|---|---|
| Networks, ACI attachment and fabric references | `render-aci.py`, `templates/aci/` | Tenant and shared access NAC YAML in the selected Fabric repo's `data/`; management BD/EPG/access attachment is intentional |
| Cluster UCS selection and `data/defaults/ucs.yaml` convention map | `render-ucs.py`, `templates/cluster-install/ucs/` | Three target-native EZI files under the selected Compute repo's `data/ucs/`; active profiles and runtime execution remain Compute-owned |
| Cluster declared inventory/network/release | `render-ocp.py` | InstallConfig, AgentConfig, metadata and selected conditional Cilium manifests under Compute `data/openshift/<cluster>/` |
| Parent/hosted feature, lifecycle and storage selection | `render-gitops.py`, `gitops_renderer/` | Roots, shared bases/type selections, Applications, overlays, charts and explicit release/runtime contracts in the GitOps repo layout |
| Exact generated payload | `write-artifact-manifest.py`, `verify-artifact-manifest.py` | Target paths and SHA-256 checks; selected semantic validation supplements hash integrity |
| Selected manifest + prior target ownership | `transport-generated-artifacts.py` | Complete owned-file reconciliation, runtime-owner preservation and separately authorized Git transport |

Physical management attachment and tenant BGP are distinct. Optional UCS/FI and direct-attached server variants need their selected inputs; optional OSPF/edge fragments are not prerequisites for all compositions. No hardcoded lab VLAN or retired target directory defines a generic invariant.

## Validation and delivery

[Generated ownership](../agents/generated-artifacts.md#ownership-and-claim-boundaries) explains stale-file handling, full-manifest claims and exact-commit attestation. [Agent validation](../agents/generated-artifacts.md) owns the focused commands; [integrated rendering](../agents/integrated.md) owns all four renderer entry points. Use the declared requirements environment; no parent-directory activation script or absent orchestration workflow is required by a public checkout.

Read the actual selected roots before import: the current hosted lifecycle workers phase is automated, while the selected Portworx provider Application is manual. Hash/render success is not operator, hardware, storage, registry or traffic qualification. Source transport can commit/push; it is not a local validation command.
