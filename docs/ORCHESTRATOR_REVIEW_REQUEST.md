# Review Request: SAIF Platform 1.0 Orchestrator Workflows

## Context

We're building a "dumb orchestrator" for deploying bare-metal Kubernetes clusters. The orchestrator lives in `saif-platform` and triggers workflows in `saif-ai-pod` (the actual deployment repo).

**Goal:** One workflow trigger → cluster comes online healthy → zero manual intervention.

## Architecture

```
saif-platform (orchestrator)
  ├── orchestrate-release.yaml  → Deploys a cluster
  └── orchestrate-destroy.yaml  → Destroys a cluster

saif-ai-pod (execution)
  ├── ucs-pipeline.yaml         → Day 0: Cisco UCS hardware activation
  ├── openshift-pipeline.yaml   → Day 1: OpenShift cluster deployment
  ├── openshift-test.yaml       → Verification: Health checks
  └── openshift-undeploy.yaml   → Cleanup: Remove kubeconfig
```

## What I Need Reviewed

### File 1: orchestrate-release.yaml

```yaml
name: Orchestrate Release

# PLATFORM 1.0 Orchestrator
#
# Deploys a single cluster through Day 0 (UCS) and Day 1 (OCP).
# Day 2 (GitOps) is automatic via ArgoCD - no workflow call needed.
#
# For multiple clusters, run this workflow multiple times.
#
# This is intentionally a "dumb" orchestrator:
# - No secrets stored here (except workflow trigger token)
# - No direct cluster access
# - Just sequences workflow calls and validates results

on:
  workflow_dispatch:
    inputs:
      cluster:
        description: "Cluster to deploy"
        required: true
        type: choice
        options:
          - ai-pod-1
          - ai-pod-2
          - ai-pod-3
          - ai-pod-4
      mode:
        description: "Execution mode"
        required: true
        type: choice
        options:
          - full-deploy
          - day0-only
          - day1-only
      force_redeploy:
        description: "Force redeploy even if cluster is operational"
        required: false
        type: boolean
        default: false

env:
  GH_TOKEN: ${{ secrets.WORKFLOW_TRIGGER_TOKEN }}

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    steps:
      - name: Validate GitHub token
        run: |
          if ! gh auth status &>/dev/null; then
            echo "::error::WORKFLOW_TRIGGER_TOKEN is invalid or missing"
            exit 1
          fi
          echo "✅ GitHub token valid"

      - name: Check sub-repo workflows exist
        run: |
          echo "Checking saif-ai-pod workflows..."
          gh workflow view ucs-pipeline.yaml --repo mahowlin/saif-ai-pod &>/dev/null || { echo "::error::ucs-pipeline.yaml not found"; exit 1; }
          gh workflow view openshift-pipeline.yaml --repo mahowlin/saif-ai-pod &>/dev/null || { echo "::error::openshift-pipeline.yaml not found"; exit 1; }
          gh workflow view openshift-test.yaml --repo mahowlin/saif-ai-pod &>/dev/null || { echo "::error::openshift-test.yaml not found"; exit 1; }
          echo "✅ Sub-repo workflows accessible"

      - name: Generate summary
        run: |
          echo "## Orchestrate Release" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Parameter | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-----------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Cluster | ${{ inputs.cluster }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Mode | ${{ inputs.mode }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Force Redeploy | ${{ inputs.force_redeploy }} |" >> $GITHUB_STEP_SUMMARY

  day0:
    name: "Day 0: UCS"
    needs: validate
    if: inputs.mode == 'full-deploy' || inputs.mode == 'day0-only'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger UCS Pipeline
        id: trigger
        run: |
          PROFILE="saif-${{ inputs.cluster }}"
          BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          echo "before=$BEFORE" >> $GITHUB_OUTPUT

          echo "Triggering UCS pipeline for $PROFILE..."
          gh workflow run ucs-pipeline.yaml \
            --repo mahowlin/saif-ai-pod \
            -f operation="Activate - Apply config to server" \
            -f server_target="$PROFILE"

      - name: Wait for UCS Pipeline
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..12}; do
            RUN_ID=$(gh run list \
              --repo mahowlin/saif-ai-pod \
              --workflow ucs-pipeline.yaml \
              --limit 5 \
              --json databaseId,createdAt \
              --jq ".[] | select(.createdAt >= \"${{ steps.trigger.outputs.before }}\") | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              break
            fi
            echo "Run not found yet, waiting... ($i/12)"
            sleep 5
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered UCS run"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo mahowlin/saif-ai-pod --exit-status

  day1:
    name: "Day 1: OpenShift"
    needs: [validate, day0]
    if: always() && needs.validate.result == 'success' && (inputs.mode == 'full-deploy' || inputs.mode == 'day1-only') && (needs.day0.result == 'success' || needs.day0.result == 'skipped')
    runs-on: ubuntu-latest
    steps:
      - name: Trigger OpenShift Pipeline
        id: trigger
        run: |
          BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          echo "before=$BEFORE" >> $GITHUB_OUTPUT

          echo "Triggering OpenShift pipeline for ${{ inputs.cluster }}..."
          gh workflow run openshift-pipeline.yaml \
            --repo mahowlin/saif-ai-pod \
            -f cluster_name="${{ inputs.cluster }}" \
            -f cni_type="Cilium" \
            -f validate=true \
            -f deploy=true \
            -f post_install=true \
            -f test=true \
            -f force_deploy="${{ inputs.force_redeploy }}"

      - name: Wait for OpenShift Pipeline
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..12}; do
            RUN_ID=$(gh run list \
              --repo mahowlin/saif-ai-pod \
              --workflow openshift-pipeline.yaml \
              --limit 5 \
              --json databaseId,createdAt \
              --jq ".[] | select(.createdAt >= \"${{ steps.trigger.outputs.before }}\") | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              break
            fi
            echo "Run not found yet, waiting... ($i/12)"
            sleep 5
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered OpenShift run"
            exit 1
          fi

          echo "Watching run $RUN_ID (this may take 60-90 minutes)..."
          gh run watch $RUN_ID --repo mahowlin/saif-ai-pod --exit-status

  verify:
    name: "Verify: Apps Healthy"
    needs: [validate, day0, day1]
    if: always() && needs.validate.result == 'success' && (inputs.mode == 'full-deploy' || inputs.mode == 'day1-only') && needs.day1.result == 'success'
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Health Check
        id: trigger
        run: |
          BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          echo "before=$BEFORE" >> $GITHUB_OUTPUT

          echo "Triggering health test for ${{ inputs.cluster }}..."
          gh workflow run openshift-test.yaml \
            --repo mahowlin/saif-ai-pod \
            -f cluster_name="${{ inputs.cluster }}"

      - name: Wait for Health Check
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..12}; do
            RUN_ID=$(gh run list \
              --repo mahowlin/saif-ai-pod \
              --workflow openshift-test.yaml \
              --limit 5 \
              --json databaseId,createdAt \
              --jq ".[] | select(.createdAt >= \"${{ steps.trigger.outputs.before }}\") | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              break
            fi
            echo "Run not found yet, waiting... ($i/12)"
            sleep 5
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered test run"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo mahowlin/saif-ai-pod --exit-status

  summary:
    name: Summary
    needs: [validate, day0, day1, verify]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Generate Summary
        run: |
          echo "## Release Summary: ${{ inputs.cluster }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Stage | Status |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|--------|" >> $GITHUB_STEP_SUMMARY
          echo "| Validate | ${{ needs.validate.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Day 0 (UCS) | ${{ needs.day0.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Day 1 (OCP) | ${{ needs.day1.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Verify | ${{ needs.verify.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ needs.verify.result }}" == "success" ]; then
            echo "✅ **${{ inputs.cluster }} deployment successful**" >> $GITHUB_STEP_SUMMARY
          elif [ "${{ needs.day1.result }}" == "success" ]; then
            echo "⚠️ Deployment complete, verification pending" >> $GITHUB_STEP_SUMMARY
          else
            echo "❌ Deployment incomplete - check failed stages" >> $GITHUB_STEP_SUMMARY
          fi
```

### File 2: orchestrate-destroy.yaml

```yaml
name: Orchestrate Destroy

# PLATFORM 1.0 Destroy Workflow
#
# Complete cluster teardown:
# 1. OpenShift undeploy (removes kubeconfig)
# 2. UCS Delete (removes config from server AND Intersight)
#
# For multiple clusters, run this workflow multiple times.

on:
  workflow_dispatch:
    inputs:
      cluster:
        description: "Cluster to destroy"
        required: true
        type: choice
        options:
          - ai-pod-1
          - ai-pod-2
          - ai-pod-3
          - ai-pod-4
      confirm_destroy:
        description: "Type cluster name to confirm (e.g., ai-pod-3)"
        required: true
        type: string

env:
  GH_TOKEN: ${{ secrets.WORKFLOW_TRIGGER_TOKEN }}

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    steps:
      - name: Verify confirmation
        run: |
          if [ "${{ inputs.cluster }}" != "${{ inputs.confirm_destroy }}" ]; then
            echo "::error::Confirmation mismatch. Selected: '${{ inputs.cluster }}', Confirmed: '${{ inputs.confirm_destroy }}'"
            exit 1
          fi
          echo "✅ Destruction confirmed for ${{ inputs.cluster }}"

      - name: Validate GitHub token
        run: |
          if ! gh auth status &>/dev/null; then
            echo "::error::WORKFLOW_TRIGGER_TOKEN is invalid or missing"
            exit 1
          fi
          echo "✅ GitHub token valid"

      - name: Check sub-repo workflows exist
        run: |
          echo "Checking saif-ai-pod workflows..."
          gh workflow view openshift-undeploy.yaml --repo mahowlin/saif-ai-pod &>/dev/null || { echo "::error::openshift-undeploy.yaml not found"; exit 1; }
          gh workflow view ucs-pipeline.yaml --repo mahowlin/saif-ai-pod &>/dev/null || { echo "::error::ucs-pipeline.yaml not found"; exit 1; }
          echo "✅ Sub-repo workflows accessible"

      - name: Generate summary
        run: |
          echo "## ⚠️ Orchestrate Destroy: ${{ inputs.cluster }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Parameter | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-----------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Cluster | ${{ inputs.cluster }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Confirmed | ${{ inputs.confirm_destroy }} |" >> $GITHUB_STEP_SUMMARY

  undeploy:
    name: "Undeploy OpenShift"
    needs: validate
    runs-on: ubuntu-latest
    steps:
      - name: Trigger OpenShift Undeploy
        id: trigger
        run: |
          BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          echo "before=$BEFORE" >> $GITHUB_OUTPUT

          echo "Triggering OpenShift undeploy for ${{ inputs.cluster }}..."
          gh workflow run openshift-undeploy.yaml \
            --repo mahowlin/saif-ai-pod \
            -f cluster_name="${{ inputs.cluster }}" \
            -f confirm_destroy="${{ inputs.cluster }}" \
            -f clean_kubeconfig=true

      - name: Wait for OpenShift Undeploy
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..12}; do
            RUN_ID=$(gh run list \
              --repo mahowlin/saif-ai-pod \
              --workflow openshift-undeploy.yaml \
              --limit 5 \
              --json databaseId,createdAt \
              --jq ".[] | select(.createdAt >= \"${{ steps.trigger.outputs.before }}\") | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              break
            fi
            echo "Run not found yet, waiting... ($i/12)"
            sleep 5
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered undeploy run"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo mahowlin/saif-ai-pod --exit-status

  delete:
    name: "Delete UCS"
    needs: [validate, undeploy]
    runs-on: ubuntu-latest
    steps:
      - name: Trigger UCS Delete
        id: trigger
        run: |
          PROFILE="saif-${{ inputs.cluster }}"
          BEFORE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
          echo "before=$BEFORE" >> $GITHUB_OUTPUT

          echo "Triggering UCS Delete for $PROFILE..."
          gh workflow run ucs-pipeline.yaml \
            --repo mahowlin/saif-ai-pod \
            -f operation="Delete - Remove from server AND Intersight" \
            -f server_target="$PROFILE" \
            -f confirm_delete="$PROFILE"

      - name: Wait for UCS Delete
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..12}; do
            RUN_ID=$(gh run list \
              --repo mahowlin/saif-ai-pod \
              --workflow ucs-pipeline.yaml \
              --limit 5 \
              --json databaseId,createdAt \
              --jq ".[] | select(.createdAt >= \"${{ steps.trigger.outputs.before }}\") | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              break
            fi
            echo "Run not found yet, waiting... ($i/12)"
            sleep 5
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered UCS delete run"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo mahowlin/saif-ai-pod --exit-status

  summary:
    name: Summary
    needs: [validate, undeploy, delete]
    if: always()
    runs-on: ubuntu-latest
    steps:
      - name: Generate Summary
        run: |
          echo "## Destroy Summary: ${{ inputs.cluster }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Stage | Status |" >> $GITHUB_STEP_SUMMARY
          echo "|-------|--------|" >> $GITHUB_STEP_SUMMARY
          echo "| Validate | ${{ needs.validate.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Undeploy OCP | ${{ needs.undeploy.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Delete UCS | ${{ needs.delete.result }} |" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ needs.delete.result }}" == "success" ]; then
            echo "✅ **${{ inputs.cluster }} destroyed successfully**" >> $GITHUB_STEP_SUMMARY
          else
            echo "❌ **Destroy incomplete - check failed stages**" >> $GITHUB_STEP_SUMMARY
          fi
```

---

## My Specific Concerns (Please Validate)

### 1. Timestamp-Based Run Detection
I use `date -u +%Y-%m-%dT%H:%M:%SZ` before triggering, then filter runs by `createdAt >= timestamp`.

**Concerns:**
- Clock skew between GitHub runners?
- Is the timestamp format correct for GitHub's API?
- What if GitHub takes >60s to register the run (12 retries x 5s)?
- Race condition if someone else triggers the same workflow in the same second?

### 2. Job Dependency Logic
The `day1` job uses:
```yaml
if: always() && needs.validate.result == 'success' && (inputs.mode == 'full-deploy' || inputs.mode == 'day1-only') && (needs.day0.result == 'success' || needs.day0.result == 'skipped')
```

**Concerns:**
- Is `always()` actually needed here given the explicit result checks?
- Will this correctly handle `day1-only` mode where `day0` is skipped?
- Does `needs.day0.result == 'skipped'` work when the job wasn't in the dependency graph?

### 3. Cross-Repo Workflow Inputs
I'm passing inputs like:
```bash
-f operation="Delete - Remove from server AND Intersight"
-f force_deploy="${{ inputs.force_redeploy }}"
```

**Concerns:**
- Does the string `"Delete - Remove from server AND Intersight"` exactly match the workflow's choice options?
- Boolean handling: Does `${{ inputs.force_redeploy }}` pass as `true`/`false` string or actual boolean?
- Are all required inputs being passed?

### 4. Error Propagation
Using `gh run watch --exit-status` to propagate failure.

**Concerns:**
- Does this properly fail if any job in the watched workflow fails?
- What's the exit code behavior for cancelled/skipped workflows?
- If the watched workflow times out, what happens?

### 5. Destroy Workflow Safety
The destroy workflow requires typing the cluster name to confirm.

**Concerns:**
- Is string comparison `[ "${{ inputs.cluster }}" != "${{ inputs.confirm_destroy }}" ]` safe against injection?
- Should we add additional safeguards (e.g., prevent destroying all clusters at once)?

### 6. Missing Idempotency Checks
The release workflow doesn't check if a cluster is already healthy before deploying.

**Concerns:**
- Should `day0-only` skip if UCS is already activated?
- Should `day1-only` skip if OCP is already deployed?
- Is this the orchestrator's job or the sub-workflows' job?

### 7. Hardcoded Values
Several values are hardcoded:
- CNI type: `Cilium`
- Profile prefix: `saif-`
- Retry count: 12 x 5s = 60s

**Concerns:**
- Should any of these be configurable?
- Are the retry values appropriate?

---

## Questions for Reviewer

1. Is the timestamp-based run detection robust enough for production?
2. Are there GitHub Actions patterns I'm missing for cross-repo orchestration?
3. What failure modes am I not handling?
4. Is there a cleaner way to structure the job dependencies?
5. Any security concerns with the token permissions or input handling?

**Please be extra critical.** This will deploy bare-metal infrastructure and mistakes are expensive.

---

## Reviewer Feedback (AI)

### Key Risks / Issues

1. **High:** Run correlation is weak. Using `createdAt >= before` with `head -1` can select the wrong run if another dispatch happens around the same time or if GitHub queues the run longer than expected.
   - **Fix:** Pass a `request_id` input (UUID or `${{ github.run_id }}`) to sub-workflows and include it in their `run-name`, then filter runs by `displayTitle` and `actor`. Alternatively, use `workflow_call` to avoid manual polling.
2. **Medium:** The poll window is short (60s) and `--limit 5` is easy to overflow on a busy repo, which can cause false failures while the target workflow is still queued.
   - **Fix:** Increase the polling window/limit or poll by `status in (queued,in_progress)` until a max timeout.
3. **Medium:** Cross-repo runs use the target repo default branch implicitly, which can drift from the orchestrator version and execute unintended workflows.
   - **Fix:** Add a `pod_ref` input and pass `--ref`, or pin to a tag/commit for releases.
4. **Low (security):** The destroy confirmation comparison runs in a shell with user-provided values; command substitution like `$(...)` would execute.
   - **Fix:** Move the comparison into a workflow-level `if:` expression or compare via environment variables without shell expansion.
5. **Low:** No per-cluster concurrency guard; simultaneous deploy/destroy actions can interleave and corrupt state.
   - **Fix:** Add `concurrency` with a group keyed by `inputs.cluster`.

### Answers to Your Specific Concerns

- **Timestamp-based run detection:** The format is fine, but the approach is fragile under queueing and concurrent dispatches. A `request_id`-based correlation is much more robust.
- **Job dependency logic:** `always()` is needed here to allow `day1` to run when `day0` is skipped. `needs.day0.result == 'skipped'` works because `day0` is in `needs`.
- **Cross-repo inputs:** The string choice must match exactly. Boolean inputs are passed as strings; in sub-workflows, treat them with `if: inputs.force_deploy == 'true'` or `fromJSON(inputs.force_deploy)`.
- **Error propagation:** `gh run watch --exit-status` returns non-zero for failure/cancelled; if you need to distinguish, add `gh run view --json conclusion,status` and branch on the conclusion.
- **Destroy workflow safety:** In addition to safer comparison, consider `environment` protection with required reviewers for destructive operations.
- **Idempotency checks:** Prefer to keep idempotency inside the sub-workflows (they know the domain state). The orchestrator can optionally add a preflight to short-circuit obvious no-ops.
- **Hardcoded values:** Promote CNI type, profile prefix, poll interval/count, and repo names to inputs or top-level `env` defaults.

### Refactoring / Improvements

- Extract the repeated “dispatch → poll → watch” logic into a composite action or shared script to avoid copy/paste drift.
- Consider making the execution repo workflows reusable (`workflow_call`) so you can call them directly and get outputs without polling.
- Add `run-name` per workflow for easier traceability and use it for run correlation.
- Add `timeout-minutes` to long-running jobs and retry wrappers around `gh` commands to handle transient API failures.
- Use a GitHub App or fine-grained PAT with minimum permissions, and document the required scopes.
