# Install a compact OpenShift cluster

Use three bare-metal servers for one compact OpenShift cluster with Isovalent Networking for Kubernetes from first boot. Each server runs control-plane and application workloads. UCS, Intersight, ACI and hosted clusters are optional integrations, not prerequisites. The existing implementation supports static IPv4 and exactly two management NICs with common interface names across the three hosts; choose LACP with a matching switch LAG or active-backup. Other hardware/network layouts are outside this example.

[![A working site network and image services are prerequisites; declared inputs become an Agent ISO, the site boots three servers, OpenShift and Isovalent CNI become ready, then optional Argo CD takes over.](../docs/diagrams/install-lifecycle.svg)](../docs/diagrams/install-lifecycle.svg)

### Topology before installation

Three servers share a management subnet. Each has a declared management bond, node IP and boot disk; API/apps VIPs are reserved on that subnet. The bond carries bootstrap, node/API traffic and the default route used for DNS, NTP and image pulls. A selected tenant trunk is additional connectivity, not a replacement for this management path.

For the portable starter, use an existing site LAN and gateway. For the ACI composition, [Fabric and host network handoff](../docs/architectures/FABRIC_NETWORK.md) provides the matching BD/EPG/access attachment and distinguishes it from tenant BGP. DNS records, reachable time/image services, entitlement, trusted CAs and the site's media/BMC boot process must be ready before the cluster exists.

### Sequence and observable result

| Step | Result to inspect |
|---|---|
| Copy and edit site inputs | Real NIC/MAC/disk identities, node/VIP addressing and upstream services |
| Validate and render | Five configuration files; raw artifact hashes agree |
| Prepare through Compute | Private enriched directory with official installer/CLife manifests |
| Build and boot ISO | Servers join the declared rendezvous; installation logs retained privately |
| Verify readiness | Three Ready nodes, converged cluster operators, declared Cilium version/configuration |
| Optional GitOps bootstrap | Reviewed real cluster root; selected catalog and Application sync/health |

## 1. Start with only the inputs you need

[examples/standalone](../examples/standalone) is a complete minimal input set in the existing schema. It contains one cluster, one management network, shared DNS/NTP/registry data and one existing release selection. It has no hosted inventory, ILB or conformance identities or site migration state. Optional GitOps bootstrap selection reuses the existing Subscription input fields; it does not create a customer GitOps root. The full `data/` tree remains the optional integrated reference.

From the platform checkout, set up its documented Python dependencies. `PLATFORM_PYTHON` points to that environment's Python; `SITE_ROOT` is a separate editable working directory, outside Git, and `CLUSTER` matches your declared cluster name:

```bash
PLATFORM_ROOT="$PWD"
PLATFORM_PYTHON="$PLATFORM_ROOT/.venv/bin/python"
SITE_ROOT="$HOME/secure-ai/site-inputs"
CLUSTER=example-third-party
mkdir -p "$SITE_ROOT"
cp -rf examples/standalone/. "$SITE_ROOT/"
cp -rf templates schemas "$SITE_ROOT/"
```

Edit **inputs** before rendering:

| Input | Supply for your site |
|---|---|
| `data/clusters/example-third-party/cluster.yaml` | Cluster/base domain, API/ingress VIPs, three node names/IPs, real NIC names/MACs, exact boot-disk hints, bond mode/options and rendezvous node. Rename directory/metadata together if changing cluster name. |
| `data/networks/example-federal.yaml` | Management prefix, gateway, MTU and upstream native/access VLAN. No ACI catalog is required. |
| `data/defaults/example.yaml` | DNS/NTP, registry/release image repository, digest mirror mappings, output identities and reviewed `day1Compatibility` policy. |
| `platform-release.yaml` | Existing selected exact OpenShift/Cilium images and CLife archive facts; reviewed release facts belong here, not in renderer code. |

All sample hardware and addresses are synthetic. No server-management API is required. Check actual hardware support/sizing, disks, LAG, address reservations/non-overlap, node/API/apps DNS, time and image-source reachability before installation. The selected image sources must be reachable before this cluster exists; an explicitly selected site bootstrap registry must already be available.

## 2. Select delivery, trust and release

Choose `imageDelivery.mode: connected` for reachable upstream/site services, or `mirrored` for the exact selected bundles and their real producer receipts. The registry must exist before cluster installation; an in-cluster cache cannot bootstrap the management cluster itself.

Set `registryTrustPolicy: public` for normal system trust, or `additional-ca` for a trusted private CA supplied at executable staging. Both retain TLS verification. Select the exact OpenShift/Isovalent release and official lifecycle archive; a successful render does not qualify another release.

The [delivery/trust/release reference](../docs/architectures/INSTALL_INPUT_REFERENCE.md) explains bundle scopes, compatibility policy and how to change the example without editing renderer code. Continue below with the selected input set.

## 3. Render and hash the public artifacts

```bash
"$PLATFORM_PYTHON" "$PLATFORM_ROOT/scripts/render-ocp.py" \
  --root "$SITE_ROOT" --cluster "$CLUSTER"
"$PLATFORM_PYTHON" "$PLATFORM_ROOT/scripts/write-artifact-manifest.py" --root "$SITE_ROOT"
RAW_ROOT="$SITE_ROOT/generated/repos/secure-ai-compute"
"$PLATFORM_PYTHON" "$PLATFORM_ROOT/scripts/verify-artifact-manifest.py" \
  --manifest "$SITE_ROOT/generated/artifact-manifest.yaml" \
  --repo secure-ai-compute --target-root "$RAW_ROOT" --fail-on-extra
```

The five outputs are `install-config.yaml`, `agent-config.yaml`, `metadata.yaml`, `manifests/ciliumconfig.yaml` and `manifests/cluster-network-02-config-local.yml` in `data/openshift/$CLUSTER/`. Render twice to confirm deterministic bytes. The manifest records hashes; a local input directory without Git has source commit `unknown`, so retain its input snapshot and the originating platform commit alongside the artifact manifest. Hashes establish integrity, not credential validity or installed health.

For selected mirrored delivery, after the owning mirroring pipeline has produced real receipts:

```bash
"$PLATFORM_PYTHON" "$PLATFORM_ROOT/scripts/validate-image-policy-bundles.py" \
  --root "$SITE_ROOT" --cluster "$CLUSTER" --sys-admin-root "$AIRGAP_ROOT" \
  --output "$SITE_ROOT/image-policy-contract.yaml" \
  --ai-pod-output-root "$RAW_ROOT/data"
```

This copies only selected promoted payloads under `data/openshift/$CLUSTER/image-policy-bundles/`. Missing/mismatched scope, content hash, exact OpenShift release or exact selected Cilium images fails. It does not mirror/pull anything. Preserve the raw render manifest separately; imported mirror payloads are a separate receipt boundary, not renderer-generated files.

## 4. Consumer-local handoff and installation

Set `COMPUTE_ROOT` to the compute checkout and `INSTALL_WORK` to a fresh private directory outside any Git checkout. The [compute installation procedure](https://github.com/mahowlin/secure-ai-compute/blob/main/docs/mop/04-openshift-installation.md) is the **single authoritative generic staging / ISO / boot procedure**. It starts with these exact five artifacts, checks the verified official archive, enriches files privately, then copies **prepared** manifests into the Agent directory and verifies equality. Follow it for private runtime inputs, installer extraction, ISO, boot and read-only readiness checks.

To copy raw artifacts into an operator-owned compute checkout without remote effects:

```bash
mkdir -p "$COMPUTE_ROOT/data/openshift/$CLUSTER"
cp -rf "$RAW_ROOT/data/openshift/$CLUSTER/." "$COMPUTE_ROOT/data/openshift/$CLUSTER/"
```

Re-run `verify-artifact-manifest.py` against that checkout **before** enrichment/imports (or without `--fail-on-extra` when separate mirrored payloads are present). Keep the pristine `RAW_ROOT` for connected-mode hash checks. `transport-generated-artifacts.py` commits/pushes to remote repos; it is not the local-copy procedure and is unnecessary here.

Optional hosted control planes (HCP), MultiCluster Engine (MCE), virtualization, storage, fabric/BGP, Load Balancer and Egress Gateway and observability retain their existing source mechanics and dependency limits in [INTEGRATIONS](../docs/INTEGRATIONS.md). They are not enabled or claimed operational by this standalone render. Customer installation/account/site material is not part of this public guide.

## 5. Hand over to ongoing GitOps operation

The standalone input set does not generate a `clusters/<cluster>` application root. Supply a real reviewed root in your GitOps repository after the cluster is healthy. [Compute's post-install guide](https://github.com/mahowlin/secure-ai-compute/blob/main/docs/POST_INSTALL.md) owns the runner-path, catalog, approval and trust procedure. [GitOps](https://github.com/mahowlin/secure-ai-gitops) owns the application sources and reconciliation workflow.

The existing `gitops.operatorSubscriptions` fields in shared defaults select source/source namespace, channel and approval. Namespace and catalog aliases resolve into generated metadata; conflicting direct values and references fail. Manual approval stays manual. For connected operation with no site registry, remove example registry/mirror mappings, use `registryTrustPolicy: public` and declare the actual upstream release repository.

## If a local step fails

| Symptom | Check before continuing |
|---|---|
| Unknown cluster or missing input | Cluster directory/name, network references, and `--root` path |
| Compatibility rejection | Exact selected release and reviewed `day1Compatibility` policy; do not rename a payload to evade it |
| Artifact hash mismatch | Return to maintained inputs and render again; keep raw output separate from private staging |
| Missing mirror receipt | Run the actual selected mirroring process or select connected mode when appropriate; do not invent a receipt |
| Missing customer GitOps root | Supply and review the actual repository/ref/root before optional post-install |

Keep a private copy of install inputs/logs before boot; the installer consumes files. Hardware, entitlement or running-cluster failures continue in [Compute's installation checks](https://github.com/mahowlin/secure-ai-compute/blob/main/docs/mop/04-openshift-installation.md), not by repeatedly changing rendered YAML.
