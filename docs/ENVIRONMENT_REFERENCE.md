# Environment Reference

This document defines the environment parameters for a SAIF Platform deployment. Use this as a template when deploying to a new environment.

**Reference Implementation:** Cisco Lab (Cisco Live 2026)

---

## Platform Versions (Fixed)

These versions are tested and validated together. Do not mix versions.

| Component | Version | Notes |
|-----------|---------|-------|
| OpenShift | 4.19.21 | Single Node OpenShift (SNO) |
| RHCOS | 4.19 | Included with OpenShift |
| Cilium Enterprise | 1.18 | Day 1 CNI (deployed with cluster) |
| CLIFE Operator | 1.18.4-cee.1 | Cilium Lifecycle Operator |
| Hubble | 1.18 | Day 2 via ArgoCD |
| Tetragon | 1.2.0 | Day 2 via ArgoCD |
| GPU Operator | v25.10 | Day 2 via ArgoCD |
| ArgoCD | OpenShift GitOps | Day 1 bootstrap |

---

## Environment Parameters (Customize Per Deployment)

### DNS & Domain

| Parameter | Description | Reference Value | Your Value |
|-----------|-------------|--------------|------------|
| Base Domain | Parent DNS zone | `example.com` | |
| Cluster Subdomain | Per-cluster zone pattern | `ai-pod-N.example.com` | |
| Registry FQDN | Container registry hostname | `registry.example.com` | |

### Network

| Parameter | Description | Reference Value | Your Value |
|-----------|-------------|--------------|------------|
| Cluster VLAN | OpenShift node network | 100 | |
| Cluster Subnet | Node IP range | `10.0.1.0/24` | |
| Gateway | Cluster network gateway | `10.0.1.1` | |
| DNS Servers | Upstream DNS | `10.0.0.53, 10.0.0.54` | |

### Cluster Inventory

| Cluster | Node IP | API Endpoint | GPU | Your Values |
|---------|---------|--------------|-----|-------------|
| Cluster 1 | `10.0.1.101` | `api.cluster-1.example.com` | L40S | |
| Cluster 2 | `10.0.1.102` | `api.cluster-2.example.com` | L40S | |
| Cluster 3 | `10.0.1.103` | `api.cluster-3.example.com` | - | |
| Cluster 4 | `10.0.1.104` | `api.cluster-4.example.com` | - | |

### LoadBalancer IP Pools

Cilium L2Announcement assigns IPs from these pools. Size pools based on expected services.

| Cluster | Pool Range | Size | Your Values |
|---------|------------|------|-------------|
| Cluster 1 | `10.0.1.80 - 10.0.1.89` | 10 | |
| Cluster 2 | `10.0.1.90 - 10.0.1.99` | 10 | |
| Cluster 3 | `10.0.1.100 - 10.0.1.109` | 10 | |
| Cluster 4 | `10.0.1.110 - 10.0.1.119` | 10 | |

### Infrastructure Services

| Service | Host | Port | Reference Value | Your Value |
|---------|------|------|--------------|------------|
| Container Registry | Runner VM | 5000 | `10.0.0.10:5000` | |
| ISO Web Server | Runner VM | 80 | `10.0.0.10:80` | |
| Terraform State | Runner VM | 8081 | `10.0.0.10:8081` | |
| GitHub Runners | Runner VM | - | `10.0.0.10` | |

> **Note:** ISO Web Server must use port 80. Intersight vMedia strips colons from URLs, breaking non-standard ports.

---

## Finding Dynamic Resources

LoadBalancer IPs are assigned dynamically. Use these commands to find current values:

```bash
# Find all LoadBalancer services
oc get svc -A -o wide | grep LoadBalancer

# Find specific service IP
oc get svc -n <namespace> <service-name> -o jsonpath='{.status.loadBalancer.ingress[0].ip}'

# Example:
oc get svc -n hubble-timescape hubble-timescape-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
```

---

## External Dependencies

| Service | Purpose | Required |
|---------|---------|----------|
| Red Hat Console | Pull secret, subscriptions | Yes |
| Intersight | UCS server management | Yes |
| GitHub | CI/CD workflows, GitOps | Yes |
| Isovalent | Cilium Enterprise license | Yes |
| Splunk Cloud | Observability (optional) | No |
| NVIDIA NGC | NIM models (optional) | No |

---

## Customization Checklist

When deploying to a new environment:

- [ ] Update DNS zone and create required records (api, api-int, *.apps per cluster)
- [ ] Allocate VLAN and IP ranges for cluster network
- [ ] Allocate LoadBalancer IP pools (non-overlapping with node IPs)
- [ ] Deploy runner VM with registry, web server, GitHub runners
- [ ] Configure Intersight with UCS domain and server profiles
- [ ] Update `cluster-mappings.yaml` with new cluster definitions
- [ ] Update `mirror/images-list.yaml` with registry URL
- [ ] Create GitHub secrets for credentials

---

## Related Documentation

- [Repository Map](REPOSITORY_MAP.md) - All repositories and their roles
- [Day 0/1/2 Architecture](architecture/DAY0_DAY1_DAY2.md) - Deployment phases
- [saif-ai-pod TOPOLOGY.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/architecture/TOPOLOGY.md) - Reference infrastructure details
