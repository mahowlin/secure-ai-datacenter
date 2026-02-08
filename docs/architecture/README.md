# Architecture Documentation

Detailed architecture documentation for the SAIF Platform.

## Documents

| Document | Purpose |
|----------|---------|
| [DAY0_DAY1_DAY2.md](DAY0_DAY1_DAY2.md) | Deployment phases and responsibilities |
| [AI_POD_VS_SAIF.md](AI_POD_VS_SAIF.md) | Layer distinction (base vs extension) |

## Overview

See [../ARCHITECTURE.md](../ARCHITECTURE.md) for the high-level platform overview.

## Key Concepts

### Two Layers

1. **AI Pod** - Base infrastructure (network, server, Kubernetes)
2. **Secure AI Factory** - Security + observability extension

### Three Days

1. **Day 0** - Infrastructure preparation (UCS, images)
2. **Day 1** - Cluster creation (OpenShift, Cilium, ArgoCD bootstrap)
3. **Day 2** - GitOps management (all operators and workloads)

### Principle: Minimal Day 1

Day 1 does the absolute minimum to hand off to GitOps:
- OpenShift installation
- Cilium CNI (Day 1 manifests)
- Bootstrap IDMS
- ArgoCD bootstrap

Everything else is Day 2 GitOps.
