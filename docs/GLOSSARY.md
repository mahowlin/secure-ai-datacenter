# SAIF Platform Glossary

Definitions of key terms and acronyms used throughout the SAIF Platform documentation.

---

## Platform Terms

| Term | Definition |
|------|------------|
| **SAIF** | Secure AI Factory - The complete platform for running secure AI workloads |
| **SAIF Platform** | The full infrastructure stack: UCS hardware + OpenShift + Cilium + GPU + Observability |
| **AI Pod** | The hardware layer - Cisco UCS-X chassis with compute blades and GPUs |
| **Reference Lab** | Reference implementation environment (Cisco lab) |

### SAIF vs AI Pod

| Layer | Name | Scope |
|-------|------|-------|
| Hardware | AI Pod | UCS chassis, blades, GPUs, network fabric |
| Platform | SAIF Platform | OpenShift + Cilium + ArgoCD + Operators |
| Application | AI Workloads | LLM inference, demo applications |

---

## Deployment Phases

| Term | Definition |
|------|------------|
| **Day 0** | Infrastructure provisioning - UCS server profiles, hardware configuration |
| **Day 1** | Cluster creation - OpenShift installation, CNI deployment, ArgoCD bootstrap |
| **Day 2** | Ongoing operations - All workloads managed via GitOps (ArgoCD) |

### Day 0/1/2 Ownership

| Phase | Repository | Automation |
|-------|------------|------------|
| Day 0 | saif-ai-pod | `ucs-pipeline.yaml` |
| Day 1 | saif-ai-pod | `openshift-pipeline.yaml` |
| Day 2 | saif-gitops | ArgoCD continuous sync |

---

## Networking

| Term | Definition |
|------|------------|
| **CNI** | Container Network Interface - Plugin that provides pod networking |
| **Cilium** | eBPF-based CNI providing networking, security, and observability |
| **Hubble** | Network observability component of Cilium - visualizes traffic flows |
| **Tetragon** | Runtime security component - monitors process execution and file access |
| **L2Announcement** | Cilium feature for LoadBalancer IP advertisement via ARP (bare metal) |
| **VXLAN** | Virtual Extensible LAN - Tunnel protocol for pod-to-pod traffic |

---

## Kubernetes/OpenShift

| Term | Definition |
|------|------------|
| **SNO** | Single Node OpenShift - All control plane and worker on one node |
| **OCP** | OpenShift Container Platform |
| **RHCOS** | Red Hat CoreOS - Immutable OS for OpenShift nodes |
| **OLM** | Operator Lifecycle Manager - Manages operator installation |
| **CSV** | ClusterServiceVersion - Operator metadata and deployment spec |
| **CRD** | Custom Resource Definition - Extends Kubernetes API |

---

## GitOps

| Term | Definition |
|------|------------|
| **GitOps** | Operations model where Git is the source of truth for cluster state |
| **ArgoCD** | GitOps continuous delivery tool for Kubernetes |
| **App-of-Apps** | ArgoCD pattern where one Application manages other Applications |
| **Sync Wave** | ArgoCD ordering mechanism - lower numbers deploy first |
| **SealedSecret** | Encrypted secret that can be safely stored in Git |

---

## Image Management

| Term | Definition |
|------|------------|
| **IDMS** | ImageDigestMirrorSet - Maps external registries to internal mirrors |
| **ITMS** | ImageTagMirrorSet - Tag-based mirror mapping |
| **Air-gap** | Disconnected environment with no direct internet access |
| **Mirror** | Local copy of container images from external registries |
| **CatalogSource** | OLM resource pointing to operator index image |

---

## Hardware (Cisco UCS)

| Term | Definition |
|------|------------|
| **UCS-X** | Cisco's modular compute platform |
| **FI** | Fabric Interconnect - Network switch connecting UCS to data center |
| **IFM** | Intelligent Fabric Module - Network module in UCS-X chassis |
| **IMC** | Integrated Management Controller - Server BMC |
| **vMedia** | Virtual media - Boot from ISO over network |
| **Intersight** | Cisco's cloud-based infrastructure management platform |

---

## GPU / AI

| Term | Definition |
|------|------------|
| **NIM** | NVIDIA Inference Microservices - Containerized AI model serving |
| **NGC** | NVIDIA GPU Cloud - Registry for GPU-optimized containers |
| **DCGM** | Data Center GPU Manager - GPU monitoring and management |
| **L40S** | NVIDIA GPU model used in AI Pods (48GB VRAM) |
| **vLLM** | High-performance LLM inference engine |

---

## Observability

| Term | Definition |
|------|------------|
| **OTEL** | OpenTelemetry - Observability framework for metrics, logs, traces |
| **Splunk** | Observability platform for metrics and dashboards |
| **Vector** | Log and metrics collection agent |
| **Timescape** | Hubble's historical flow storage (ClickHouse-based) |

---

## Acronyms Reference

| Acronym | Expansion |
|---------|-----------|
| ACI | Application Centric Infrastructure (Cisco SDN) |
| API | Application Programming Interface |
| ARP | Address Resolution Protocol |
| BMC | Baseboard Management Controller |
| CIMC | Cisco Integrated Management Controller |
| CLI | Command Line Interface |
| CNP | CiliumNetworkPolicy |
| eBPF | Extended Berkeley Packet Filter |
| EPG | Endpoint Group (ACI term) |
| FQDN | Fully Qualified Domain Name |
| GPU | Graphics Processing Unit |
| gRPC | Google Remote Procedure Call |
| HA | High Availability |
| HTTP | HyperText Transfer Protocol |
| ISO | International Organization for Standardization (disk image format) |
| LB | Load Balancer |
| LLM | Large Language Model |
| MOP | Method of Procedure |
| NFD | Node Feature Discovery |
| OVN | Open Virtual Network |
| PAT | Personal Access Token |
| PR | Pull Request |
| RBAC | Role-Based Access Control |
| SDN | Software Defined Networking |
| SNO | Single Node OpenShift |
| SRE | Site Reliability Engineering |
| SSE | Server-Sent Events |
| TLS | Transport Layer Security |
| UI | User Interface |
| VPN | Virtual Private Network |
| VRAM | Video Random Access Memory |
| YAML | YAML Ain't Markup Language |
