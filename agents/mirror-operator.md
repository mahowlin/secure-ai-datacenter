# Optional community image-mirror operator

The community operator is distinct from the supported `oc-mirror` CLI. Its immutable inspection inputs are prepared separately, and this example is not ready for operator activation: scoped destination-write authorization, credential custody, monitoring compatibility, effective mirror-policy ordering and copy/consumer/recovery checks remain prerequisites. Existing registry hosting and installer/mirroring mechanisms do not depend on it.

Start with [image-delivery stages](../docs/architectures/REGISTRY_CHAIN.md), then use this reference for the pinned implementation and its operational limits.

## Selected optional operator

The optional integration selects community [oc-mirror-operator v0.1.7](https://github.com/mariusbertram/oc-mirror-operator/tree/eea10842d7048b61304151ee0b63d2e3b46d6846), exact upstream `eea10842d7048b61304151ee0b63d2e3b46d6846`, Apache-2.0. It is an independent **regclient-based operator**, distinct from the supported `oc-mirror` CLI. Do not transfer CLI support or qualification claims to the operator.

Use one OLM owner: package `oc-mirror`, channel `alpha`, starting CSV `oc-mirror.v0.1.7`, Manual InstallPlan approval. The pinned published manager/worker RoleBindings require **`oc-mirror-system`**; a renamed namespace fails validation. The official catalog, bundle, controller, manager, worker and five OpenShift-version console-plugin images form the declared **ten-image bootstrap closure**. The input pins each digest, the CSV bytes and runtime binding bytes. Published CSV declarations are not proof of runtime compatibility or Red Hat support.

The operator controller reconciles `ImageSet` and `MirrorTarget` (`mirror.openshift.io/v1alpha1`). One manager per target schedules worker pods; workers copy selected images and report results. Registry storage, access and TLS remain prerequisites. The selected normal OLM release enables its console plugin and monitoring integrations, including Console and `openshift-config-managed` effects. This is not merely an isolated namespaced workload. Review its actual pinned CSV RBAC, console registration, monitoring resources and collision/capacity impact before activation. There is no proven plugin-disable variant here.

The tested public capability is offline source preparation: pinned installation
artifacts, declared-input rendering, exported producer-contract consumption and
manual activation boundaries. It does not establish an installed controller,
continuous cache reconciliation, complete image contents or recovery. Require
those operational checks against the selected site before accepting delivery.

For the separately selected full-cache source mechanism, see [selected inventory, production settings and lifecycle readiness](../docs/architectures/HCP_CACHE_LIFECYCLE.md). It reuses this pinned operator owner with distinct production resources. The example trial and its qualification boundaries remain unchanged.

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

## Prepare the selected external operator input

Platform owns the dependency selection in
`data/package-contracts/oc-mirror-operator-v0.1.7.json` and the local-registry
`installation.artifactContractPath` / `artifactDirectory`. Airgap owns acquisition
and import. The contract pins upstream commit, bundle image digest, CSV and two
RoleBindings, bundle annotations and Apache-2.0 license hashes. No upstream
source/bundle directory is retained as Platform site code.

Before resolving an enabled operator integration, supply the declared prepared
input. From the Platform input root, using the selected requirements environment:

```bash
: "${AIRGAP_ROOT:?Set selected Airgap/sys-admin checkout}"
# Disconnected import: SOURCE_CACHE contains the declared upstream file layout.
: "${SOURCE_CACHE:?Set verified prepared-artifact-layout artifact/cache directory}"
python "$AIRGAP_ROOT/scripts/prepare-oc-mirror-artifacts.py" \
  --contract data/package-contracts/oc-mirror-operator-v0.1.7.json \
  --source-root "$SOURCE_CACHE" \
  --output .artifacts/oc-mirror-operator/v0.1.7
```

For explicit connected acquisition, replace `--source-root "$SOURCE_CACHE"` with
`--fetch`. The helper uses the existing pinned Crane client to read the immutable published
bundle and fetches LICENSE at the declared Git commit, with normal TLS. It
checks every selected checksum before writing; it does not substitute stale
generated CSV templates from Git for the published release bundle.
It preserves conflicting cache files. Retain `artifact-receipt.json` and LICENSE
with the imported files. An unchanged prepared directory can be copied into a
disconnected build with the same declared path. The resolver rechecks the
contract/release join and all file hashes locally; it never downloads anything.

The existing Airgap `sync-hcp-oc-mirror-operator.yaml` workflow can prepare and
upload `oc-mirror-operator-inputs` with `artifacts_only=true`; the registry-writing
job is skipped in that mode. `artifact_source_path` selects a pre-staged cache
for disconnected preparation. Neither local preparation nor that mode installs
an operator or proves registry content. CI/build callers must stage this input
before renderer tests; a clean checkout alone no longer contains third-party
inspection bytes. Run only the selected artifact preparation under the site's
existing pipeline authority.

### Render from prepared inputs

Run these in the datacenter checkout using its documented Python environment. The selected input root includes `data/`, `platform-release.yaml`, templates, scripts and prepared checksum-verified external inputs. `example-management` is an example selector; use your declared management name for deployment.

```bash
python scripts/render-gitops.py --root .
python scripts/export_hcp_oc_mirror_producer.py --root . \
  --cluster example-management \
  --output ../secure-ai-airgap/mirror/package-inputs/hcp-oc-mirror-operator/contract.json
python scripts/write-artifact-manifest.py --root .
```

Review/copy generated `secure-ai-gitops` payloads into the selected target using the normal artifact handoff. The exported producer contract must match the selected renderer input exactly; do not hand-edit the exported destinations or image list. Commit source inputs before recording the generated artifact manifest's source identity.

In the airgap checkout, validate the consumed contract without contacting a registry:

```bash
python mirror/package-inputs/hcp-oc-mirror-operator/validate-contract.py \
  --expected-registry registry.example.com:5000
```

Replace the example address through canonical input and regenerate; the validator rejects a conflicting selected destination. The manual **HCP oc-mirror-operator bootstrap images** workflow takes an explicit `environment` profile and optional `project_contract` metadata path already staged by the owning pipeline. It uses existing `RUNNER_LABELS`, `RUNNER_CONTAINER_IMAGE`, selected registry resolution and runtime `REDHAT_PULL_SECRET` references. Its declared execution scope is to copy only immutable upstream references with pinned/checksummed Crane and verify the complete source/target content closure for supported image and attestation formats. It never writes the hosted cache. The graph verifier checks descriptor/blob sizes and SHA256 bytes and validates runnable images with Crane. Supported attestations require explicit `artifactType: application/vnd.docker.attestation.manifest.v1+json` and `subject`, an indexed `attestation-manifest` marker, an OCI empty config (`application/vnd.oci.empty.v1+json`, JSON `{}`), and in-toto `Statement/v1` layers; subject and predicate relationships are checked structurally. Legacy annotation-only attestations, `Statement/v0.1` and unknown forms fail closed rather than being skipped. Offline positive/negative fixtures cover this mechanism; the selected site still needs an actual normal-TLS bootstrap run. Digest/manifest equality alone is not full-blob or recovery validation.

A matching destination root is fully verified without copying; missing/corrupt content fails instead of silently overwriting it. Only an explicit missing-manifest response permits copy. TLS/auth errors, conflicting roots, unknown artifacts and preexisting referrer fallback tags fail closed. For an absent root, required new fallback tags are checked before copy and their exact contents checked afterward. The workflow serializes its own launches, but cannot lock other repositories or external writers: arrange exclusive destination-tag/fallback ownership for the run. This is a concurrent-writer limitation, not an atomic cross-client lock. Receipts establish content integrity and structural attestation consistency, **not signature verification or publisher trust**. Registry cleanup and production-cache repair remain separately owned operations.

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

## Build callers

The renderer-checks workflow prepares this input before tests; the internal
Generate workflow does so before resolution/rendering. Both check out an exact
Airgap source commit and invoke its `prepare-operator-inputs` action. The action
checks the Platform contract's selected bundle/file hashes and LICENSE.
`OPERATOR_ARTIFACT_SOURCE_PATH` may point to an already mounted prepared cache
for a disconnected runner. An empty value explicitly selects connected,
read-only immutable acquisition; the renderer itself remains offline.

Public CI uses the site's configured `RUNNER_LABELS` and
`RUNNER_CONTAINER_IMAGE` with Python, pip, curl and tar. The public verifier is manually invoked after site runner configuration; initial
publication does not launch unconfigured jobs. Internal Platform retains its
automatic same-repository PR/main checks. The reviewed Airgap commit must be available at the
published repository before this caller is enabled. Local builds use the
preparation command above before `render-gitops.py` or renderer tests; a missing
or changed prepared file remains an error, never an implicit download.
