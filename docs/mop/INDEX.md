# SAIF Platform - MOP Index

Unified index of all Method of Procedure (MOP) documentation across the platform.

---

## Quick Reference

| Need to... | Repository | Procedure |
|------------|------------|-----------|
| Deploy a new cluster | saif-ai-pod | [04-openshift-installation.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/04-openshift-installation.md) |
| Add an application | saif-gitops | [01-add-application.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/01-add-application.md) |
| Add an operator | saif-gitops | [02-add-operator.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/02-add-operator.md) |
| Mirror images | saif-sys-admin | [01-image-mirroring.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/01-image-mirroring.md) |
| Create cluster users | saif-sys-admin | [02-user-management.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/02-user-management.md) |
| Manage secrets | saif-gitops | [03-sealed-secrets.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/03-sealed-secrets.md) |
| Fix runner issues | saif-sys-admin | [03-runner-vm.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/03-runner-vm.md) |

---

## By Repository

### saif-ai-pod (Day 0/1 - Infrastructure)

Base cluster deployment procedures.

| Procedure | Description | When to Use |
|-----------|-------------|-------------|
| [00-prerequisites.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/00-prerequisites.md) | Infrastructure validation | Before first deployment |
| [01-runner-vm-bootstrap.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/01-runner-vm-bootstrap.md) | Deploy runner VM | Initial setup |
| [02-image-mirroring.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/02-image-mirroring.md) | Mirror container images | Before cluster deployment |
| [03-intersight-configuration.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/03-intersight-configuration.md) | UCS server profiles | Day 0 hardware setup |
| [04-openshift-installation.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/04-openshift-installation.md) | OpenShift + Cilium install | Day 1 cluster creation |
| [troubleshooting.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/troubleshooting.md) | Common issues | When errors occur |
| [verification-procedures.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/verification-procedures.md) | Health checks | After each phase |

### saif-gitops (Day 2 - Workloads)

GitOps-managed workload procedures.

| Procedure | Description | When to Use |
|-----------|-------------|-------------|
| [01-add-application.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/01-add-application.md) | Deploy new applications | Adding workloads |
| [02-add-operator.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/02-add-operator.md) | Add OLM operators | Installing operators |
| [03-sealed-secrets.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/03-sealed-secrets.md) | GitOps secrets management | Creating/updating secrets |
| [04-troubleshooting.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/04-troubleshooting.md) | ArgoCD issues | When sync fails |

### saif-sys-admin (Infrastructure Services)

Platform-wide infrastructure procedures.

| Procedure | Description | When to Use |
|-----------|-------------|-------------|
| [01-image-mirroring.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/01-image-mirroring.md) | Container image mirroring | Adding operators, updates |
| [02-user-management.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/02-user-management.md) | Cluster user provisioning | New user access |
| [03-runner-vm.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/03-runner-vm.md) | Runner VM services | Service issues, upgrades |
| [04-troubleshooting.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/04-troubleshooting.md) | Infrastructure issues | When things break |

---

## By Task Type

### Deployment Operations

| Task | Phase | Procedure |
|------|-------|-----------|
| Deploy new cluster | Day 0/1 | ai-pod: [04-openshift-installation.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/04-openshift-installation.md) |
| Configure UCS hardware | Day 0 | ai-pod: [03-intersight-configuration.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/03-intersight-configuration.md) |
| Add application to cluster | Day 2 | gitops: [01-add-application.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/01-add-application.md) |
| Install operator | Day 2 | gitops: [02-add-operator.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/02-add-operator.md) |

### Infrastructure Operations

| Task | Procedure |
|------|-----------|
| Mirror new images | sys-admin: [01-image-mirroring.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/01-image-mirroring.md) |
| Create cluster user | sys-admin: [02-user-management.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/02-user-management.md) |
| Fix runner VM | sys-admin: [03-runner-vm.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/03-runner-vm.md) |
| Bootstrap runner VM | ai-pod: [01-runner-vm-bootstrap.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/01-runner-vm-bootstrap.md) |

### Secrets Management

| Task | Procedure |
|------|-----------|
| Create sealed secret | gitops: [03-sealed-secrets.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/03-sealed-secrets.md) |
| Rotate secrets | gitops: [03-sealed-secrets.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/03-sealed-secrets.md) |

---

## Troubleshooting Index

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for a unified troubleshooting guide across all repositories.

---

## Day 0 / Day 1 / Day 2 Reference

| Phase | Repository | Scope |
|-------|------------|-------|
| **Day 0** | saif-ai-pod | UCS hardware profiles |
| **Day 1** | saif-ai-pod | OpenShift install, IDMS bootstrap, ArgoCD bootstrap |
| **Day 2** | saif-gitops | ALL operators and workloads |

For architectural details, see [Day 0/1/2 Architecture](../architecture/DAY0_DAY1_DAY2.md).
