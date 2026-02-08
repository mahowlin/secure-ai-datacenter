# Review Request: SAIF Platform 1.0 Orchestrator - Post-Refactor

## Summary

This is a follow-up review after implementing fixes from the first review. The orchestrator now uses request_id correlation, concurrency guards, and improved polling. **Please review for consistency across all workflows.**

## Changes Since Last Review

| Issue | Fix Implemented |
|-------|-----------------|
| Run correlation weak | request_id UUID passed to sub-workflows, filter by displayTitle |
| Poll window too short | 30×10s = 5 minutes (was 12×5s = 60s) |
| Cross-repo ref drift | Added `target_ref` input with `--ref` flag |
| Shell injection in confirmation | Moved to workflow `if:` expression |
| No concurrency guard | Added `concurrency: { group: "cluster-${{ inputs.cluster }}" }` |
| Missing timeouts | All jobs have explicit `timeout-minutes` |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    saif-platform                         │
│                                                                 │
│  orchestrate-release.yaml ──┬── ucs-pipeline.yaml              │
│                             ├── openshift-pipeline.yaml        │
│                             └── openshift-test.yaml            │
│                                                                 │
│  orchestrate-destroy.yaml ──┬── openshift-undeploy.yaml        │
│                             └── ucs-pipeline.yaml (Delete)     │
└─────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      saif-ai-pod                              │
│                                                                 │
│  ucs-pipeline.yaml ─────────┬── ucs-validate.yaml              │
│                             ├── ucs-deploy.yaml                │
│                             └── ucs-test.yaml                  │
│                                                                 │
│  openshift-pipeline.yaml ───┬── openshift-validate.yaml        │
│                             ├── openshift-deploy.yaml          │
│                             ├── openshift-post-install.yaml    │
│                             └── openshift-test.yaml            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Files to Review

### 1. Orchestrator: orchestrate-release.yaml

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
      target_ref:
        description: "Target repo ref (branch/tag/sha)"
        required: false
        type: string
        default: "main"

# Prevent concurrent operations on the same cluster
concurrency:
  group: "cluster-${{ inputs.cluster }}"
  cancel-in-progress: false

env:
  GH_TOKEN: ${{ secrets.WORKFLOW_TRIGGER_TOKEN }}
  TARGET_REPO: mahowlin/saif-ai-pod

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    timeout-minutes: 5
    outputs:
      request_id: ${{ steps.setup.outputs.request_id }}
    steps:
      - name: Setup
        id: setup
        run: |
          REQUEST_ID=$(uuidgen | cut -d'-' -f1)
          echo "request_id=$REQUEST_ID" >> $GITHUB_OUTPUT
          echo "Generated request_id: $REQUEST_ID"

      - name: Validate GitHub token
        run: |
          if ! gh auth status &>/dev/null; then
            echo "::error::WORKFLOW_TRIGGER_TOKEN is invalid or missing"
            exit 1
          fi
          echo "✅ GitHub token valid"

      - name: Check sub-repo workflows exist
        run: |
          echo "Checking $TARGET_REPO workflows..."
          gh workflow view ucs-pipeline.yaml --repo $TARGET_REPO &>/dev/null || { echo "::error::ucs-pipeline.yaml not found"; exit 1; }
          gh workflow view openshift-pipeline.yaml --repo $TARGET_REPO &>/dev/null || { echo "::error::openshift-pipeline.yaml not found"; exit 1; }
          gh workflow view openshift-test.yaml --repo $TARGET_REPO &>/dev/null || { echo "::error::openshift-test.yaml not found"; exit 1; }
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
          echo "| Target Ref | ${{ inputs.target_ref }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Request ID | ${{ steps.setup.outputs.request_id }} |" >> $GITHUB_STEP_SUMMARY

  day0:
    name: "Day 0: UCS"
    needs: validate
    if: inputs.mode == 'full-deploy' || inputs.mode == 'day0-only'
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      REQUEST_ID: ${{ needs.validate.outputs.request_id }}
    steps:
      - name: Trigger UCS Pipeline
        run: |
          PROFILE="saif-${{ inputs.cluster }}"
          echo "Triggering UCS pipeline for $PROFILE (request_id: $REQUEST_ID)..."
          gh workflow run ucs-pipeline.yaml \
            --repo $TARGET_REPO \
            --ref "${{ inputs.target_ref }}" \
            -f operation="Activate - Apply config to server" \
            -f server_target="$PROFILE" \
            -f request_id="$REQUEST_ID"

      - name: Wait for UCS Pipeline
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          # Find run by request_id in displayTitle
          for i in {1..30}; do
            RUN_ID=$(gh run list \
              --repo $TARGET_REPO \
              --workflow ucs-pipeline.yaml \
              --limit 20 \
              --json databaseId,displayTitle,status \
              --jq ".[] | select(.displayTitle | contains(\"[$REQUEST_ID]\")) | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              echo "Found run $RUN_ID"
              break
            fi
            echo "Run not found yet, waiting... ($i/30)"
            sleep 10
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered UCS run with request_id $REQUEST_ID"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo $TARGET_REPO --exit-status

  day1:
    name: "Day 1: OpenShift"
    needs: [validate, day0]
    if: always() && needs.validate.result == 'success' && (inputs.mode == 'full-deploy' || inputs.mode == 'day1-only') && (needs.day0.result == 'success' || needs.day0.result == 'skipped')
    runs-on: ubuntu-latest
    timeout-minutes: 120
    env:
      REQUEST_ID: ${{ needs.validate.outputs.request_id }}
    steps:
      - name: Trigger OpenShift Pipeline
        run: |
          echo "Triggering OpenShift pipeline for ${{ inputs.cluster }} (request_id: $REQUEST_ID)..."
          gh workflow run openshift-pipeline.yaml \
            --repo $TARGET_REPO \
            --ref "${{ inputs.target_ref }}" \
            -f cluster_name="${{ inputs.cluster }}" \
            -f cni_type="Cilium" \
            -f validate=true \
            -f deploy=true \
            -f post_install=true \
            -f test=true \
            -f force_deploy="${{ inputs.force_redeploy }}" \
            -f request_id="$REQUEST_ID"

      - name: Wait for OpenShift Pipeline
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..30}; do
            RUN_ID=$(gh run list \
              --repo $TARGET_REPO \
              --workflow openshift-pipeline.yaml \
              --limit 20 \
              --json databaseId,displayTitle,status \
              --jq ".[] | select(.displayTitle | contains(\"[$REQUEST_ID]\")) | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              echo "Found run $RUN_ID"
              break
            fi
            echo "Run not found yet, waiting... ($i/30)"
            sleep 10
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered OpenShift run with request_id $REQUEST_ID"
            exit 1
          fi

          echo "Watching run $RUN_ID (this may take 60-90 minutes)..."
          gh run watch $RUN_ID --repo $TARGET_REPO --exit-status

  verify:
    name: "Verify: Apps Healthy"
    needs: [validate, day0, day1]
    if: always() && needs.validate.result == 'success' && (inputs.mode == 'full-deploy' || inputs.mode == 'day1-only') && needs.day1.result == 'success'
    runs-on: ubuntu-latest
    timeout-minutes: 15
    env:
      REQUEST_ID: ${{ needs.validate.outputs.request_id }}
    steps:
      - name: Trigger Health Check
        run: |
          echo "Triggering health test for ${{ inputs.cluster }} (request_id: $REQUEST_ID)..."
          gh workflow run openshift-test.yaml \
            --repo $TARGET_REPO \
            --ref "${{ inputs.target_ref }}" \
            -f cluster_name="${{ inputs.cluster }}" \
            -f request_id="$REQUEST_ID"

      - name: Wait for Health Check
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..30}; do
            RUN_ID=$(gh run list \
              --repo $TARGET_REPO \
              --workflow openshift-test.yaml \
              --limit 20 \
              --json databaseId,displayTitle,status \
              --jq ".[] | select(.displayTitle | contains(\"[$REQUEST_ID]\")) | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              echo "Found run $RUN_ID"
              break
            fi
            echo "Run not found yet, waiting... ($i/30)"
            sleep 10
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered test run with request_id $REQUEST_ID"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo $TARGET_REPO --exit-status

  summary:
    name: Summary
    needs: [validate, day0, day1, verify]
    if: always()
    runs-on: ubuntu-latest
    timeout-minutes: 5
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
          echo "**Request ID:** ${{ needs.validate.outputs.request_id }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ needs.verify.result }}" == "success" ]; then
            echo "✅ **${{ inputs.cluster }} deployment successful**" >> $GITHUB_STEP_SUMMARY
          elif [ "${{ needs.day1.result }}" == "success" ]; then
            echo "⚠️ Deployment complete, verification pending" >> $GITHUB_STEP_SUMMARY
          else
            echo "❌ Deployment incomplete - check failed stages" >> $GITHUB_STEP_SUMMARY
          fi
```

### 2. Orchestrator: orchestrate-destroy.yaml

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
      target_ref:
        description: "Target repo ref (branch/tag/sha)"
        required: false
        type: string
        default: "main"

# Prevent concurrent operations on the same cluster
concurrency:
  group: "cluster-${{ inputs.cluster }}"
  cancel-in-progress: false

env:
  GH_TOKEN: ${{ secrets.WORKFLOW_TRIGGER_TOKEN }}
  TARGET_REPO: mahowlin/saif-ai-pod

jobs:
  validate:
    name: Validate
    runs-on: ubuntu-latest
    timeout-minutes: 5
    # Safe confirmation check - no shell expansion
    if: inputs.cluster == inputs.confirm_destroy
    outputs:
      request_id: ${{ steps.setup.outputs.request_id }}
    steps:
      - name: Setup
        id: setup
        run: |
          REQUEST_ID=$(uuidgen | cut -d'-' -f1)
          echo "request_id=$REQUEST_ID" >> $GITHUB_OUTPUT
          echo "Generated request_id: $REQUEST_ID"
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
          echo "Checking $TARGET_REPO workflows..."
          gh workflow view openshift-undeploy.yaml --repo $TARGET_REPO &>/dev/null || { echo "::error::openshift-undeploy.yaml not found"; exit 1; }
          gh workflow view ucs-pipeline.yaml --repo $TARGET_REPO &>/dev/null || { echo "::error::ucs-pipeline.yaml not found"; exit 1; }
          echo "✅ Sub-repo workflows accessible"

      - name: Generate summary
        run: |
          echo "## ⚠️ Orchestrate Destroy: ${{ inputs.cluster }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "| Parameter | Value |" >> $GITHUB_STEP_SUMMARY
          echo "|-----------|-------|" >> $GITHUB_STEP_SUMMARY
          echo "| Cluster | ${{ inputs.cluster }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Confirmed | ${{ inputs.confirm_destroy }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Target Ref | ${{ inputs.target_ref }} |" >> $GITHUB_STEP_SUMMARY
          echo "| Request ID | ${{ steps.setup.outputs.request_id }} |" >> $GITHUB_STEP_SUMMARY

  confirmation-failed:
    name: "Confirmation Failed"
    runs-on: ubuntu-latest
    if: inputs.cluster != inputs.confirm_destroy
    steps:
      - name: Report Failure
        run: |
          echo "::error::Confirmation mismatch. Selected: '${{ inputs.cluster }}', Confirmed: '${{ inputs.confirm_destroy }}'"
          echo "## ❌ Confirmation Failed" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "You must type the exact cluster name to confirm destruction." >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY
          echo "- **Selected:** ${{ inputs.cluster }}" >> $GITHUB_STEP_SUMMARY
          echo "- **Typed:** ${{ inputs.confirm_destroy }}" >> $GITHUB_STEP_SUMMARY
          exit 1

  undeploy:
    name: "Undeploy OpenShift"
    needs: validate
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      REQUEST_ID: ${{ needs.validate.outputs.request_id }}
    steps:
      - name: Trigger OpenShift Undeploy
        run: |
          echo "Triggering OpenShift undeploy for ${{ inputs.cluster }} (request_id: $REQUEST_ID)..."
          gh workflow run openshift-undeploy.yaml \
            --repo $TARGET_REPO \
            --ref "${{ inputs.target_ref }}" \
            -f cluster_name="${{ inputs.cluster }}" \
            -f confirm_destroy="${{ inputs.cluster }}" \
            -f clean_kubeconfig=true \
            -f request_id="$REQUEST_ID"

      - name: Wait for OpenShift Undeploy
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..30}; do
            RUN_ID=$(gh run list \
              --repo $TARGET_REPO \
              --workflow openshift-undeploy.yaml \
              --limit 20 \
              --json databaseId,displayTitle,status \
              --jq ".[] | select(.displayTitle | contains(\"[$REQUEST_ID]\")) | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              echo "Found run $RUN_ID"
              break
            fi
            echo "Run not found yet, waiting... ($i/30)"
            sleep 10
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered undeploy run with request_id $REQUEST_ID"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo $TARGET_REPO --exit-status

  delete:
    name: "Delete UCS"
    needs: [validate, undeploy]
    runs-on: ubuntu-latest
    timeout-minutes: 30
    env:
      REQUEST_ID: ${{ needs.validate.outputs.request_id }}
    steps:
      - name: Trigger UCS Delete
        run: |
          PROFILE="saif-${{ inputs.cluster }}"
          echo "Triggering UCS Delete for $PROFILE (request_id: $REQUEST_ID)..."
          gh workflow run ucs-pipeline.yaml \
            --repo $TARGET_REPO \
            --ref "${{ inputs.target_ref }}" \
            -f operation="Delete - Remove from server AND Intersight" \
            -f server_target="$PROFILE" \
            -f confirm_delete="$PROFILE" \
            -f request_id="$REQUEST_ID"

      - name: Wait for UCS Delete
        run: |
          echo "Waiting for workflow to start..."
          sleep 10

          for i in {1..30}; do
            RUN_ID=$(gh run list \
              --repo $TARGET_REPO \
              --workflow ucs-pipeline.yaml \
              --limit 20 \
              --json databaseId,displayTitle,status \
              --jq ".[] | select(.displayTitle | contains(\"[$REQUEST_ID]\")) | .databaseId" | head -1)

            if [ -n "$RUN_ID" ]; then
              echo "Found run $RUN_ID"
              break
            fi
            echo "Run not found yet, waiting... ($i/30)"
            sleep 10
          done

          if [ -z "$RUN_ID" ]; then
            echo "::error::Could not find triggered UCS delete run with request_id $REQUEST_ID"
            exit 1
          fi

          echo "Watching run $RUN_ID..."
          gh run watch $RUN_ID --repo $TARGET_REPO --exit-status

  summary:
    name: Summary
    needs: [validate, undeploy, delete]
    if: always() && needs.validate.result == 'success'
    runs-on: ubuntu-latest
    timeout-minutes: 5
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
          echo "**Request ID:** ${{ needs.validate.outputs.request_id }}" >> $GITHUB_STEP_SUMMARY
          echo "" >> $GITHUB_STEP_SUMMARY

          if [ "${{ needs.delete.result }}" == "success" ]; then
            echo "✅ **${{ inputs.cluster }} destroyed successfully**" >> $GITHUB_STEP_SUMMARY
          else
            echo "❌ **Destroy incomplete - check failed stages**" >> $GITHUB_STEP_SUMMARY
          fi
```

---

## Sub-Workflow: request_id Integration

All sub-workflows in saif-ai-pod now accept `request_id` and include it in `run-name`:

### ucs-pipeline.yaml (header only)

```yaml
name: 'UCS Pipeline'
run-name: "UCS ${{ inputs.operation }} - ${{ inputs.server_target }}${{ inputs.request_id && format(' [{0}]', inputs.request_id) || '' }}"

on:
  workflow_dispatch:
    inputs:
      # ... other inputs ...
      request_id:
        description: 'Orchestrator correlation ID (for run tracking)'
        required: false
        type: string
        default: ''
```

### openshift-pipeline.yaml (header only)

```yaml
name: 'OpenShift Pipeline'
run-name: "OCP ${{ inputs.cluster_name }}${{ inputs.request_id && format(' [{0}]', inputs.request_id) || '' }}"

on:
  workflow_dispatch:
    inputs:
      # ... other inputs ...
      request_id:
        description: 'Orchestrator correlation ID (for run tracking)'
        required: false
        type: string
        default: ''
```

### openshift-test.yaml (header only)

```yaml
name: 'Test OpenShift Deployment'
run-name: "Test ${{ inputs.cluster_name }}${{ inputs.request_id && format(' [{0}]', inputs.request_id) || '' }}"

on:
  workflow_dispatch:
    inputs:
      # ... other inputs ...
      request_id:
        description: 'Orchestrator correlation ID (for run tracking)'
        required: false
        type: string
        default: ''
  workflow_call:
    inputs:
      # ... other inputs ...
      request_id:
        type: string
        required: false
        default: ''
```

### openshift-undeploy.yaml (header only)

```yaml
name: 'Undeploy OpenShift Cluster'
run-name: "Undeploy ${{ inputs.cluster_name }}${{ inputs.request_id && format(' [{0}]', inputs.request_id) || '' }}"

on:
  workflow_dispatch:
    inputs:
      # ... other inputs ...
      request_id:
        description: 'Orchestrator correlation ID (for run tracking)'
        required: false
        type: string
        default: ''
```

---

## Review Checklist

### Consistency Checks

1. **Input naming consistency:**
   - Orchestrator uses `cluster`, sub-workflows use `cluster_name` or `server_target`
   - Is this intentional? Should they match?

2. **request_id format:**
   - Orchestrator generates: `$(uuidgen | cut -d'-' -f1)` (8 chars)
   - Sub-workflows expect it in displayTitle as `[request_id]`
   - Is 8 characters sufficient for uniqueness?

3. **run-name format consistency:**
   - UCS: `"UCS ${{ inputs.operation }} - ${{ inputs.server_target }}[...]"`
   - OCP: `"OCP ${{ inputs.cluster_name }}[...]"`
   - Test: `"Test ${{ inputs.cluster_name }}[...]"`
   - Undeploy: `"Undeploy ${{ inputs.cluster_name }}[...]"`
   - Are these searchable/filterable enough?

4. **Timeout alignment:**
   - Orchestrator day1 timeout: 120min
   - openshift-pipeline.yaml internal timeout: ?
   - Could the inner workflow timeout before the orchestrator?

5. **Error propagation:**
   - Sub-workflow fails → `gh run watch --exit-status` returns non-zero → orchestrator job fails
   - Is this chain reliable?

### Security Checks

1. **Token scope:**
   - `WORKFLOW_TRIGGER_TOKEN` needs `workflow` scope
   - Does it also need `actions:read` for `gh run list`?

2. **Input validation:**
   - Orchestrator validates cluster names via `type: choice`
   - Sub-workflows also validate - is double validation needed?

3. **Confirmation bypass:**
   - destroy workflow uses `if: inputs.cluster == inputs.confirm_destroy` at job level
   - Can this be bypassed via API?

### Edge Cases

1. **Concurrent dispatches:**
   - Same cluster: blocked by `concurrency` group
   - Different clusters: allowed to run in parallel
   - What if same request_id is somehow reused?

2. **Sub-workflow already running:**
   - User manually triggers sub-workflow
   - Orchestrator triggers same workflow
   - Both have different request_ids - will polling find the right one?

3. **Partial failure recovery:**
   - Day 0 succeeds, Day 1 fails
   - Re-run with `day1-only` mode
   - Does this work correctly?

4. **Target ref mismatch:**
   - Orchestrator on `main`
   - Target ref set to `feature-branch`
   - Sub-workflow on `feature-branch` has different inputs
   - Will the `-f` flags still work?

---

## Questions for Reviewer

1. Is the request_id correlation approach robust enough?
2. Are there any race conditions I'm missing in the run detection logic?
3. Is the timeout hierarchy (orchestrator vs sub-workflow) correct?
4. Any concerns with the concurrency group naming scheme?
5. Should sub-workflows also have concurrency guards?
6. Is there a better pattern for the dispatch-poll-watch cycle?

**Please be thorough.** This deploys bare-metal infrastructure.

---

## Reviewer Feedback (AI)

### Key Risks / Gaps

1. **Run correlation still fragile with short IDs.** The 8-char `request_id` can collide and `contains("[id]")` could match unintended runs.
   - **Suggestion:** Use full UUID or `${{ github.run_id }}` and tag run-name as `rid=<id>` for exact matching.
2. **Polling list size is still bounded.** `--limit 20` can miss the run on busy repos; you may fail while the workflow is queued.
   - **Suggestion:** Increase `--limit` (e.g. 100) and/or use `gh api` with `per_page`, or extend the polling window based on queue depth.
3. **No actor/event filtering.** If an ID is reused (rare but possible), a run triggered by another user could be matched.
   - **Suggestion:** Filter by `actor` and `event == "workflow_dispatch"` in the `jq` filter.
4. **Timeout hierarchy not verified.** Orchestrator timeouts are set, but sub-workflow timeouts aren’t documented here; a shorter inner timeout will still fail the orchestrator.
   - **Suggestion:** Document or align the inner timeouts and surface them in the summary.
5. **Cross-repo schema drift still possible.** `target_ref` helps, but a ref with different inputs will fail at runtime.
   - **Suggestion:** Add a preflight that validates inputs via `gh workflow view --json inputs` and asserts required names/types.

### Improvements / Refactors

- Consider switching to `workflow_call` in all sub-workflows (not just `openshift-test.yaml`) to remove polling and return explicit outputs.
- Extract the dispatch/poll/watch logic to a composite action or shared script to avoid divergence.
- Add retries around `gh workflow run` and `gh run list` for transient API failures.
- Add top-level `permissions` with minimum `contents: read` to avoid accidental use of default `GITHUB_TOKEN`.

### Quick Responses to Your Checklist

- **Input naming consistency:** It’s workable, but if you keep mixed names, document mapping explicitly (cluster → cluster_name/server_target).
- **request_id format:** 8 chars is likely fine in low volume, but full UUID or `github.run_id` is safer and still readable.
- **run-name format:** Searchable; recommend prefixing with `rid=` to avoid substring collisions.
- **Timeout alignment:** Check sub-workflow timeouts and ensure orchestrator timeouts exceed them.
- **Error propagation:** `gh run watch --exit-status` is fine; add a `gh run view --json conclusion` if you need nuanced handling.
