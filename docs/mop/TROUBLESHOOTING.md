# SAIF Platform - Troubleshooting Index

Unified troubleshooting guide for the SAIF Platform. Find your issue category and follow the links to detailed procedures.

---

## Quick Diagnosis

| Symptom | Likely Cause | Go To |
|---------|--------------|-------|
| Pods stuck in `ImagePullBackOff` | IDMS not applied or image not mirrored | [Image Mirroring Issues](#image-mirroring-issues) |
| ArgoCD sync stuck | Git credentials or resource conflict | [ArgoCD Issues](#argocd-issues) |
| Workflow failing | Runner issues or missing secrets | [GitHub Actions Issues](#github-actions-issues) |
| Cluster API unreachable | VPN/network or certificate issues | [Cluster Connectivity](#cluster-connectivity) |
| Operator not installing | CatalogSource or subscription issues | [OLM/Operator Issues](#olmoperator-issues) |
| GPU not detected | NFD or GPU Operator issues | [GPU Issues](#gpu-issues) |

---

## By Repository

### saif-ai-pod (Infrastructure)

Full troubleshooting guide: [saif-ai-pod/docs/mop/troubleshooting.md](https://github.com/mahowlin/saif-ai-pod/blob/main/docs/mop/troubleshooting.md)

**Common Issues:**
- UCS profile deployment failures
- OpenShift installation timeouts
- vMedia ISO mounting problems
- Cilium CNI startup failures

### saif-gitops (GitOps)

Full troubleshooting guide: [saif-gitops/docs/mop/04-troubleshooting.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/04-troubleshooting.md)

**Common Issues:**
- ArgoCD sync failures
- Application stuck in `OutOfSync`
- SealedSecret decryption failures
- Resource conflicts

### saif-sys-admin (Infrastructure Services)

Full troubleshooting guide: [saif-sys-admin/docs/mop/04-troubleshooting.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/04-troubleshooting.md)

**Common Issues:**
- Image mirroring failures
- Runner VM service issues
- Registry connectivity problems
- Workflow execution failures

---

## Issue Categories

### Image Mirroring Issues

**Symptom:** Pods show `ImagePullBackOff` or `ErrImagePull`

**Quick Checks:**
```bash
# Check if IDMS is applied
oc get imagecontentsourcepolicy
oc get imagedigestmirrorset

# Check registry connectivity
curl -k https://registry.example.com:5000/v2/_catalog

# Check specific image
oc debug node/<node> -- crictl pull <image>
```

**Solutions:**
1. Image not mirrored → Run `sync-images.yaml` in saif-sys-admin
2. IDMS not applied → Re-run post-install in saif-ai-pod
3. Registry down → Check runner VM services

**Detailed Guide:** [sys-admin/01-image-mirroring.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/01-image-mirroring.md)

---

### ArgoCD Issues

**Symptom:** Applications stuck in `OutOfSync` or `Unknown`

**Quick Checks:**
```bash
# Check ArgoCD status
oc get application -n openshift-gitops

# Check ArgoCD pods
oc get pods -n openshift-gitops

# Check repository connectivity
oc logs -n openshift-gitops deployment/openshift-gitops-repo-server | tail -20
```

**Common Causes:**
1. **Git credentials expired** → Recreate `github-repo-creds` secret
2. **Resource conflict** → Check `oc describe application <app>` for errors
3. **Namespace missing** → Verify namespace exists or `CreateNamespace=true`

**Detailed Guide:** [gitops-applications/04-troubleshooting.md](https://github.com/mahowlin/saif-gitops/blob/main/docs/mop/04-troubleshooting.md)

---

### GitHub Actions Issues

**Symptom:** Workflows fail or hang

**Quick Checks:**
```bash
# Check runner status
gh api /orgs/mahowlin/actions/runners --jq '.runners[] | "\(.name): \(.status)"'

# Check runner container logs
ssh ubuntu@10.0.0.10 'sudo docker logs runner-1 --tail 50'
```

**Common Causes:**
1. **Runner restart loop** → Run configure-runner-vm workflow with tags=runner
2. **Missing secrets** → Verify GitHub secrets configured
3. **Disk full** → Check `/data` usage on runner VM

**Detailed Guide:** [sys-admin/03-runner-vm.md](https://github.com/mahowlin/saif-sys-admin/blob/main/docs/mop/03-runner-vm.md)

---

### Cluster Connectivity

**Symptom:** Cannot reach cluster API

**Quick Checks:**
```bash
# Check VPN connection
ping 10.0.0.10

# Check DNS resolution
nslookup api.cluster-1.example.com

# Test API directly
curl -k https://api.cluster-1.example.com:6443/healthz
```

**Common Causes:**
1. **VPN disconnected** → Reconnect to lab VPN
2. **DNS not resolving** → Check DNS server at 10.0.0.53
3. **Cluster down** → Check UCS profile state in Intersight
4. **Certificate expired** → Regenerate kubeconfig

**MCP Tool:** Use `cluster_status_investigate(cluster_name="ai-pod-1")` for detailed diagnostics.

---

### OLM/Operator Issues

**Symptom:** Operator subscription stuck or CSV failed

**Quick Checks:**
```bash
# Check subscriptions
oc get subscriptions -A

# Check CSVs
oc get csv -A | grep -v Succeeded

# Check CatalogSources
oc get catalogsource -n openshift-marketplace
oc get pods -n openshift-marketplace
```

**Common Causes:**
1. **CatalogSource not ready** → IDMS missing, images not mirrored
2. **Bundle unpack timeout** → OLM images not in IDMS
3. **Wrong channel** → Check subscription channel matches available version

**Solution:** Ensure IDMS includes `registry.redhat.io/openshift4` and operator-specific namespaces.

---

### GPU Issues

**Symptom:** GPU not detected or CUDA errors

**Quick Checks:**
```bash
# Check NFD labels
oc get nodes -o json | jq '.items[].metadata.labels | with_entries(select(.key | startswith("feature.node.kubernetes.io/pci")))'

# Check GPU Operator
oc get pods -n nvidia-gpu-operator
oc logs -n nvidia-gpu-operator -l app=nvidia-driver-daemonset

# Check ClusterPolicy
oc get clusterpolicy -o yaml
```

**Common Causes:**
1. **NFD not labeling** → Check NFD pods, may need seccomp fix
2. **Driver pod failing** → Check BIOS settings (SR-IOV, Above 4G decoding)
3. **Wrong BIOS policy** → Verify GPU-enabled BIOS policy on UCS profile

**Related Plans:** NFD_OPERATOR_SECCOMP_FIX, GPU_CLUSTER_DEPLOYMENT

---

## Escalation Path

If troubleshooting guides don't resolve your issue:

1. **Check repo documentation** for known gotchas and workarounds
2. **File an issue** with:
   - Symptom description
   - Commands run and output
   - Relevant logs (pod logs, workflow logs)
   - Cluster name and timestamp

---

## Related Documentation

- [MOP Index](INDEX.md) - All operational procedures
- [Day 0/1/2 Architecture](../architecture/DAY0_DAY1_DAY2.md) - Deployment model
- [Repository Map](../REPOSITORY_MAP.md) - Which repo owns what
