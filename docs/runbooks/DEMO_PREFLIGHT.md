# Demo Pre-Flight Checklist

Use this checklist before any demo or presentation to verify the SAIF Platform is operational.

**Target:** Cisco Live 2026 TECCOM-2788

---

## Quick Health Check (5 minutes)

Run these commands to verify core components:

```bash
# 1. Set cluster context (ai-pod-2 for demo)
export KUBECONFIG=~/.kube/ai-pod-2

# 2. Check cluster health
oc get nodes
oc get clusterversion

# 3. Check core namespaces
oc get pods -n cilium | grep -v Running
oc get pods -n nim-llm | grep -v Running
oc get pods -n hubble-timescape | grep -v Running
```

**Expected:** All commands return healthy status, no pods in error state.

---

## Component Checklist

### 1. Cluster Infrastructure

| Check | Command | Expected |
|-------|---------|----------|
| Nodes ready | `oc get nodes` | All nodes `Ready` |
| Cluster version | `oc get clusterversion` | `Available=True` |
| ArgoCD healthy | `oc get pods -n openshift-gitops` | All pods `Running` |

### 2. Cilium CNI

| Check | Command | Expected |
|-------|---------|----------|
| Cilium agents | `oc get pods -n cilium -l k8s-app=cilium` | All `Running` |
| Hubble relay | `oc get pods -n cilium -l k8s-app=hubble-relay` | `Running` |
| Cilium status | `oc exec -n cilium ds/cilium -- cilium status` | All green |

### 3. NIM / LLM

| Check | Command | Expected |
|-------|---------|----------|
| NIM pod running | `oc get pods -n nim-llm` | `Running` |
| GPU allocated | `oc describe pod -n nim-llm \| grep nvidia` | GPU assigned |
| Model loaded | `curl -s http://nim-llm.nim-llm.svc:8000/v1/models` | Model listed |

### 4. Hubble Timescape

| Check | Command | Expected |
|-------|---------|----------|
| Pods running | `oc get pods -n hubble-timescape` | All `Running` |
| Flows ingesting | `oc logs -n hubble-timescape deploy/hubble-timescape-ingester --tail=5` | Recent timestamps |
| UI has IP | `oc get svc -n hubble-timescape hubble-timescape-ui` | `EXTERNAL-IP` assigned |

**Find Timescape URL:**
```bash
TS_IP=$(oc get svc -n hubble-timescape hubble-timescape-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
echo "Hubble Timescape: http://$TS_IP"
```

---

## Demo URLs Summary

Run this to get all demo URLs:

```bash
echo "=== Demo URLs ==="
echo "Hubble Timescape: http://$(oc get svc -n hubble-timescape hubble-timescape-ui -o jsonpath='{.status.loadBalancer.ingress[0].ip}')"
echo "Splunk Dashboard: https://app.us1.signalfx.com"
```

---

## Common Issues & Recovery

### NIM Model Not Loading

```bash
# Check GPU availability
oc describe node | grep -A5 nvidia

# Check pod events
oc describe pod -n nim-llm

# Common fix: restart pod
oc delete pod -n nim-llm -l app.kubernetes.io/part-of=nim-service
```

### No Flows in Timescape

```bash
# Check Vector exporter
oc logs -n hubble-timescape -l app.kubernetes.io/name=vector --tail=20

# Check ingester
oc logs -n hubble-timescape deploy/hubble-timescape-ingester --tail=20

# Verify Hubble is generating flows
oc exec -n cilium ds/cilium -- hubble observe --last 10
```

### LoadBalancer IP Not Assigned

```bash
# Check Cilium L2 announcement
oc get ciliuml2announcementpolicy
oc get ciliumbgppeeringpolicy

# Check IP pool
oc get ciliumloadbalancerippool
```

---

## 30-Minute Pre-Demo Routine

| Time | Action |
|------|--------|
| T-30 | Run quick health check |
| T-25 | Verify all demo URLs accessible from presenter laptop |
| T-15 | Verify NIM inference endpoint responds |
| T-10 | Open Hubble Timescape, verify flows populating |
| T-5 | Open Splunk dashboard, verify metrics flowing |
| T-0 | Demo ready |

---

## Fallback Plan

If critical component fails during demo:

| Component | Fallback |
|-----------|----------|
| NIM/LLM | Use cached output, explain LLM is loading |
| Hubble Timescape | Show static topology diagram |

---

## Contact

For urgent issues during Cisco Live:
- Platform team Slack channel
- On-site support contact
