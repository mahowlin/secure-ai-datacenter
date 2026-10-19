# Preparation capability boundaries

| Selection | Canonical input / consumer | Local evidence | Remaining prerequisite |
|---|---|---|---|
| Declared third-party bare metal | examples/standalone, OCPCluster + NetworkCatalog + release/defaults -> compute | five-file deterministic output, archive/YAML and selected-policy negative tests | entitled archive/auth, real hardware/CA, pipeline ISO/boot/readiness |
| UCS + ACI composition | data/clusters + networks -> compute/fabric | generated output and manifest equality; NAC wrapper validate only | live inventory, authorized pipeline plan/rollout, no inferred physical compatibility |
| Hosted clusters | hosted configuration -> existing management/tenant GitOps overlays | actual child Helm/Kustomize source builds where private enrichment is not required | registered hosted API, CA/secret overlays, storage/lifecycle guards and actual convergence |
| Isovalent/observability | explicit release images/charts/profile + rendered Applications | exact selected source paths and offline builds; declared sizing arithmetic | supported installed tuple, actual exporter/producer measurements and end-to-end events |
| Mirrored image chain | canonical delivery scopes + release inventory -> airgap receipts/compute validation | synthetic receipt/archive tests and changed-input preflight | real source/target digest/auth/trust/transfer and per-model guest pulls |
| Mirror operator | optional LocalRegistry `ocMirror` -> pinned v0.1.7 OLM package, generated GitOps and exported airgap producer contract | offline input/closure/namespace/target checks and cross-repo equality; manual activation, no embedded credentials/CA | activation held: dedicated scoped-writer protocol/custody, monitoring and IDMS ordering; copy/reconcile, TLS consumer access, retained-content/restart recovery and support remain unqualified |
| Portworx | storage nodes/disks/class and lifecycle inputs -> storage manifests | synthetic unique device paths and retained staging/cutover guards | real inventory, installation/licence, data/HA testing and consumer acceptance |

Public example addresses, disks and measurements are not deployment evidence. Feature existence in source is not readiness. The generic starter omits unrelated HCP, conformance and Day2 inventory; enabling an optional feature requires its real prerequisites. `secure-ai` is the intended release branch; no public repository/default setting is changed here.
