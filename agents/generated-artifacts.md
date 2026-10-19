# Validate generated artifact handoffs

Use the repository requirements environment from the checkout root. Read the ownership and claim boundaries below before changing a source/target join. These tests use local fixtures and do not run the transport CLI, push, install or deploy.

## Complete inventory and CNI transition regressions

```bash
python -m unittest discover -s tests -p test_generated_transport_inventory.py -v
python -m unittest discover -s tests -p test_cni_output_transition.py -v
```

## Exact committed publication scope

```bash
python -m unittest discover -s tests -p test_attestation_commit_bytes.py -v
python -m unittest discover -s tests -p test_storage_transition_composer.py -v
```

For changed renderer inputs, render into independent temporary outputs twice, compare payload bytes, then use the existing manifest writer and verifier against the affected target. Keep private runtime files outside the raw source tree. The transport CLI can commit and push; invoke it only through its separately authorized owning pipeline. Do not infer whole-repository or runtime readiness from these checks.

## Selected inputs and consumer joins

Use the selected source candidates and their local toolchain; none of these commands applies infrastructure.

```bash
: "${COMPUTE_ROOT:?Set selected Compute candidate root}"
python -m unittest discover -s tests -p test_ucs_reference_closure.py -v
python -m unittest discover -s tests -p test_selected_validator_inputs.py -v
python -m unittest discover -s tests -p test_worker_replica_precedence.py -v
python -m unittest discover -s tests -p test_declared_timescape_base.py -v
```

The management interface and any receipt-bound CiliumConfig profile are separate declared owners: changing one without the other is a conflict, not a valid rename. The public legacy path and current internal receipt path retain their own contracts. Inspect the actual selected Application Helm output and metadata key references as well as the parent root.


## Registry and hardware runtime enrichment

Portworx tag preflight uses Skopeo with normal TLS. Supply REGISTRY_AUTH_FILE
(mode0600) and, for a private CA, REGISTRY_CERT_DIR containing the approved
ca.crt. Missing client/auth/trust is a prerequisite failure, never permission to
disable TLS. The declared registry and locked digest still determine the image.
Transport keeps Git authorization in the child process environment rather than
persisting it in origin URLs. Invoke actual transport only through its owning
pipeline authority.

The optional boot-model helper accepts a hostname/serial map from cluster YAML
or explicit hostname=serial pairs. It observes actual MSTOR-RAID virtual or
passthrough models and fails on ambiguity; it never infers RAID from disk count.
Other controller models require reviewed declared rootDeviceHints. This helper
is separate from the portable declared-hardware installation path.

## Ownership and claim boundaries

Canonical site inputs and explicit defaults select the deployment. Renderers derive raw artifacts; downstream repositories own execution mechanics, independent resources and private runtime enrichment.

## Complete transport inventory

`transport-generated-artifacts.py` reads every selected target file and checksum from `artifact-manifest.yaml`, including shared GitOps bases, storage, exporter and configuration files outside hosted-cluster directories. It validates the full source set, copies exactly those paths and checks all advertised destination hashes before copying the manifest or committing. A copied manifest must never claim missing payloads.

The previously transported manifest records generated ownership for later removal/rename. A retired path is removed only when its bytes still match that previous ownership record. Independently changed retired/current files, unclaimed collisions and runtime-owner overlaps fail before payload replacement. Independent target files and private CA/bootstrap material are preserved; transport never deletes an entire target directory to reconcile a subset.

For first adoption without a prior manifest, matching bytes or an existing generated header identify compatible generated targets. Other collisions need the target owner's explicit reconciliation. Old paths without a reliable ownership record are not automatically deleted. An existing full NAC project still needs its own reviewed scan/import policy; preserving unrelated files does not qualify the complete Terraform project. Check the actual manifest and final target diff before an authorized pipeline transport. The helper can commit and push; local regression tests do neither.

## CNI transitions

OCP output writing removes only the two known renderer-owned conditional Day 1 Cilium manifests when the selected network no longer emits them. A foreign file at either path fails instead of being deleted. Independent raw-directory files remain. The artifact verifier rejects Cilium Day 1 manifests for a non-Cilium selected network even if their hashes were included in a new manifest.

Fresh-target checks use the actual selected generated inventory and synthetic target trust material. External Git operations, runtime credentials, cluster admission and deployment are not exercised.

## Scoped publication attestation

`scoped-publication-attestation.py` reads present, absent and composed payloads and Application leaf identities from the exact resolved `--target-head` Git commit. Worktree files do not substitute for that commit, even when the worktree is clean at a different head. Existing base-to-head diff allowlisting and retained-owner checks remain in force. An absent worktree file does not prove absence from the named commit. This is a scoped claim, never whole-repository parity.

## Packaged publication snapshot

The manifest bundled with the single-commit publication records `source.commit: unknown`: a commit cannot contain its own final hash. Its target paths and SHA-256 checksums still describe the packaged bytes exactly. After checkout, rerun the existing renderers and manifest writer to bind a new generated handoff to the checked-out source commit; review those outputs before import.
