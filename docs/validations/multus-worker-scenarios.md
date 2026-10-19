# Multus worker scenarios

`example-multus-a` is the Multus configuration this example actually renders. Each worker attaches to a tenant VLAN through Multus. DHCP on that VLAN supplies the guest address, prefix, gateway, DNS and MTU, and it is the only service that does so. The options appear in the render only because this profile asks for them. Guest Cilium advertises application Service prefixes to the tenant edge.

When you test a worker, look for one address owned by that virtual machine. The prefix, gateway, DNS and MTU have to come from the same lease. An address kept for a different worker does not pass.

The architecture is [HCP with Multus, DHCP and direct BGP](../architectures/HCP_MULTUS_DHCP.md).
The inputs are
`data/clusters/example-management/hcpclusters/example-multus-a.yaml`.
That file sets `workerNetworkProfileRef: multus-l2-dhcp` and
`applicationIngress.mode: direct-bgp-router`.

Render with the [integrated agent procedure](../../agents/integrated.md); the root README portable example does not emit hosted overlays. Inspect the `example-multus-a` overlays under
`generated/repos/secure-ai-gitops/`. Run the same commands again and confirm
`generated/` is byte-identical. These scenarios say what a reader should
test on a cluster created from that output, and what to observe. They are
not a record of a particular run.

## fresh-worker-boot

Scale or create one worker for `example-multus-a`. Watch the worker VM, the
DHCP lease and the hosted node.

Observe one address owned by that VM, with the prefix, gateway, DNS and MTU from that same lease, and the hosted node Ready. An address retained on a
different node does not satisfy this scenario.

## guest-dependencies

From that worker, resolve a name with DNS over UDP and over TCP, complete a
time-sync exchange, and pull a guest image from the example registry.

Observe an answer and a return for each exchange. A cached image, or a
request that never returns, is not enough.

## application-router

Read `applicationIngress` on `example-multus-a` for the router pool, VIP and
DNS wildcard. On the rendered cluster, confirm the BGP advertisement for
that VIP and a hostname under the wildcard.

Observe authoritative DNS, a request that validates the cluster CA, and a
return path. This is the Multus application router. It is separate from any
in-cluster load balancer on the management cluster.

## allow-and-deny

Apply one explicit allow and one explicit deny for a named flow, and collect
the packet together with the Hubble flow for the same tuple.

Observe the allow reaching its destination and the deny receiving no reply.
A denied flow that still gets a reply does not pass. A missing flow log, by
itself, does not pass.

## worker-move

The example render does not start a migration. A reader who moves one
`example-multus-a` worker uses an owned lease, one client on the same node
and one client on another node, and a checksum file on the moving worker's
root volume.

Observe the same checksum before and after the move, and both clients still
reaching the workload. A completed migration object without those checks
does not pass.

## persistent-probe

The example render does not enroll a traffic probe. A reader who adds one
places a receiver on one Multus worker, a Service with local external
traffic policy, a Route, a result volume and a low-rate scheduled probe.

Observe a local request to the receiver succeeding, and a CA-verified
request from that worker to the hosted API succeeding. A timeout on a
management API check, or on a Route that only hairpins inside the cluster,
is a separate observation. The hairpin does not prove that an external
client can reach the receiver.
