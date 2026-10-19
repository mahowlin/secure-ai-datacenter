# Fabric render and consumer handoff

Run from Secure AI Datacenter using the Python environment in [AGENTS](../AGENTS.md). `PLATFORM_ROOT` is this checkout, `FABRIC_ROOT` is a separate reviewed Fabric checkout. Edit the catalog/cluster inputs described in [the human guide](../docs/architectures/FABRIC_NETWORK.md) first. These commands write local generated files; they do not call APIC.

```bash
PLATFORM_ROOT="$PWD"
PLATFORM_PYTHON="$PLATFORM_ROOT/.venv/bin/python"
: "${FABRIC_ROOT:?Set the reviewed Fabric checkout}"
"$PLATFORM_PYTHON" scripts/resolver.py --root "$PLATFORM_ROOT" --validate-only
"$PLATFORM_PYTHON" scripts/render-aci.py --root "$PLATFORM_ROOT"
"$PLATFORM_PYTHON" scripts/render-ocp.py --root "$PLATFORM_ROOT" --cluster example-management
"$PLATFORM_PYTHON" scripts/render-gitops.py --root "$PLATFORM_ROOT"
"$PLATFORM_PYTHON" scripts/write-artifact-manifest.py --root "$PLATFORM_ROOT"
```

The selected example emits management tenant, domains and attachments plus workload tenant and tenant access files. Repeat into a separate output directory to compare bytes before handoff. `render-aci.py --output` accepts the destination directory. No stale unmanaged file is removed by the renderer; consumer import/ownership conflicts must be resolved explicitly.

## Dedicated generated scan root

Do **not** overlay this output onto an existing `data/` tree. Old tenant identities would remain in Terraform's recursive scan. Instead create a new, empty project scan root and copy the selected generated data there, with the reviewed wrapper's module/default selections. The existing checkout and any unrelated site-owned configuration remain intact.

```bash
FABRIC_STAGE="$(mktemp -d)"
mkdir -p "$FABRIC_STAGE/data"
cp -rf "$PLATFORM_ROOT/generated/repos/secure-ai-fabric/data/." "$FABRIC_STAGE/data/"
cp -rf "$FABRIC_ROOT/modules" "$FABRIC_ROOT/defaults" "$FABRIC_STAGE/"
"$PLATFORM_PYTHON" "$PLATFORM_ROOT/scripts/verify-artifact-manifest.py" \
  --manifest "$PLATFORM_ROOT/generated/artifact-manifest.yaml" \
  --repo secure-ai-fabric --target-root "$FABRIC_STAGE" --fail-on-extra
```

Pass **this exact `FABRIC_STAGE` as Terraform `project_dir`** in the owning pipeline. Do not validate the stage and then plan the checkout's different scan root. On a rename/removal, make another fresh stage from the new manifest; the old identity cannot enter that stage. A missing, changed or extra generated file fails verification. Keep the validated stage with its manifest through plan/apply.

For an existing NAC-ACI project, first validate this isolated generated slice. Its owner then reviews/imports only those selected objects and reconciles previously owned generated identities; unrelated owned access/tenant policy must be preserved. The exact check does not qualify the entire merged project or permit blanket `data/` deletion. Its full scan/import ownership and final plan are reviewed by that project's pipeline.

Use equivalent verification for Compute and GitOps if their outputs change. [Fabric preparation](https://github.com/mahowlin/secure-ai-fabric/blob/main/agents/operations.md) owns Terraform/cache/backend details. A transport helper may commit/push: these local render commands are not that helper and do not grant live execution authority.
