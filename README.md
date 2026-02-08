# SAIF Platform

Top-level orchestration repository for the **Secure AI Factory (SAIF) Platform**.

## What is SAIF Platform?

SAIF Platform 1.0 is a production-grade, fully automated infrastructure for running AI workloads on Cisco UCS-X hardware with enterprise security (Cilium, Tetragon) and observability (Splunk, Hubble).

```
+-------------------------------------------------------------------+
|                      SAIF PLATFORM 1.0                            |
|                                                                   |
|  +-------------------------------------------------------------+ |
|  |  SECURE AI FACTORY (Extension)                               | |
|  |  Observability + Security + AI Workloads                     | |
|  |  Repos: saif-gitops                                           | |
|  +-------------------------------------------------------------+ |
|                                                                   |
|  +-------------------------------------------------------------+ |
|  |  AI POD (Base Infrastructure)                                | |
|  |  Network + Server + Kubernetes                               | |
|  |  Repos: saif-ai-pod, saif-sys-admin                          | |
|  +-------------------------------------------------------------+ |
|                                                                   |
|  Orchestration: saif-platform (this repo)                         |
+-------------------------------------------------------------------+
```

## Repository Bundle

SAIF Platform 1.0 is a versioned bundle of repositories that work together:

| Repository | Role | Description |
|------------|------|-------------|
| **[saif-platform](https://github.com/mahowlin/saif-platform)** | Orchestration | Release coordination, SBOM, cross-repo workflows (this repo) |
| **[saif-ai-pod](https://github.com/mahowlin/saif-ai-pod)** | Day 0/Day 1 | UCS server profiles, OpenShift deployment, post-install |
| **[saif-gitops](https://github.com/mahowlin/saif-gitops)** | Day 2 | ArgoCD app-of-apps, operators, workload configurations |
| **[saif-sys-admin](https://github.com/mahowlin/saif-sys-admin)** | Infrastructure | Image mirroring, runner VM, user management |
| **[saif-splunk-dashboard](https://github.com/mahowlin/saif-splunk-dashboard)** | Observability | Splunk dashboard specifications and chart definitions |

## Key Files

| File | Purpose |
|------|---------|
| `platform-release.yaml` | SBOM - all component versions and cluster inventory |
| `repo-map.yaml` | Repository references and cross-repo dependencies |
| `.github/workflows/` | Orchestration workflows |

## Getting Started

### Check Current Versions

```bash
cat platform-release.yaml
```

### Deploy Full Platform

```bash
gh workflow run orchestrate-platform.yaml \
  -f clusters="ai-pod-1" \
  -f mode=full-deploy
```

## Documentation

- [Architecture](docs/ARCHITECTURE.md) - Platform overview and diagrams
- [Repository Map](docs/REPOSITORY_MAP.md) - All repos explained
- [Day 0/1/2 Model](docs/architecture/DAY0_DAY1_DAY2.md) - Deployment phases
- [Troubleshooting](docs/mop/TROUBLESHOOTING.md) - Common issues and fixes

## License

This project is licensed under the Cisco Sample Code License, Version 1.1. See [LICENSE](LICENSE) for details.
