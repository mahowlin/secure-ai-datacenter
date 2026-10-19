# External pull for the management registry

`LocalRegistry.spec.externalPull` is optional across profiles. The compact management example declares it for the active Multus subject: VIP198.18.13.20 in the tenant-B management-services pool, port443 to listener8443. The synthetic address still needs a real site reservation before apply. Its render adds a LoadBalancer Service in front of the existing registry. Workers can then pull images from a name that resolves to that address. The path does not use the control-plane API router or a custom image.

You have to set every field below. The address has to sit in the services subnet you name, and it cannot be a control-plane virtual IP. The renderer checks that shape. It cannot prove the address is free on the network.

| Input | What it sets |
|---|---|
| `enabled` | Off leaves the current render unchanged. |
| `name` | Name of the Service, the pool and the advertisement. |
| `networkRef`, `poolRef` | The management BGP network and the subnet the address comes from. |
| `vip` | The address. There is no fallback if you omit it. |
| `hostnamePrefix` | DNS name `<prefix>.<cluster>.<base domain>`, plus aliases for the hosted clusters you select. |
| `servicePort`, `listenerPort`, `externalTrafficPolicy` | The external TLS port, the nginx listener, and how the Service forwards. |
| `tlsSecret` | A reference to the certificate. The certificate itself is supplied outside this tree. |
| `pullClientSecret` | A reference to the pull credential. The renderer does not create one. |
| `consumer` | Which hosted clusters inherit the mirror URL, the pull secret and the extra trust bundle. |
| `workloadName`, `listeners` | The existing registry workload and its internal ports. |

DNS names come from each hosted cluster's `applicationIngress`. The primary name and the aliases share one frontend. This example uses the VLAN 2113 routing domain and the BGP peers already declared there. Choosing another network means choosing another address and another frontend.

A worker resolves the name to the registry address. Management BGP advertises that `/32`. The Service forwards TLS to an extra listener on the existing nginx container. That listener proxies `/v2/` to the registry and leaves the sync listener alone. The original internal pull listener keeps its own certificate.

`external-runtime-contract.yaml` lists the DNS records and the certificate names the deployer has to satisfy. It does not publish DNS or write a certificate.

`tests/test_registry_external_pull.py` checks the shape with documentation addresses. It does not prove that a worker can pull an image.

The example runtime owner supplies the declared DNS names, certificate, pull credential and additive trust bundle before consumer activation. Inspect generated `external-runtime-contract.yaml` for exact names. The image content must be seeded before bootstrap; exposing an empty registry or merely rendering this contract does not establish a working pull.

## Content activation

A frontend is not seeded content. The selected example defaults to the preceding registry. Optional production and HostedCluster/worker activation use the [selected inventory and current reader check](architectures/HCP_CACHE_LIFECYCLE.md); changing this endpoint or its reader/trust inputs invalidates that evidence.
