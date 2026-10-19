# Optional community image-mirror operator

The community operator is distinct from the supported `oc-mirror` CLI. Its immutable inspection inputs are prepared separately, and this example is not ready for operator activation: scoped destination-write authorization, credential custody, monitoring compatibility, effective mirror-policy ordering and copy/consumer/recovery checks remain prerequisites. Existing registry hosting and installer/mirroring mechanisms do not depend on it.

Start with [image-delivery stages](REGISTRY_CHAIN.md), then use this reference for the pinned implementation and its operational limits.

## Selected optional operator

The optional integration selects community [oc-mirror-operator v0.1.7](https://github.com/mariusbertram/oc-mirror-operator/tree/eea10842d7048b61304151ee0b63d2e3b46d6846), exact upstream `eea10842d7048b61304151ee0b63d2e3b46d6846`, Apache-2.0. It is an independent **regclient-based operator**, distinct from the supported `oc-mirror` CLI. Do not transfer CLI support or qualification claims to the operator.

Use one OLM owner: package `oc-mirror`, channel `alpha`, starting CSV `oc-mirror.v0.1.7`, Manual InstallPlan approval. The pinned published manager/worker RoleBindings require **`oc-mirror-system`**; a renamed namespace fails validation. The official catalog, bundle, controller, manager, worker and five OpenShift-version console-plugin images form the declared **ten-image bootstrap closure**. The input pins each digest, the CSV bytes and runtime binding bytes. Published CSV declarations are not proof of runtime compatibility or Red Hat support.

The operator controller reconciles `ImageSet` and `MirrorTarget` (`mirror.openshift.io/v1alpha1`). One manager per target schedules worker pods; workers copy selected images and report results. Registry storage, access and TLS remain prerequisites. The selected normal OLM release enables its console plugin and monitoring integrations, including Console and `openshift-config-managed` effects. This is not merely an isolated namespaced workload. Review its actual pinned CSV RBAC, console registration, monitoring resources and collision/capacity impact before activation. There is no proven plugin-disable variant here.

The tested public capability is offline source preparation: pinned installation
artifacts, declared-input rendering, exported producer-contract consumption and
manual activation boundaries. It does not establish an installed controller,
continuous cache reconciliation, complete image contents or recovery. Require
those operational checks against the selected site before accepting delivery.

For the separately selected full-cache source mechanism, see [selected inventory, production settings and lifecycle readiness](HCP_CACHE_LIFECYCLE.md). It reuses this pinned operator owner with distinct production resources. The example trial and its qualification boundaries remain unchanged.

## Input-to-output contract

The public `example-management` input renders the optional installation as a **manually synchronized Argo CD Application**. It selects one small digest-pinned image from the example site registry into a separate `mirror-validation` repository prefix. This proves the packaging mechanism; it is not a complete release inventory or a production cache-population plan. The retained `oc-mirror-trial` directory/file names identify the existing bounded renderer mechanism, not a deployment claim.

| Declared input | Derived output / consumer |
|---|---|
| `spec.ocMirror.publish`, `applicationName`, `syncMode` | manual Argo Application under `clusters/<management>/hcp-oc-mirror-trial.yaml`; false omits all operator resources and extra sync ingress |
| `namespace`, `installation.*`, `release.*` | pinned CatalogSource, OperatorGroup, Subscription, bootstrap IDMS; OLM owns actual CSV/CRDs/controller |
| `release.bootstrapImages`, `producerTrust` | exported `hcp-oc-mirror-producer/v3` JSON consumed by airgap's existing manual producer mechanism |
| `selectedImages[].inventoryName/source/targetRepo` | ImageSet additionalImages pinned by digest; selections come from this site's input, with no private BOM fallback |
| `targetPrefix`, `imageSet`, `mirrorTarget`, resources and polling fields | MirrorTarget in its isolated namespace/prefix; Service-only exposure, normal TLS, bounded concurrency/batch size |
| `authSecret`, `caConfigMap` | existing runtime Docker-auth Secret and trusted CA ConfigMap references; no credential or CA bytes in public output |
| LocalRegistry namespace/listener + optional operator namespace | sync ingress restricted to selected namespace AND manager/worker pod labels |

The bounded example accepts one or two selected images and concurrency/batch size 1–2. It does not silently broaden its selection to an entire release. Site-specific sizing, inventory and production operation need their own explicit reviewed inputs and runtime qualification. A disabled feature changes no operator-owned objects.

### Prepared dependency handoff

Airgap acquires or imports the five checksum-pinned inspection files selected by
Platform. Platform verifies the local input and renders installation resources;
GitOps/OLM own reconciliation and installation. The source is not vendored here.
The [agent procedure](../../agents/mirror-operator.md#prepare-the-selected-external-operator-input)
owns exact preparation, render and producer commands, including disconnected
import. An input-preparation receipt is not installation or image-delivery proof.

### Trust and authentication prerequisites

`producerTrust: {mode: system}` explicitly uses normal system roots for the preceding site registry. For an independently signed registry, select `mode: bundle`, `sourcePath` (private CA file inside the selected datacenter input root) and `producerPath` (`mirror/certs/<site>.pem` staged privately in the airgap pipeline). Export computes its SHA256; the producer rejects missing or changed CA bytes. Neither private CA file goes into a public archive. Both modes retain TLS verification; no insecure flag is offered.

Before syncing the mirror CRs, require a protocol-qualified dedicated writer endpoint that enforces the selected repository subtree and denies sibling/cross-repository operations. Its private runtime identity must not grant registry-wide sync access. Do not copy or reuse an existing broad writer credential; separate source-pull and narrowly authorized destination-push identities, retaining only the Secret readers the selected operator actually requires. The example references `authSecret`; a protocol-qualified restricted-writer endpoint must be supplied separately before activation. The owning pipeline supplies the approved Docker auth reference and `caConfigMap` with key `ca-bundle.crt` containing trusted source/destination roots (including destination service CA where needed). Preserve unrelated auth/CA owners. An already-installed namespace or object is not permission to overwrite it. Supply Secret values privately through the owning pipeline; keep them out of rendered configuration and logs.

## Activation and operational checks

Installation, custom-resource activation and rollback use the site's approved pipeline/GitOps process. Complete the prerequisites below before activating mirroring.

1. Prove preceding site registry availability, authorized pulls/pushes, trust and ten-image closure. Confirm destination hosting/PVC readiness, available resources, ownership/collisions and healthy machine-config pools. Review the new IDMS for conflicts and release-specific machine-config effects; stop on unexpected disruption.
2. Stage the bootstrap IDMS first and verify its effective policy on affected nodes before starting CatalogSource/bundle resolution. Global mirror changes can propagate into hosted control-plane workloads and replace their pods even when machine-config settings disable drains; observe hosted API readiness and continuous authorized traffic, not only machine-config pool state. Then sync the reviewed Namespace/CatalogSource/OperatorGroup/Subscription subset and inspect only the selected immutable InstallPlan. The child Application remains manual and must not auto-activate mirror CRs. A bundle pulled from upstream does not prove the site-registry-only path.
3. Require CSV Succeeded, CRDs Established and controller Ready. Review console and monitoring effects separately: rejected monitoring integration needs a compatible source-owned correction, not a controller-owned live handpatch; its state does not prove that mirroring works or cannot work. Console presentation is not a first-copy prerequisite, but this pinned renderer still uses the normal published plugin-enabled OLM path; no tested plugin-disable variant is supplied. Only after dedicated-writer authorization and runtime trust/custody are qualified may the owner separately activate ImageSet/MirrorTarget. Do not install a parallel raw controller/CRD set alongside OLM.
4. Observe actual source-to-target digest and complete descriptor/blob content, authenticated normal-TLS consumer pulls and unchanged-input reconciliation. Check worker/manager errors, invalid-auth/unreachable-source behavior and bounded restart/recovery. Merely Ready pods or a completed job are not image-delivery acceptance.
5. Qualify retained-content recovery with a disposable prefix only: missing manifests, missing child manifests/blobs and a bounded worker restart. Manifest-presence checks alone do not prove all-blob repair. No production-cache deletion or cleanup is authorized by this guide. Existing registry content is retained by default.

Rollback saves status, then removes only the selected ImageSet/MirrorTarget and their owned managers/workers through the normal inverse operation. Remove the selected OLM Subscription/CSV/controller/catalog/group only after checking other consumers. Preserve shared CRDs and registry PVC/content when any consumer remains. Revert only reviewed IDMS/network ingress deltas through their owners and observe machine-config health. No blanket delete or prune recipe is supplied.

## What local validation establishes

Local validation checks input propagation, deterministic output, the exported producer contract, pinned artifact drift rejection and manual activation settings. It does not establish installed state, registry authentication, complete image delivery or recovery. Qualify the three-registry chain against real sources and consumers before relying on it for bootstrap or production image pulls.
