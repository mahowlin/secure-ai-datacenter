# Composition input

`data/` is the example you edit. Change a file here, render, and read the result under `generated/`. Do not patch the generated copy and expect the next render to keep it.

## Start with a real composition

| Goal | Canonical input | First generated output to inspect |
|---|---|---|
| Change the standalone SNO | [`clusters/example-sno/cluster.yaml`](clusters/example-sno/cluster.yaml) | `generated/repos/secure-ai-compute/data/openshift/example-sno/` after selecting it for render |
| Change the compact HCP management cluster | [`clusters/example-management/cluster.yaml`](clusters/example-management/cluster.yaml) | [`generated/repos/secure-ai-compute/data/openshift/example-management/`](../generated/repos/secure-ai-compute/data/openshift/example-management/) |
| Change the hosted Multus cluster | [`clusters/example-management/hcpclusters/example-multus-a.yaml`](clusters/example-management/hcpclusters/example-multus-a.yaml) | [`generated/.../example-multus-a/`](../generated/repos/secure-ai-gitops/apps/hcp/management/hosted-cluster-lifecycle/overlays/example-management/example-multus-a/) |
| Change the shared hosted cluster | [`clusters/example-management/hcpclusters/example-shared.yaml`](clusters/example-management/hcpclusters/example-shared.yaml) | [`generated/.../example-shared/`](../generated/repos/secure-ai-gitops/apps/hcp/management/hosted-cluster-lifecycle/overlays/example-management/example-shared/) |

The rest of the story is in the [documentation index](../docs/README.md).

## What each input owns

### Cluster files

`clusters/<cluster>/cluster.yaml` owns the management or standalone cluster: its identity, the `sno` or `compact` shape, the selected release, the OpenShift install network and VIP selections, the intended physical nodes and serial identities, the UCS vNIC selections, and the parent-cluster Day 2 features.

`clusters/<parent>/hcpclusters/<tenant>.yaml` owns one hosted cluster: its parent, worker replicas, cluster and service CIDRs, worker-network selections, Cilium BGP behavior, and the load-balancer and egress pools it uses.

`example-multus-a.yaml` selects `tenant-b-workers`. `spec.nodePool.replicas` is `5`. `stagedRollout` allows the checkpoints `0`, `1` and `5`. The resolver joins that file with its parent and the named networks. In the selected `workers` lifecycle phase, the parent `day2GitOps.hostedClusterLifecycle.workerReplicasByCluster` override wins, with parent `workerReplicas` as fallback; the tenant count alone is not the effective selected owner. The current parent selects five workers here and three for `example-shared`. The [composition model](../docs/COMPOSITION_MODEL.md) traces that path.

This example has no publication-stage allowlist. The render writes the whole example.

### Network catalogue

[`networks/example-federal.yaml`](networks/example-federal.yaml) owns the named VLANs, prefixes, ACI identities, BGP endpoints, control-plane VIPs, load balancer pools and egress pools. Select a different network from the cluster file. Edit the catalogue when the shared allocation itself changes.

### Defaults

[`defaults/example.yaml`](defaults/example.yaml) owns shared DNS, NTP, registry, image-mirror, default cluster-network and target-repository conventions. [`defaults/aci.yaml`](defaults/aci.yaml) owns the ACI policy inputs the ACI renderer reads. [`defaults/ucs.yaml`](defaults/ucs.yaml) owns the UCS policy names the generated overlays consume.

Keep those values in the files. Do not bury a deploy-impacting choice in a Python or Jinja fallback.

### Pinned bundles and CRDs

`bundles/` and `crds/` are for an exact build under an active-test lifecycle. A cluster or release selects them. They are not a general install source. Keep the tested rollback path while a pre-release path is still under validation.

## Release and profile model

The cluster files select a combined release from [`../platform-release.yaml`](../platform-release.yaml). [`../schemas/saif-platform-v2.schema.json`](../schemas/saif-platform-v2.schema.json) describes a separate versioned shape. The renderers do not load it, and this example has no fixture tree for it.

That target model splits `releaseRefs.openshift` from `releaseRefs.iep`, bounds management and worker profiles, and requires an authorization gate. The fixtures do not render into this example. Do not edit them as if they were the cluster source.

A resolve can succeed and still refuse a deploy. A legacy adaptation, or a mismatched or revoked active-test gate, must not emit an authorized deploy path.

## Third-party bare-metal inventory

`clusters/example-third-party/cluster.yaml` selects `agentConfig.inventoryMode: declared`. Each node supplies `interfaces` (name and MAC) and one `rootDeviceHints` selector. `managementInterface` supplies bond name, members, mode and options; `rendezvousNode` selects a named host, not array order. The contract is [agent-inventory-v1.schema.json](../schemas/agent-inventory-v1.schema.json).

The machine network is `Generic-Management` in `networks/example-federal.yaml`. DNS, NTP, pod/service CIDRs and mirror paths remain shared inputs in `defaults/example.yaml`; release/image pins remain in `../platform-release.yaml`. `installConfig.cilium` holds explicit Day1 policy, while cluster identity, API endpoint, interface and IPAM pool are derived from those inputs. No UCS inventory is required. No HCP, storage or tenant Day2 resources are selected by this example.

For existing `inventoryMode: runtime` installations (the previous default), UCS MAC/disk enrichment remains unchanged. Missing declared inventory never falls back to UCS. `installConfig.openshift.registryTrustPolicy` explicitly selects `public` (normal system trust, no added CA) or `additional-ca` (private trusted CA file required). Private pull-secret JSON, SSH public key and conditional registry CA are read only with `render-ocp.py --executable`, written outside this repository with mode0600, and forbidden in `--dry-run` output.

## Optional image reconciliation

`clusters/<management>/local-registry.yaml` owns `spec.ocMirror`. The shipped example
selects a fixed upstream v0.1.7 OLM contract and a bounded disposable content prefix.
Site registry destinations, selected digest inventory, resources, polling and runtime
Secret/CA references are explicit input. See [registry lifecycle](../docs/architectures/REGISTRY_CHAIN.md)
for producer export, manual activation, trust choices and runtime limits.

## Declared shared Timescape consumer

[`gitops/hcp-base/tier2-isovalent/hubble-timescape.yaml`](gitops/hcp-base/tier2-isovalent/hubble-timescape.yaml) owns the selected shared Timescape Application values, including authentication method, gRPC choice and Secret references. The existing declared-base copier substitutes only selected repository/image-registry identity and emits `clusters/_base/tier2-isovalent/hubble-timescape.yaml`; the artifact manifest includes that file. Edit this input instead of the downstream generated copy. The public selected chart/version remains distinct from newer internal compositions, and no real credential or historical private receipt is copied into it.
