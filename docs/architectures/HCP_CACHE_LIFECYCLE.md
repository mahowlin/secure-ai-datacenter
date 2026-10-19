# Populate a selected hosted-cluster cache before activation

The compact example uses the **preceding site registry only**. Its hosted release,
worker image and exporter configuration do not depend on the optional management
cache. Keep this mode for an existing-registry deployment or bootstrap/recovery.
Registry hosting can be prepared separately; it is not a content-filling proxy.

For a second cache tier, the existing image-sync workflow produces a reviewed
selected inventory into the site registry. The existing optional mirror operator
then copies the selected cached roots into the management registry. A separate
read-only check verifies the actual read authorities before the existing HCP
lifecycle transitions include control planes or workers.

[![The preceding registry enables management bootstrap; an independently hosted registry and optional operator serve selected hosted workers.](../diagrams/registry-chain.svg)](../diagrams/registry-chain.svg)

## Ownership and order

| Before | Source / execution | Result required by the next step |
|---|---|---|
| Management OpenShift exists | Existing [Airgap image sync](https://github.com/mahowlin/secure-ai-airgap/blob/main/.github/workflows/sync-images.yaml), then [Compute installation](COMPACT_BAREMETAL_INSTALL.md) | Site/upstream images, working DNS/time, actual install and CNI |
| Hosted consumers exist | GitOps registry/PVC/read+write services and the separately pinned operator bootstrap/OLM procedure | Ready storage, registry, current public CA and distinct private reader/writer credentials |
| Hosted cache production | Versioned inventory below → existing `sync-images` target `hcp-cache` → existing bundle promoter | Every selected root readable at its exact site destination; immutable contract/content evidence |
| Cached consumption | Explicit production ImageSet/MirrorTarget, separate from trial objects/custody | Operator copies only that inventory to its declared production prefix |
| Control-plane creation | `hostedClusterLifecycle.phase: control-planes` plus matching reader evidence | Release lookup **and management CDI disk dependencies** are ready before creating a HostedCluster |
| Worker creation | `phase: workers` plus current complete reader evidence | Full selected content, unchanged destination/config/trust/auth prerequisite identity |

The held phase prepares registry/operator resources without activating a hosted
consumer. A control-plane or zero-worker stage is **not** permission to defer
release lookup or disk/import dependencies. By default `releaseLookup` stays on
the preceding registry even with a cache; the generated HostedCluster and
NodePool pin its selected digest there. `releaseLookup: hosted-cache` is explicit
and requires that root at the cache during the control-plane stage.

Actual installation, source production, operator activation and secret
preparation use their owning authorized pipelines. These instructions supply
source and handoff commands; do not replace them with a local live rollout.

## 1. Describe the selected inputs, then review the closure

Edit [the optional settings template](../../examples/hcp-cache-production/settings.yaml)
into your `LocalRegistry.spec.cacheProduction`, keeping the phase held while
preparing production. Use the real reserved frontend, writer authority and public
CA; align the reader name with `externalPull.consumer.pullSecret`. The production
namespace is the installed operator's own namespace. ImageSet/MirrorTarget names,
writer Secret and repository prefix must differ from the trial.

The requirements export is an offline preparation step:

Exact execution: [agent procedure](../../agents/hosted-cache.md).

This offline output contains selected HostedCluster release references,
architecture, guest CNI/package selection, add-ons, source-file fingerprints and
actual rendered image references. It is a requirements description, **not** an
automatically complete release inventory. It deliberately bypasses content
activation so you can prepare an inventory before any readiness receipt exists.

Create a versioned customer inventory JSON with these fields:

| Field | Required meaning |
|---|---|
| `selection` | The exact `selected-requirements.json` object |
| `roots` | Unique pinned image roots and the destination/consumer map below |
| `unresolved` | Empty list; unknown selected dependencies block export |
| `provenance` | List of `{path, sha256}` references to reviewed closure records described below, relative to the source root |
| `reviewedBy`, `refreshProcedure` | Responsible completeness reviewer and the usable release-refresh procedure |

Each root contains:

```json
{
  "source": "source.example/product/component@sha256:REPLACE_WITH_REAL_DIGEST",
  "requestedReferences": ["registry.example.com:5000/product/component:SELECTED_TAG"],
  "siteRepository": "product/component",
  "cacheRepository": "hosted-content/product/component",
  "scope": "cache",
  "phase": "workers",
  "architecture": "x86_64",
  "uses": ["guest-cni:example-multus-a"]
}
```

This illustrates the shape; it is intentionally not a usable image pin. A root
may carry multiple requested tags/references for the same digest. Use the canonical original repository for a release-info component, not only a matching digest under an unrelated mirror repository. Source tags
are resolved to digests by the inventory owner, not looked up during rendering.
The requested repository must agree with its source or site mapping. One source
repository has one destination scope and cache mapping; collisions and unknown rendered references
fail. Only roots with `scope: cache` enter the production ImageSet. Other roots
use the preceding registry. Both sets enter source production and read checks.

Coverage must include `release:<hosted-name>` and `disk:<hosted-name>` for each
selected hosted cluster, `guest-cni:<hosted-name>` for Cilium guests, and
`addon:<selected-addon>` where selected. Release entries must also name the exact
selected release reference. Include **every** release payload component, selected
architecture's worker container disk, selected product operands and add-on/chart
images, not only the top-level release image. Disk dependencies must be control-plane-stage roots, even if cached, because management CDI import may occur before worker creation. Management/registry/operator bootstrap images must stay on the preceding registry. A source repository cannot mix cache/preceding scopes: image mirror rules apply to a whole repository, not individual digests.
Operator-bootstrap image production remains its existing separate workflow.

### Bind the completeness review to its source evidence

After filling the inventory roots, generate the **unreviewed** closure record:

Exact execution: [agent procedure](../../agents/hosted-cache.md).

It contains canonical `selectionSha256`, `rootsSha256` and a `coverage` map
from each use to its pinned roots. Keep these consistent with the inventory.
Fill `sources` with `{path, sha256}` references to the actual versioned JSON
release/stream/product/chart metadata snapshots. After the responsible review,
clear its `unresolved` list and pin this record under inventory `provenance`.
A source path/hash, the closure's selection/root fingerprint, and every required
coverage association are verified. OpenShift release-info `references.spec.tags`
are also checked: a referenced payload digest missing from the inventory fails.
Changing a root or destination requires refreshing the completeness record; an
unrelated JSON file cannot stand in for that record.

This review remains responsible for dependency semantics in vendor metadata
that is not a release-info document. The validator does not turn a declaration
of completeness into vendor qualification or discover dynamic chart/OLM
requirements. Record exact chart/catalog/package revisions and any unresolved
requirement before marking the review complete.

### Refresh for another release or composition

1. Export the new requirements. Source/selection changes invalidate the old
   inventory. Phase and replica transitions alone do not change image selection.
2. Acquire exact selected release metadata with the entitled, normal-TLS
   the selected release-info query in the agent procedure.
   Retain the JSON and its hash. Resolve its component roots by digest.
3. Recover the selected release's RHCOS stream/boot metadata and its KubeVirt
   container-disk digest for the chosen architecture. Do not substitute the
   management release's disk when the tenant release differs.
4. Inspect the selected CLife/OLM product release, operands and enabled chart/
   add-on images using the [existing release-inspection and mirroring procedure](https://github.com/mahowlin/secure-ai-airgap/blob/main/docs/mop/01-image-mirroring.md).
   Keep source/version/content hashes and actual resolved dependency lists in
   the provenance records. Inspect dynamic/catalog references separately.
5. Review the **complete** closure, populate root uses/destination maps, remove
   no unknown dependency by assumption, then set `unresolved: []`. Commit this
   versioned inventory/provenance and pin its actual file SHA256 in settings.

This contract accepts a customer-supplied, reviewed closure. It checks selection,
provenance hashes, explicit coverage, rendered references and content; it does
not discover every hidden vendor dependency or qualify arbitrary new releases.
An unresolved closure is a clear stop, not a reason to reuse a historical BOM.

## 2. Export and produce the same inventory

Exact execution: [agent procedure](../../agents/hosted-cache.md).

Stage this non-secret file and any declared **public** CA at the same relative
paths in the reviewed Airgap checkout. In the existing `Sync Images` pipeline,
select `sync_target=hcp-cache`, `promote_scope=none`, and the staged
`hcp_cache_contract` path. `validate_only=true` performs read checks with no
copy; otherwise this explicit target produces selected roots into the site
registry and invokes the existing bundle promoter. It does not write the hosted
cache. The Linux x86_64 runner job uses the existing normal-TLS, checksum-verified download mechanism to stage `crane 0.22.0` in its temporary directory; installed tools are untouched. Reader verification requires the same pinned binary.

`HCP_CACHE_SOURCE_AUTH` is the protected Docker authentication document for the
selected upstream and site authorities. Its custody corresponds to the declared
site-writer reference. No value belongs in the contract or Git. Normal system
trust or the exact declared additional CA is mandatory; the new path does not
inherit legacy TLS-disable flags. A conflicting existing digest/referrer tag is
preserved and fails production. The source workflow uploads immutable contract/
verification/bundle artifacts through its existing artifact mechanism. This content-evidence scope is not an `ocp-install-core` or `cilium-day1` policy bundle and must not be substituted for those install receipts.

## 3. Render production and verify the read authorities

Held GitOps rendering emits `production-imageset.yaml` and
`production-mirrortarget.yaml` alongside the existing operator overlay. They use
the same selected roots/site mapping, explicit production budgets/resources,
separate writer Secret and normal-TLS CA ConfigMap. The original trial selection
and operator installation are not widened. Site-owned credential preparation
must bind the writer to the intended registry/prefix; a CR prefix alone is not a
server-side authorization policy.

After authorized operator reconciliation, the owning pipeline prepares a
**reader-only** Docker auth directory, and a non-secret JSON observation matching
`runtimePrerequisites` (current reader Secret UID/resourceVersion and DNS/TLS
evidence IDs). It then uses Airgap's read-only mode:

Exact execution: [agent procedure](../../agents/hosted-cache.md).

Use `--phase control-planes` for the earlier transition: it verifies all control-plane and management disk roots without requiring later worker-only cache content. The workers check requires the complete set.

The auth document must cover the required site and cache read authorities. The reader check verifies site content for every selected root and, for cached roots, the cache endpoint as well; this covers management release lookup, cache consumers and the preceding path independently.
The verifier streams and hashes every selected manifest/config/blob, checks the
requested architecture, and performs the existing remote image-format check.
Unknown media/graphs fail; existing per-root bounds (depth8,256 manifests and
8MiB parsed JSON blobs) remain explicit. Reader mode cannot copy. Failed runs
remove any previous success file at their output path.

Stage the successful JSON into Datacenter's ignored private runtime directory,
set its real `{path, sha256}` under `cacheProduction.readiness`, then render the
control-plane or workers phase. The barrier checks the exact contract,
selection/destination/config, proof stage, current declared reader/trust/
prerequisite identity, age and required root content. Changed inputs, stale
proof or missing images stop before usable HostedCluster/NodePool activation
output is returned. Re-run verification after credential/CA/content changes.
Do not reuse an old generated activation artifact as a bypass; the owning
pipeline must re-render and verify its manifest at each transition.

This is raw content readability on the verifier's path plus the owner's current
prerequisite observation. Native guest DNS/TLS/auth/CRI cold pulls, management
CDI import and offline boot still require their own authorized runtime evidence.
No source test or success receipt is described as that qualification.
