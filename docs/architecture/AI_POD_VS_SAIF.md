# AI Pod vs Secure AI Factory

## Overview

The SAIF Platform is composed of two distinct layers:

1. **AI Pod** - Base infrastructure that can exist independently
2. **Secure AI Factory (SAIF)** - Security and observability extension

Understanding this distinction is critical for:
- Knowing which repo to modify for changes
- Understanding what's required vs. optional
- Planning customizations for different environments

## Layer Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                                                                             │
│                    SECURE AI FACTORY (Extension)                            │
│                                                                             │
│   This layer WRAPS the AI Pod with enterprise security and observability.  │
│   It is OPTIONAL - AI Pod can run without it.                              │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                                                                     │  │
│   │  OBSERVABILITY          SECURITY              AI WORKLOADS         │  │
│   │                                                                     │  │
│   │  • Hubble Timescape     • Tetragon            • NIM Operator       │  │
│   │  • Splunk OTEL          • Cilium Network      • Model Inference    │  │
│   │  • Vector               •   Policies                               │  │
│   │  • DCGM GPU Metrics     • Default-Deny                             │  │
│   │                                                                     │  │
│   │  Repos: saif-gitops                                │  │
│   │                                                                     │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│                         AI POD (Base Infrastructure)                        │
│                                                                             │
│   This layer provides GPU-enabled Kubernetes. It is REQUIRED.              │
│   It can exist and function WITHOUT the SAIF extension.                    │
│                                                                             │
│   ┌─────────────────────────────────────────────────────────────────────┐  │
│   │                                                                     │  │
│   │  NETWORK                SERVER                KUBERNETES            │  │
│   │                                                                     │  │
│   │  • ACI Fabric           • UCS-X Blades        • OpenShift 4.19     │  │
│   │  • Cilium CNI           • Intersight          • ArgoCD Bootstrap   │  │
│   │  • VLANs, DNS           • NVIDIA L40S GPU     • Base IDMS          │  │
│   │  • IP Addressing        • NVMe Storage        •                    │  │
│   │                                                                     │  │
│   │  Repos: saif-ai-pod, saif-sys-admin                            │  │
│   │                                                                     │  │
│   └─────────────────────────────────────────────────────────────────────┘  │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

## AI Pod Layer

### What It Provides

The AI Pod is a GPU-enabled Kubernetes cluster on Cisco UCS hardware.

| Component | Technology | Description |
|-----------|------------|-------------|
| **Network** | ACI + Cilium | Physical fabric and pod networking |
| **Server** | UCS-X + Intersight | Hardware lifecycle management |
| **Kubernetes** | OpenShift 4.19 | Container orchestration |
| **CNI** | Cilium Enterprise | Pod networking with eBPF |
| **Storage** | NVMe + LVMS | Local persistent storage |

### Standalone Capability

An AI Pod **can function independently** without the SAIF extension:

```
AI Pod Standalone:
├── OpenShift cluster ✓
├── GPU scheduling ✓ (via GPU Operator - can be Day 2)
├── Basic networking ✓
├── Storage ✓
├── ArgoCD ✓
└── Custom workloads ✓

What's Missing Without SAIF:
├── Deep flow visibility (Hubble Timescape)
├── Security observability (Tetragon)
├── Centralized metrics (Splunk)
└── Network policy automation
```

### Repositories

| Repository | Responsibility |
|------------|----------------|
| `saif-ai-pod` | UCS profiles, OpenShift installation, Cilium Day 1 |
| `saif-sys-admin` | Image mirroring, IDMS generation |

---

## Secure AI Factory Layer

### What It Adds

The SAIF layer wraps the AI Pod with enterprise-grade security and observability.

| Component | Technology | Value |
|-----------|------------|-------|
| **Observability** | Hubble Timescape | Flow storage, historical analysis |
| **Observability** | Splunk OTEL | Centralized metrics and dashboards |
| **Observability** | Vector | Flow forwarding and transformation |
| **Security** | Tetragon | Runtime security, process monitoring |
| **Security** | Cilium Network Policies | Microsegmentation, default-deny |
| **AI** | NIM + LLM model | On-premises LLM inference |

### Why It's an Extension

The SAIF layer is deployed **on top of** the AI Pod via Day 2 GitOps:

```
Day 1 (AI Pod):
└── OpenShift + Cilium + ArgoCD

Day 2 (SAIF Extension):
├── ArgoCD syncs saif-gitops
├── Deploys Hubble Timescape
├── Deploys Tetragon
├── Deploys Splunk OTEL
└── Deploys GPU/NIM Operators
```

### Repositories

| Repository | Responsibility |
|------------|----------------|
| `saif-gitops` | All SAIF operators and configs |
| `saif-splunk-dashboard` | Observability dashboard config |

---

## Comparison Table

| Aspect | AI Pod | Secure AI Factory |
|--------|--------|-------------------|
| **Required?** | Yes | No (extension) |
| **Deployment** | Day 0 + Day 1 | Day 2 (GitOps) |
| **Repos** | saif-ai-pod, saif-sys-admin | saif-gitops |
| **Can Run Alone** | Yes | No (needs AI Pod) |
| **Purpose** | Base infrastructure | Security + observability |
| **Managed By** | Pipelines | ArgoCD |

---

## Use Cases

### AI Pod Only

When you might use just the AI Pod:

- Development/testing environment
- Simple AI workloads without compliance requirements
- Resource-constrained environments
- Proof-of-concept deployments

### AI Pod + SAIF (Full Platform)

When you need the full SAIF Platform:

- Production AI workloads
- Compliance requirements (security observability)
- Network policy enforcement
- Centralized monitoring and dashboards
- AI-assisted security operations

---

## Customization Implications

### Customizing AI Pod Layer

Changes to base infrastructure:
- **Where:** `saif-ai-pod`, `saif-sys-admin`
- **When:** Requires Day 0/Day 1 operations
- **Impact:** May require cluster redeployment

Examples:
- Different hardware (UCS model, GPU type)
- Different network topology (VLANs, IPs)
- Different OpenShift version
- Different Cilium configuration

### Customizing SAIF Layer

Changes to security/observability:
- **Where:** `saif-gitops`
- **When:** Day 2 GitOps (anytime)
- **Impact:** ArgoCD syncs changes automatically

Examples:
- Different Splunk endpoint
- Custom Tetragon policies
- Additional operators
- Modified network policies

---

## SAIF Platform 1.0

The complete **SAIF Platform 1.0** release includes both layers:

```
SAIF Platform 1.0 = AI Pod + Secure AI Factory
                  = saif-ai-pod + saif-sys-admin
                  + saif-gitops
```

See [platform-release.yaml](../../platform-release.yaml) for the complete SBOM with pinned versions.

---

## Related Documentation

- [Platform Architecture](../ARCHITECTURE.md)
- [Day 0/1/2 Architecture](DAY0_DAY1_DAY2.md)
- [Repository Map](../REPOSITORY_MAP.md)
