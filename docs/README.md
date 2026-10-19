# Platform documentation

Start with the [standalone installation path](architectures/COMPACT_BAREMETAL_INSTALL.md) for a three-server cluster, or use these references for the integrated composition. The composition model shows how one set of inputs becomes fabric, compute and GitOps. The Multus guide follows one hosted worker from the VLAN to the Service advertisement. The validation notes say what to look for.

## Platform guide

| Title | Purpose |
|---|---|
| [Compact bare-metal install](architectures/COMPACT_BAREMETAL_INSTALL.md) | Start a three-server cluster with declared inventory and trace the Compute handoff. |
| [Installation input reference](architectures/INSTALL_INPUT_REFERENCE.md) | Check supported editable fields, delivery/trust policy and private runtime boundaries. |
| [Fabric and host network](architectures/FABRIC_NETWORK.md) | Join physical/bond interfaces, ACI access and tenant BGP before installation. |
| [Image-delivery chain](architectures/REGISTRY_CHAIN.md) | Separate upstream sources, pre-install site registry and optional management-hosted cache. |
| [Hosted-cache lifecycle](architectures/HCP_CACHE_LIFECYCLE.md) | Follow selected inventory, production readiness and control-plane/worker activation ordering. |
| [Optional mirror operator](architectures/MIRROR_OPERATOR.md) | Review immutable inputs, manual OLM installation and outstanding activation qualification. |
| [Composition model](COMPOSITION_MODEL.md) | Follow one input through to ACI, UCS, OpenShift and GitOps, and compare the single-node, compact and hosted shapes. |
| [HCP with Multus, DHCP and direct BGP](architectures/HCP_MULTUS_DHCP.md) | See how a hosted worker gets a tenant-VLAN address and how guest Cilium advertises a Service. |
| [Integration catalogue](INTEGRATIONS.md) | See where fabric, compute, cluster lifecycle, tenant networking, software supply, storage and observability meet. |
| [Diagram index](diagrams/README.md) | Find the page that draws each diagram. |
| [Validation notes](validations/README.md) | See what the example can show, then run `tests/`. |

## Focused validation

These pages say what a check has to observe. They are not a log of a run.

| Workstream | Purpose |
|---|---|
| [Multus worker scenarios](validations/multus-worker-scenarios.md) | Run `example-multus-a` from the example inputs and see what each scenario has to show, including who owns the DHCP address. |
| [Direct-worker BGP scaling model](validations/tenant-cluster-scaling.md) | Size the Multus composition when hosted workers peer with the tenant fabric. |
| [Shared storage](validations/portworx-shared-storage.md) | See why hosted workers use shared ReadWriteMany block disks, and what to check. |

The diagrams sit next to the explanation they belong to.

- [Compact bare-metal installation](architectures/COMPACT_BAREMETAL_INSTALL.md) — declared third-party hardware, canonical render, Agent ISO and read-only acceptance checks.
