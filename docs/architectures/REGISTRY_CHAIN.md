# Image delivery: external sources, site registry and hosted cache

Image delivery crosses two bootstrap boundaries. OpenShift needs its images before it exists; hosted workers can use a management-cluster cache only after the management cluster and cache are ready.

[![External sources feed a site registry before bare-metal bootstrap; after management OpenShift and storage exist, a separate registry can serve hosted workers. Optional mirror reconciliation writes selected content, while workers pull through their own TLS/auth and mirror mapping.](../diagrams/registry-chain.svg)](../diagrams/registry-chain.svg)

For a connected installation, reachable upstream services may replace the site mirror. A selected site registry must be available before installation. The optional in-cluster mirror operator cannot supply installation images or bootstrap itself through an unavailable cache.

## Each stage has an owner

| Stage | Configuration owner | Execution and output |
|---|---|---|
| Release and site selection | Datacenter `platform-release.yaml`, cluster input and shared defaults | Resolve exact source images, destination, trust and selected delivery scopes |
| External → site registry | [Airgap image inventory and procedure](https://github.com/mahowlin/secure-ai-airgap/blob/main/docs/mop/01-image-mirroring.md) | Authorized mirroring, verified contents and integrity-checked policy receipts |
| Site sources → first boot | [Compute installation](https://github.com/mahowlin/secure-ai-compute/blob/main/docs/mop/04-openshift-installation.md) | Private archive staging, Agent ISO, physical boot and readiness checks |
| In-cluster registry hosting | Datacenter LocalRegistry input → GitOps registry overlay | Distribution/Nginx, storage, Services and access policy |
| Optional cache population | [Community mirror-operator contract](MIRROR_OPERATOR.md) | Manual installation/activation, selected image copies and separate runtime qualification |
| Hosted-worker consumption | Per-cluster attachment, DNS, route, trust and pull-secret inputs | Verify actual pulls for each attachment model |

Airgap can provision the external runner/registry host, or the site can supply existing services. [Project inputs](https://github.com/mahowlin/secure-ai-airgap/blob/main/docs/PROJECT_INPUTS.md) distinguish canonical shared values from runner-local inventory and credentials. Actual host rollouts use the owning pipeline.

## Keep the boundaries intact

- Edit shared registry/release facts in Datacenter and regenerate downstream metadata; consumers reject conflicting overrides.
- Preserve raw generated artifacts. Private credentials, trusted CA files and validated image-policy receipts are separate execution inputs.
- A registry Service or Ready operator does not establish image contents, usable credentials or recovery. Check normal-TLS pulls and actual consumers.
- PrivNet and Multus workers use different attachment and return paths. Multus does not acquire a PrivNet PIP; verify each selected model.

For cache credential/trust rotation, use [Airgap's maintenance procedure](https://github.com/mahowlin/secure-ai-airgap/blob/main/docs/mop/06-hcp-local-registry-trust.md). For the pinned operator's input/export, activation and recovery checklist, use [the detailed operator reference](MIRROR_OPERATOR.md).

## How hosted workers reach the management registry

The compact example declares [external pull exposure](../registry-external-pull.md) for optional cache preparation, using the existing management BGP domain and a distinct synthetic VIP198.18.13.20. Generated Service/pool/advertisement prepare this frontend. The default `cacheProduction.mode: preceding-registry` keeps hosted consumers on the preceding registry until a reviewed production inventory and current readiness evidence are selected. A guest cannot be assumed to resolve or route to an internal `.svc.cluster.local` listener. The runtime contract names the external DNS records, certificate, pull Secret and additive CA bundle required from their owners.

Management-hosted control-plane pods can use their management pod-network/service path; Multus workers use their tenant VLAN, ACI routing and this frontend. Registry hosting, content seeding, reader exposure and the optional mirror operator remain distinct. The declared frontend is source preparation, not proof of a live consumer pull.

Selected cache population now uses the [source-owned lifecycle procedure](HCP_CACHE_LIFECYCLE.md): versioned inventory and provenance, the existing Airgap sync/promotion path, distinct operator production settings, and a current read-side barrier on control-plane/worker activation. The operator-bootstrap workflow and bounded trial remain separate. This supplies the reusable mechanism; customers still supply/review their exact release/product/disk/add-on closure and qualify real consumers.

## Configuration and artifact owners

Platform inputs select versions, site policy and integration. Airgap prepares verified upstream artifacts and registry content. Platform renders the selected resources; Compute and Fabric consume their generated inputs, and GitOps owns application reconciliation. Private runtime enrichment stays with each execution owner. [Implementation checks and transport boundaries](../../agents/generated-artifacts.md) belong to the agent procedure.
