# Composition templates

A template sits between resolved platform data and the file a target system expects. They live here so you can review the shape next to the inputs that fill it.

## Structure

```
templates/
  secure-ai-compute/
    openshift/
      install-config.yaml.j2    # OpenShift agent-based installer config
      agent-config.yaml.j2      # Node NIC/IP/DNS/NTP config
    ucs/
      vlan.ezi.yaml.j2          # UCS VLAN policy
      ethernet_network_group.ezi.yaml.j2  # UCS trunk VLAN assignments

  secure-ai-gitops/
    clusters/
      application.yaml.j2       # Argo CD Application wiring primitives
      kustomization.yaml.j2     # Cluster entrypoint/resource indexes
    apps/
      hcp/
        management/             # Parent/compact management app-type primitives
        tenant/                 # Hosted-cluster app-type primitives
```

GitOps templates are selective. Use one when the YAML shape is stable and worth reading. The rendered file still lands under `generated/repos/secure-ai-gitops/` and is transported into the target repo as `clusters/` and `apps/`.

## How a template is taken from generated output

1. Start from generated YAML that already matches the target.
2. Copy it to `templates/` as a `.j2` file, still identical to that output.
3. Replace the concrete values with Jinja variables.
4. Render it from the resolved platform.
5. Diff the result against the original generated YAML. A difference has to be intentional.

```bash
python scripts/render-ocp.py --cluster example-management --output /tmp/rendered
diff -r generated/repos/secure-ai-compute/data/openshift/example-management/ /tmp/rendered/example-management/
```

## Rules

Templates produce target-native shapes: EZI YAML for UCS, install-config for OpenShift. They do not emit a generic handoff object.

If the resolver did not provide a required value, the template fails. Do not hide that with a Jinja `default()`.

The resolver validates first. A template can assume the structure is sound.

One template produces one target artifact.

Use a GitOps template where ownership is clear: an Argo CD `Application`, a `Kustomization`, an OLM operator triplet, a HostedCluster or NodePool primitive, a hook Job and its RBAC, a CiliumConfig primitive, or IDMS Application wiring.

Do not hide desired state in a GitOps template. Interface names, devices, VIPs, ASNs, BGP timers, source interfaces, operator channels and images, hook images, sync waves, NodePool sizing and storage, CiliumConfig values, the IDMS bundle, and tenant datapath policy come from the contract, the defaults or the resolver.

Future HCP GitOps templates follow `apps/hcp/management/<app-type>/{base,overlays/<cluster>}` and `apps/hcp/tenant/<app-type>/{base,overlays/<hosted-cluster>}`.

Python builders stay valid for payloads that fan out, including BGP, LB-IPAM, Egress Gateway, CLIFE bundle ingestion and generated kustomization indexes. They still write into those app-type overlay paths.

ACI templates stay with the fabric target. `secure-ai-fabric` owns its reusable execution design. This repository renders the composition-varying data.
