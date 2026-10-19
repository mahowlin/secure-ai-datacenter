# Render the optional integrated composition

Use the requirements environment in [AGENTS](../AGENTS.md), from the Datacenter checkout root. These existing renderers consume the selected maintained inputs and write generated review artifacts locally. They do not commission hardware, apply ACI or sync a cluster.

The selected integrated profile includes the optional operator inspection input.
Complete [verified external-input preparation](mirror-operator.md#prepare-the-selected-external-operator-input)
in this same input root before these commands. A disconnected runner imports the
prepared cache; connected acquisition is an explicit preparation step, never a
renderer download. The renderer-checks pipeline stages it automatically.

```bash
python scripts/render-aci.py
python scripts/render-ucs.py
python scripts/render-ocp.py
python scripts/render-gitops.py
python scripts/write-artifact-manifest.py
```

Inspect each target path and verify the exact manifest against the consumer before a separately authorized import. Repeat into independent scratch outputs to compare bytes. Follow [generated-file validation](generated-artifacts.md), [Fabric staging](fabric.md) and the downstream owner procedures. Root import is consequential: the selected `workers` lifecycle Application is automated; Portworx is manual. Resolve private bootstrap/CA, controller and storage prerequisites before any runtime sync.

To change selected hosted replicas, update parent `day2GitOps.hostedClusterLifecycle.workerReplicasByCluster` (or its parent fallback), update the tenant staged-rollout checkpoints to include the intended value (for three workers, `[0, 1, 3, 5]` retains current bounds), then render and inspect `nodepool-workers.yaml`. A tenant-only replica edit is ineffective while the parent override remains.
