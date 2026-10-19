# Diagram sources and owners

Each diagram is drawn by the Python file beside the SVG. The page in the middle column is where that picture is explained. This index only records who generates the figure and which page owns the explanation.

| Source and render | Owning page | What it shows |
|---|---|---|
| `platform-views.py` / `platform-system.svg` | [`README.md`](../../README.md) | Simple runtime overview: ACI, bare metal, OpenShift, Isovalent, storage and workloads. |
| `platform-views.py` / `network-contract.svg` | [`FABRIC_NETWORK.md`](../architectures/FABRIC_NETWORK.md), Fabric network guide | Management versus tenant attachment, including host and ACI ownership. |
| `platform-views.py` / `install-lifecycle.svg` | compact install guide | Services → input/ISO → site boot → installed cluster → optional GitOps. |
| `platform-views.py` / `registry-chain.svg` | registry guide | Bootstrap dependencies, producer/consumer and registry/operator roles. |
| `platform-views.py` / `gitops-operation.svg` | GitOps `docs/ARCHITECTURE.md` | Generated values, reusable resources, two API destinations. |
| `platform-views.py` / `platform-integration.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md#platform-integration-reference) | Detailed platform integration, retained separately from the overview. |
| `platform-views.py` / `compute-overview.svg` | Compute README | UCS provisioning and ISO installation converge on servers, then OpenShift and GitOps. |
| `platform-views.py` / `airgap-overview.svg` | Airgap README | Vendor images, site registry, management-hosted mirror operator/cache and hosted workers. |
| `platform-views.py` / `gitops-overview.svg` | GitOps README | Generated selections and reusable resources reconcile to management and tenant API objects. |
| `platform-views.py` / `fabric-overview.svg` | Fabric README | YAML and Terraform through APIC to separate management/tenant server attachments. |
| `repository-handoffs.py` / `repository-handoffs.svg` | [`README.md`](../../README.md) | Site inputs fan out into separate install/application artifacts; optional image supply and runtime reconciliation remain distinct. |
| `composition-flow.py` / `composition-flow.svg` | [`COMPOSITION_MODEL.md`](../COMPOSITION_MODEL.md) | One composition fans out into the target repositories. |
| `input-resolution.py` / `input-resolution.svg` | [`schemas/README.md`](../../schemas/README.md) | Example inputs and the versioned schema take different paths. Only the example inputs are rendered. |
| `sno-realization.py` / `sno-realization.svg` | [`COMPOSITION_MODEL.md`](../COMPOSITION_MODEL.md) | Rendered install inputs become an Agent ISO, reach one server and realize SNO. |
| `compact-management-realization.py` / `compact-management-realization.svg` | [`COMPOSITION_MODEL.md`](../COMPOSITION_MODEL.md) | Day 0, Day 1, Day 2 and hosted-cluster layers of the compact composition. |
| `management-tenant-architecture.py` / `management-tenant-architecture.svg` | [`COMPOSITION_MODEL.md`](../COMPOSITION_MODEL.md) | Management-cluster areas and APIs that exist before a tenant is created. |
| `hosted-tenant-realization.py` / `hosted-tenant-realization.svg` | [`COMPOSITION_MODEL.md`](../COMPOSITION_MODEL.md) | Control-plane components, KubeVirt objects and the guest boundary of one hosted tenant. |
| `nested-cluster-l2bridge.py` / `nested-cluster-l2bridge.svg` | [`HCP_MULTUS_DHCP.md`](../architectures/HCP_MULTUS_DHCP.md) | Management control-plane pods, Multus workers, the KubeVirt DHCP service and direct worker BGP. |
| `hcp-multus-dhcp-boot-control.py` / `hcp-multus-dhcp-boot-control.svg` | [`HCP_MULTUS_DHCP.md`](../architectures/HCP_MULTUS_DHCP.md) | L2, DHCP, Ignition and API dependencies that precede worker readiness. |
| `hcp-konnectivity-direction.py` / `hcp-konnectivity-direction.svg` | [`HCP_MULTUS_DHCP.md`](../architectures/HCP_MULTUS_DHCP.md) | DNS, NTP, registry, Ignition and API paths before Node registration, and the worker-established Konnectivity channel for later reverse requests. |
| `hcp-management-service-bgp.py` / `hcp-management-service-bgp.svg` | [`HCP_MULTUS_DHCP.md`](../architectures/HCP_MULTUS_DHCP.md) | Management Cilium publishes hosted control endpoints independently of guest worker BGP. |
| `hcp-direct-worker-service-bgp.py` / `hcp-direct-worker-service-bgp.svg` | [`HCP_MULTUS_DHCP.md`](../architectures/HCP_MULTUS_DHCP.md) | Guest Services, LB-IPAM, Cilium BGP, ACI and backends for direct worker ingress. |
| `hcp-ilb-object-control.py` / `hcp-ilb-object-control.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md) | Isovalent Load Balancer reaches the hosted kube-apiserver Service and turns remote Service and Node state into management load-balancer state. |
| `foundation-integrations.py` / `foundation-integrations.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md) | Fabric, compute, host networking and hosted workers meet. |
| `hosted-cluster-integrations.py` / `hosted-cluster-integrations.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md) | Local management reconciliation, tenant API registration and remote guest reconciliation differ. |
| `delivery-integrations.py` / `delivery-integrations.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md) | Disconnected software reaches management and guest clusters. |
| `observability-integrations.py` / `observability-integrations.svg` | [`INTEGRATIONS.md`](../INTEGRATIONS.md) | Cluster identity is preserved across Hubble and Timescape. |

A source and SVG pair that is not in this table is not placed on a page yet. Keep the pair together until its workstream adds it to a page or removes it.

`diagram_theme.py` is the shared renderer. From the repository root:

See [diagram regeneration](../../AGENTS.md#diagram-updates) for the exact command.

Read the source and the SVG together, at normal Markdown width and at 1920×1080. Labels should be readable, endpoints correct, and lines should not clip or collide. Crossings should be few. Edges should mean something. A planned path has to look different from a path this example already renders.
