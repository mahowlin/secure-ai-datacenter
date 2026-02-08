# Runbooks & Operational Procedures

This directory contains cross-repo operational guidance for the SAIF Platform.

## Contents

| Document | Description |
|----------|-------------|
| [../mop/INDEX.md](../mop/INDEX.md) | Unified MOP index across all repos |
| [../mop/TROUBLESHOOTING.md](../mop/TROUBLESHOOTING.md) | Cross-repo troubleshooting guide |

## Quick Links

### Common Operations

| Task | Go To |
|------|-------|
| Deploy new cluster | [ai-pod MOP](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/README.md) |
| Add GitOps application | [gitops-applications MOP](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/README.md) |
| Mirror images | [sys-admin MOP](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/README.md) |

### Troubleshooting

| Issue Type | Go To |
|------------|-------|
| Image pull failures | [TROUBLESHOOTING.md#image-mirroring-issues](../mop/TROUBLESHOOTING.md#image-mirroring-issues) |
| ArgoCD sync problems | [TROUBLESHOOTING.md#argocd-issues](../mop/TROUBLESHOOTING.md#argocd-issues) |
| Workflow failures | [TROUBLESHOOTING.md#github-actions-issues](../mop/TROUBLESHOOTING.md#github-actions-issues) |
| Cluster unreachable | [TROUBLESHOOTING.md#cluster-connectivity](../mop/TROUBLESHOOTING.md#cluster-connectivity) |

## Architecture References

- [Day 0/1/2 Model](../architecture/DAY0_DAY1_DAY2.md)
- [Repository Map](../REPOSITORY_MAP.md)
- [Platform Architecture](../ARCHITECTURE.md)
