# Direct-worker BGP scaling model

This is a planning model for the selected Multus attachment, not a measured capacity result or a vendor limit. [The networking composition](../architectures/HCP_MULTUS_DHCP.md) and maintained networks determine the relationships being counted.

## Selected attachment and owners

The management cluster advertises hosted control-plane Service VIPs through its selected host VLAN interfaces. Hosted control-plane pods remain on management networking. Each guest worker uses one Multus bridge attachment, with `attachDefaultNetwork: false` and `guestDefaultRoute: true`: DHCP on that tenant network supplies its address, gateway, DNS and MTU. The guest does not retain an extra default-route NIC on the management pod network.

Guest Cilium peers directly with the tenant fabric for its own selected Service advertisements and route-import policy. It does not share the management BGP routing table. Any dual-NIC management/default design would be a different, separately declared composition.

The current inputs select `example-multus-a` and `example-shared` on the same `tenant-b-workers` attachment and shared Tenant-B control-plane peering network. A hosted-cluster count is therefore not a count of distinct VLANs, L3Outs, SVIs or VRFs. The parent workers phase selects five and three workers respectively; these are desired replicas, not observed running peers.

## Count actual relationships

| Quantity | Count from |
|---|---|
| VLANs and subnets | Distinct selected network identities, not one pair per hosted cluster |
| VRFs, BDs, L3Outs and floating SVIs | The distinct tenant/network/fabric relationships in the catalog |
| Management sessions | Management nodes × selected control-plane peering networks × leaf peers on each network |
| Guest sessions | Guest workers on each selected direct-peering network × that network's leaf peers |
| Advertised paths | Prefix owners × actual advertising nodes × peer sessions; separate ECMP paths from unique prefixes |
| Address pools | Distinct ingress/egress pool references, not a fixed number per cluster |
| Worker capacity | Declared VM sizing, disks, management workload reserve, failure reserve and actual hardware |

For a hypothetical network with three management nodes, eight guest workers and two leaf peers, there are six management and sixteen guest sessions (eleven sessions per leaf). Sharing that network across two clusters does not double its VLAN objects. Adding a new network/VRF changes the fabric and session count; adding workers to an existing network changes guest sessions and compute/storage demand.

## Bound an operating unit

Choose an operating unit by resource ownership, desired failure domain and measured headroom. Evaluate per-leaf route paths, prefix filters, ECMP, BFD timers/churn, import policy, maintenance behavior and controller convergence as well as session counts. Keep IP allocation and overlapping tenant address spaces explicit. A table of estimated peers cannot establish a supported leaf limit or a safe cluster count.

Record the exact hardware/software versions, scale profile, configured limits and test evidence before making a capacity claim. This repository has no version-qualified proof for a numeric ACI neighbor/BFD ceiling or the former large-cluster recommendations. [Validation notes](README.md) distinguish desired-state checks from runtime results.
