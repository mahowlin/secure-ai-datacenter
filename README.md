# Cisco Secure AI Datacenter

**Input YAML → validate and render → generated configuration.** This repository is the configuration composer for Cisco Secure AI Datacenter. Shared site inputs describe hardware, networks, software releases and services; the resolver and renderers produce repository-shaped files under [generated/repos/](generated/repos/) for the downstream Fabric, Compute and GitOps repositories.

A composition can describe a standalone bare-metal cluster or **nested Kubernetes (“Kubernetes in Kubernetes”)**: a management OpenShift platform hosts tenant OpenShift control planes as pods and their worker nodes as KubeVirt VMs, alongside Isovalent networking and observability and Portworx storage.

[![Datacenter composes configuration for Cisco ACI and a UCS/OpenShift management platform. Tenant OpenShift clusters consist of HyperShift control-plane pods and KubeVirt worker VMs; Fabric, Compute and GitOps execute the configuration.](docs/diagrams/platform-system.svg)](docs/diagrams/platform-system.svg)

## Technology vendors and projects

The composition brings together the following vendor technologies. Exact versions and enabled capabilities come from [platform-release.yaml](platform-release.yaml), the [add-on catalogue](data/capabilities/addons.yaml) and the selected cluster inputs.

| Vendor | Technology and role |
|---|---|
| **Cisco** | UCS servers, Intersight provisioning, ACI fabric and APIC network automation |
| **Red Hat** | OpenShift Container Platform, OpenShift Virtualization, multicluster engine and OpenShift GitOps; Ansible for service-host configuration |
| **Isovalent (Cisco)** | Isovalent Enterprise Platform: Cilium networking, Hubble/Timescape observability and selected Load Balancer/Egress Gateway integrations |
| **Pure Storage / Portworx** | Distributed persistent storage and CSI volumes, including KubeVirt worker root disks |
| **NVIDIA** | Optional GPU Operator and NIM inference workloads; NVIDIA add-ons are disabled in the current management example |

Upstream building blocks include **HyperShift**, **KubeVirt**, **Argo CD**, **Cilium/Hubble** and the **NAC-ACI module from netascode**. The [integration catalogue](docs/INTEGRATIONS.md) explains their joins; the [repository handoff map](repo-map.yaml) identifies where each generated configuration is consumed.

## Work through the repo

**Input YAML → resolve, validate and render → files in this checkout**

1. **Choose an example and edit its inputs.** Start with the examples below. Define the cluster and hosts in [data/clusters/](data/clusters/), networks in [data/networks/](data/networks/), common services in [data/defaults/](data/defaults/), and software in [platform-release.yaml](platform-release.yaml).
2. **Validate and render the selection.** Use [AGENTS.md](AGENTS.md) to prepare the toolchain, then follow the [standalone procedure](docs/architectures/COMPACT_BAREMETAL_INSTALL.md) or [integrated renderer sequence](agents/integrated.md). The [scripts/](scripts/) resolve references, check the selected inputs and generate the target configuration.
3. **Inspect what was produced.** Open [generated/repos/](generated/repos/) to see each destination's actual files. Check their contents and [artifact hashes](agents/generated-artifacts.md) before delivering them.

These three steps are this repository’s contained purpose. Continue with the [downstream handoff](#place-generated-files-in-downstream-repositories) to put the reviewed files into the repositories that execute them. The sections below expand the input and rendering steps. The [detailed composition model](docs/COMPOSITION_MODEL.md) is a reference for tracing how selected inputs combine and why a particular output was generated.

## Choose your starting inputs

A cluster input selects named networks, a release and platform capabilities. Pick the maintained input closest to your deployment:

| Composition | Starting inputs | What it assembles |
|---|---|---|
| Standalone compact OpenShift | [Minimal three-server example](examples/standalone/) | Bare-metal OpenShift with Isovalent CNI, using your existing network and installation services |
| Integrated management platform | [Management cluster](data/clusters/example-management/cluster.yaml) | Compact OpenShift, shared storage, virtualization, MCE/HyperShift, management services and selected network integrations |
| Hosted tenant clusters | [Tenant definitions](data/clusters/example-management/hcpclusters/) beneath the management cluster | Tenant control-plane pods, KubeVirt worker pools and their selected storage, network and service configuration |
| Single-node OpenShift | [SNO cluster](data/clusters/example-sno/cluster.yaml) | A one-server composition with its selected installation and hardware inputs |

ACI, UCS, image mirroring and the hosted image cache are selected integrations. The examples describe intended configuration; site-specific inputs and runtime prerequisites still need to be supplied.

## Define the shared inputs

Keep site choices in maintained inputs so installation, networking and application configuration use the same definitions.

| Input | Controls |
|---|---|
| [Cluster inputs](data/clusters/) | Cluster identity, host inventory, parent/tenant relationships and enabled capabilities |
| [Network catalogue](data/networks/example-federal.yaml) | VLANs, prefixes, gateways, BGP peers, VIPs and address pools |
| [Shared defaults](data/defaults/example.yaml) | DNS, time, registries, GitOps destinations and common policy |
| [Release selection](platform-release.yaml) | Compatible software choices and exact image/archive references |
| [Templates](templates/) and [schemas](schemas/) | Generated file shapes and input constraints |

Use the [installation input reference](docs/architectures/INSTALL_INPUT_REFERENCE.md) for the concrete fields. The [composition model](docs/COMPOSITION_MODEL.md#trace-one-change) traces a tenant worker-count change through its parent lifecycle policy into the generated NodePool.

## Place generated files in downstream repositories

The generated tree already mirrors each destination repository. **Remove the `generated/repos/<repository>/` prefix and keep the rest of the path.** For example, a generated Compute file under `generated/repos/secure-ai-compute/data/openshift/…` goes under `data/openshift/…` in your Compute checkout.

Copy or import the reviewed generated files into those matching paths, inspect the downstream diff and verify the [artifact manifest and file ownership](agents/generated-artifacts.md). The [repository map](repo-map.yaml) defines the complete handoff. The paired links below show actual generated files here and their corresponding downstream examples:

| Configuration | Generated locally in this repo | Corresponding downstream example |
|---|---|---|
| ACI management attachments | [platform-management.yml](generated/repos/secure-ai-fabric/data/platform-management/platform-management.yml) | **Fabric:** [data/platform-management/platform-management.yml](https://github.com/mahowlin/secure-ai-fabric/blob/main/data/platform-management/platform-management.yml) |
| ACI tenant routing | [tenant-workloads.yml](generated/repos/secure-ai-fabric/data/tenant-workloads/tenant-workloads.yml) | **Fabric:** [data/tenant-workloads/tenant-workloads.yml](https://github.com/mahowlin/secure-ai-fabric/blob/main/data/tenant-workloads/tenant-workloads.yml) |
| UCS connectivity policies | [lan-connectivity.ezi.yaml](generated/repos/secure-ai-compute/data/ucs/lan-connectivity.ezi.yaml) | **Compute:** [data/ucs/lan-connectivity.ezi.yaml](https://github.com/mahowlin/secure-ai-compute/blob/main/data/ucs/lan-connectivity.ezi.yaml) |
| OpenShift installation | [install-config.yaml](generated/repos/secure-ai-compute/data/openshift/example-management/install-config.yaml) | **Compute:** [data/openshift/example-management/install-config.yaml](https://github.com/mahowlin/secure-ai-compute/blob/main/data/openshift/example-management/install-config.yaml) |
| Server/NIC/disk Agent inputs | [agent-config.yaml](generated/repos/secure-ai-compute/data/openshift/example-management/agent-config.yaml) | **Compute:** [data/openshift/example-management/agent-config.yaml](https://github.com/mahowlin/secure-ai-compute/blob/main/data/openshift/example-management/agent-config.yaml) |
| Selected cluster root | [kustomization.yaml](generated/repos/secure-ai-gitops/clusters/example-management/kustomization.yaml) | **GitOps:** [clusters/example-management/kustomization.yaml](https://github.com/mahowlin/secure-ai-gitops/blob/main/clusters/example-management/kustomization.yaml) |
| Nested tenant definition | [hostedcluster.yaml](generated/repos/secure-ai-gitops/apps/hcp/management/hosted-cluster-lifecycle/overlays/example-management/example-multus-a/hostedcluster.yaml) | **GitOps:** [hosted-cluster-lifecycle example](https://github.com/mahowlin/secure-ai-gitops/blob/main/apps/hcp/management/hosted-cluster-lifecycle/overlays/example-management/example-multus-a/hostedcluster.yaml) |

The same layout applies to the remaining generated files. Edit maintained inputs and regenerate to change them; preserve the downstream repository's independently maintained code and private runtime material during import.

[![Datacenter renders files locally; the reviewed files are placed in Fabric, Compute and GitOps, whose pipelines/controllers execute them. Airgap supplies services and images.](docs/diagrams/repository-handoffs.svg)](docs/diagrams/repository-handoffs.svg)

### Execution continues downstream

- **[Fabric](https://github.com/mahowlin/secure-ai-fabric):** the NAC-ACI/Terraform pipeline consumes the imported YAML and applies the selected ACI configuration.
- **[Compute](https://github.com/mahowlin/secure-ai-compute):** UCS/Intersight and OpenShift pipelines consume the hardware/install inputs, provision the selected servers and build/boot their Agent ISO. ISO generation is also available independently.
- **[GitOps](https://github.com/mahowlin/secure-ai-gitops):** Argo CD reads the imported cluster roots and overlays; Kubernetes operators/controllers realize the management and tenant resources.
- **[Airgap](https://github.com/mahowlin/secure-ai-airgap):** supplies the shared services, images and verified external inputs used by those stages.

The [integrated renderer sequence](agents/integrated.md) and [standalone procedure](docs/architectures/COMPACT_BAREMETAL_INSTALL.md) describe how to produce these files. Their destination owners handle transport, credentials, provider state, deployment and runtime health.

## Scope and license

The portable standalone example supports static IPv4, three compact hosts and two management NICs with common names. Release compatibility is explicit input; successful rendering does not qualify a new release. The optional community image-mirror operator [requires additional activation and recovery qualification](docs/architectures/MIRROR_OPERATOR.md) and is not required to install OpenShift.

See [LICENSE](LICENSE) for Cisco Sample Code License 1.1. Product software, entitlement and vendor support remain separate from this reference code.
