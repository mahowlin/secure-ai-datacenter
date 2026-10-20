# Simplified cluster inputs — foundation proposal

**Status:** Design for review; no renderer, schema or deployment changes.

**Baseline:** Secure AI Datacenter `85c9684ef894683ddbcebfb4da075637079a916a`.

**Coordination:** `isovalent-7loj.34`; foundation snapshot `01a159e6-0382-7904-af03-f8902e3f8430`, incorporating participant-selection and bond-mode correction `01a159e9-c619-79fe-b3d0-680d0bab10bf`.

## Overview

Read a server's bonds.bond0 block: it contains its NIC identities and leaf ports, actual bond mode/primary, untagged management address and optional tagged peering address. Each attached network is one direct meaningful name in this SAME file. That shared network contains its allocation and corresponding ACI intent once. There is no separate hostNetworking map, per-node peering-IP map, physical-inventory file or index to reconstruct the link.
Three active-backup bonds use independent ACI access ports, NOT an undeclared cross-leaf LACP/vPC. Native management VLAN 1701 carries node/API/apps/bootstrap and the single default route. Tagged VLAN 2111 establishes an optional directly connected enterprise BGP session set only. No advertised VIP, imported route, off-link service range or forwarding workaround is taught by this example. Removing service-peering and each bond's tagged block leaves the native-management-only model. A standalone site may also select existing/external fabric without ACI generation; this snapshot deliberately selects MANAGED ACI to show correspondence.

## 1. Complete proposed cluster.yaml

Synthetic documentation topology, NOT customer allocations or a live deployment. Commissioned APIC/pod/leaves, functioning external site-management-uplink and site-management-services contract are explicit prerequisites outside cluster commissioning. Cisco CX supplies those facts and validates upstream routing/contracts; generated leaf/access/BD/EPG/L3Out intent below is in scope. APIC fabric ASN is an observed commissioning fact to validate, not permission to change fabric-wide ASN. Exact site MAC/disk/interface/port values and DNS reservations must replace examples.

```yaml
version: 1
cluster:
  name: demo-mgmt
  baseDomain: example.com
  deployment: compact
  release: saif-ocp-4.19-cilium-1.19.7-stable
  apiVIP: 192.0.2.20
  ingressVIP: 192.0.2.21
services:
  dns:
  - 198.19.3.188
  - 198.19.3.189
  ntp:
  - 198.19.49.151
images:
  delivery: connected
  trust: additional-ca
  repositories:
    openshiftRelease: registry.example.com:5000/openshift/release-images
    openshiftContent: registry.example.com:5000/openshift/release
fabric:
  ownership: managed
  apic: https://apic.example.invalid
  username: site-automation-user
  pod: 1
  asn: 64512
networks:
  management:
    vlan: 1701
    cidr: 192.0.2.0/24
    gateway: 192.0.2.1
    mtu: 1500
    aci:
      tenant: platform-management
      vrf: Management
      existingL3Out: site-management-uplink
      existingContract: site-management-services
  service-peering:
    vlan: 2111
    cidr: 198.18.11.0/28
    mtu: 1500
    aci:
      tenant: platform-services
      vrf: VRF-1
      sharedAddress: 198.18.11.3
      pathFloatingAddress: 198.18.11.4
      leaves:
        103:
          address: 198.18.11.1
          routerID: 10.255.103.1
        104:
          address: 198.18.11.2
          routerID: 10.255.104.1
    bgp:
      localASN: 64711
      advertisements: none
      importRoutes: false
hosts:
- name: bm-node-1
  bootDiskSerial: EXAMPLE-BOOT-0001
  bootstrap: true
  bonds:
    bond0:
      mode: active-backup
      primary: eno1
      native:
        network: management
        address: 192.0.2.10
      tagged:
        service-peering: 198.18.11.10
      members:
        eno1:
          mac: 02:00:5e:00:53:00
          leaf: 103
          port: eth1/1
        eno2:
          mac: 02:00:5e:00:53:01
          leaf: 104
          port: eth1/1
- name: bm-node-2
  bootDiskSerial: EXAMPLE-BOOT-0002
  bonds:
    bond0:
      mode: active-backup
      primary: eno1
      native:
        network: management
        address: 192.0.2.11
      tagged:
        service-peering: 198.18.11.11
      members:
        eno1:
          mac: 02:00:5e:00:53:02
          leaf: 103
          port: eth1/2
        eno2:
          mac: 02:00:5e:00:53:03
          leaf: 104
          port: eth1/2
- name: bm-node-3
  bootDiskSerial: EXAMPLE-BOOT-0003
  bonds:
    bond0:
      mode: active-backup
      primary: eno1
      native:
        network: management
        address: 192.0.2.12
      tagged:
        service-peering: 198.18.11.12
      members:
        eno1:
          mac: 02:00:5e:00:53:04
          leaf: 103
          port: eth1/3
        eno2:
          mac: 02:00:5e:00:53:05
          leaf: 104
          port: eth1/3
privateFiles:
  pullSecret: private/pull-secret.json
  sshPublicKey: private/ocp-install.pub
  registryCA: private/registry-ca.pem
  apicPassword: private/apic-password
  apicCA: private/apic-ca.pem
```
This is 125 non-comment lines including managed-fabric/leaf-port facts and optional tagged peering. It intentionally spends lines keeping each physical-to-logical attachment understandable. No new percentage reduction claim comparing this larger managed-ACI scope with the old standalone scope. Customer uses one file rather than the copied cluster/network/default/release tree; usability and architectural consistency are the acceptance criteria. tenants.yaml remains optional later, absent here. No template/schema copy into the customer directory.

Required service prerequisites: api.demo-mgmt.example.com and api-int resolve to192.0.2.20; *.apps.demo-mgmt.example.com resolves to192.0.2.21; time/DNS/image services reachable through192.0.2.1; entitled registry and official CLife access; matching installer/hardware support; no existing IP/MAC/port ownership conflict. These are preflight observations, not constants silently assumed by the resolver. Actual host NIC names correspond to declared MACs; BMC/media boot stays site-owned. APIC password/CA and pull-secret/SSH/registry-CA are private references, never generated browser/public data. Managed ACI apply uses the owning pipeline and private trust mechanism with TLS verification enabled.

## 2. One readable resolved result (illustrative, not current-renderer output)

| Server | Physical attachment -> Linux bond | Native management | Tagged peering | Route/BGP |
|---|---|---|---|---|
| bm-node-1 | eno1 MAC...00 -> pod1/leaf103 eth1/1; eno2 MAC...01 -> leaf104 eth1/1; both bond0, active-backup primary eno1 | bond0=192.0.2.10/24, untagged1701 | bond0.2111=198.18.11.10/28 | default via192.0.2.1 on bond0; AS64711 -> leaf103 .1 and104 .2 AS64512 |
| bm-node-2 | eno1 MAC...02 -> leaf103 eth1/2; eno2 MAC...03 -> leaf104 eth1/2; both bond0, active-backup primary eno1 | bond0=192.0.2.11/24, untagged1701 | bond0.2111=198.18.11.11/28 | same native default; peering source.11 |
| bm-node-3 | eno1 MAC...04 -> leaf103 eth1/3; eno2 MAC...05 -> leaf104 eth1/3; both bond0, active-backup primary eno1 | bond0=192.0.2.12/24, untagged1701 | bond0.2111=198.18.11.12/28 | same native default; peering source.12 |
All host IP interfaces MTU 1500; API/appsVIPs .20/.21 belong to the native management subnet. Native VLAN is a switch/EPG property; NO bond0.1701 interface. The tagged network adds only a connected198.18.11.0/28 route, no second default. BGP creates 6 sessions, advertisements none/import disabled; it does not enable forwarding or program an extra Linux default route merely by establishing a session.

Effective policy is shown beside this result, not a requirement to open defaults:
- Compact:3 control-plane/workload nodes, 0 separate workers; bm-node-1 rendezvous192.0.2.10; all nodes rolemaster.
- Maintained Cilium base matches the reviewed starter: tunnel/VXLAN4789, kube-proxy replacement, CNI paths and cluster-pool policy; pod10.192.0.0/14 /23 blocks, service172.19.0.0/16. Exact extracted policy and provenance visible, override required on conflict/overlap.
- Active-backup option miimon100 comes from maintained policy; primary and member identities are customer facts. No LACP settings.
- Naming policy derives BD-management, OpenShift/management EPG, demo-mgmt-hosts physical domain/AAEP, demo-mgmt-hosts-vlans pool and corresponding access policy group/profile names. Derived names and policy revision appear in the result; customer can override legitimate pre-existing identity collisions explicitly.
- Both VLANs in the same access pool; SAME AAEP/access policy group owns a given physical port. Physical/routed domains attached through that group. No competing management and tenant policy group assignment.
- Optional BGP uses IPv4 unicast, reviewed5/9/3 timers, no BFD/GR or route-import feature selected for this simple example, no advertisements. Those operational choices live in explicit versioned policy, not renderer literals. Enterprise BGP enablement is release-checked; do not copy unrelated EGW/GatewayAPI feature approvals.
- Selected release resolves recorded OCP4.19.21/Cilium1.19.7 pinned images and CLife archive/checksum. Current release data is render-only evidence and lacks a complete trusted OCP payload-digest record: obtaining/verifying that record, matching release architecture and exact installed/chart feature prerequisites are IMPLEMENTATION/PREPARATION prerequisites. No guessed payload digest, entitlement or release qualification. Site repository mappings select destinations only; receipt/content must match the single release authority.

## 3. Representative corresponding outputs — all ILLUSTRATIVE TARGET OUTPUT, not rendered by current code

Compute's generated agent-config.yaml uses the following full networkConfig for bm-node-1 (its host record also has explicit NIC/MAC mapping and rootDeviceHints.serialNumber from input). Other two substitute their own addresses/MACs only:
```yaml
networkConfig:
  interfaces:
  - name: eno1
    type: ethernet
    state: up
  - name: eno2
    type: ethernet
    state: up
  - name: bond0
    type: bond
    state: up
    mtu: 1500
    link-aggregation:
      mode: active-backup
      options:
        miimon: '100'
        primary: eno1
      port: [eno1, eno2]
    ipv4:
      enabled: true
      dhcp: false
      address:
      - ip: 192.0.2.10
        prefix-length: 24
    ipv6:
      enabled: false
  - name: bond0.2111
    type: vlan
    state: up
    mtu: 1500
    vlan:
      base-iface: bond0
      id: 2111
    ipv4:
      enabled: true
      dhcp: false
      address:
      - ip: 198.18.11.10
        prefix-length: 28
    ipv6:
      enabled: false
  dns-resolver:
    config:
      server: [198.19.3.188, 198.19.3.189]
  routes:
    config:
    - destination: 0.0.0.0/0
      next-hop-address: 192.0.2.1
      next-hop-interface: bond0
      table-id: 254
```
Compute install-config machineNetwork=192.0.2.0/24, API192.0.2.20, ingress192.0.2.21; no service-peering address becomes an API VIP, machineNetwork or default gateway. Metadata/image mappings/raw manifest and private enrichment remain existing execution boundaries. Do not create a Day2 controller that also owns/reset this shared bond: initial interface ownership is the Agent network config. Later updates require a separately supported procedure.

Representative NAC-ACI managed management tenant excerpt (same key shape as existing templates/aci/management.yml.j2):
```yaml
apic:
  tenants:
  - name: platform-management
    vrfs:
    - name: Management
    bridge_domains:
    - name: BD-management
      vrf: Management
      l3outs: [site-management-uplink]
      subnets:
      - ip: 192.0.2.1/24
    application_profiles:
    - name: OpenShift
      endpoint_groups:
      - name: management
        bridge_domain: BD-management
        physical_domains: [demo-mgmt-hosts]
        contracts:
          consumers: [site-management-services]
          providers: [site-management-services]
        static_ports:
        - {pod_id: 1, node_id: 103, module: 1, port: 1, vlan: 1701, mode: untagged}
        - {pod_id: 1, node_id: 104, module: 1, port: 1, vlan: 1701, mode: untagged}
        - {pod_id: 1, node_id: 103, module: 1, port: 2, vlan: 1701, mode: untagged}
        - {pod_id: 1, node_id: 104, module: 1, port: 2, vlan: 1701, mode: untagged}
        - {pod_id: 1, node_id: 103, module: 1, port: 3, vlan: 1701, mode: untagged}
        - {pod_id: 1, node_id: 104, module: 1, port: 3, vlan: 1701, mode: untagged}
```
This is generated BD/EPG/port correspondence, not just a handoff description. Existing L3Out and contract are referenced, not re-created or guessed. Effective source/output mapping additionally identifies these generated access objects:
| Generated ACI object | Exact effective content |
|---|---|
| demo-mgmt-hosts-vlans | Static ranges1701 and2111, one allocation per input network |
| demo-mgmt-hosts physical domain | That same pool; management EPG attaches to it |
| demo-mgmt-service-peering routed domain | That pool; service L3Out uses it |
| demo-mgmt-hosts AAEP | Both domains; one demo-mgmt-hosts-access access policy group |
| leaf103 interface profile | Port selectors eth1/1 bm-node-1/eno1; eth1/2 bm-node-2/eno1; eth1/3 bm-node-3/eno1; same policy group |
| leaf104 interface profile | Corresponding eno2 ports1/2/3; same policy group |
| leaf103/104 switch profiles | Select actual nodes103/104 and respective interface profiles |
| platform-services:VRF-1, L3Out service-peering | Node profiles103 routerID10.255.103.1 and104 routerID10.255.104.1; floatingSVI VLAN2111 MTU 1500 |

Representative leaf103 L3Out interface excerpt using existing templates/aci/tenant.yml.j2 keys:
```yaml
svi: true
floating_svi: true
node_id: 103
vlan: 2111
ip: 198.18.11.1/28
ip_shared: 198.18.11.3/28
mtu: 1500
paths:
- physical_domain: demo-mgmt-hosts
  floating_ip: 198.18.11.4/28
bgp_peers:
- {ip: 198.18.11.10/32, remote_as: 64711, local_as: 64512}
- {ip: 198.18.11.11/32, remote_as: 64711, local_as: 64512}
- {ip: 198.18.11.12/32, remote_as: 64711, local_as: 64512}
```
Leaf104 has node_id104, ip198.18.11.2/28, same shared/path addresses and actual host peers. Exact host /32 peer matches derive from host attachments; no duplicate hand-maintained peer-IP list and no wildcard acceptance of any future address in the /28. Remaining NAC security/protocol fields come from explicitly reviewed maintained policy. Starter does not require peer-AS bypass/as-override and must not silently inherit current forced true settings. Service-peering exports no advertised workload prefixes; no route-import/transit/off-link VIP fixture. APIC provider/schema/plan validation is required before any parity claim; these snippets are representative intended data, not a Terraform plan.

Representative documented Isovalent CRs:
```yaml
apiVersion: isovalent.com/v1
kind: IsovalentBGPClusterConfig
metadata:
  name: foundation-bgp-bm-node-1
spec:
  nodeSelector:
    matchLabels:
      kubernetes.io/hostname: bm-node-1
  bgpInstances:
  - name: service-peering
    localASN: 64711
    peers:
    - name: leaf103
      peerASN: 64512
      peerAddress: 198.18.11.1
      peerConfigRef:
        name: service-peering
    - name: leaf104
      peerASN: 64512
      peerAddress: 198.18.11.2
      peerConfigRef:
        name: service-peering
---
apiVersion: isovalent.com/v1
kind: IsovalentBGPPeerConfig
metadata:
  name: service-peering
spec:
  transport:
    sourceInterface: bond0.2111
  timers:
    connectRetryTimeSeconds: 5
    holdTimeSeconds: 9
    keepAliveTimeSeconds: 3
  families:
  - afi: ipv4
    safi: unicast
    advertisements:
      matchLabels:
        advertise: foundation-session-only
```
Generate one configuration per declared resolved participant: `foundation-bgp-bm-node-1`, `foundation-bgp-bm-node-2`, and `foundation-bgp-bm-node-3`, each selecting only its corresponding standard hostname label. The snippet shows the first. All reuse `service-peering` because this v1 example resolves the same source-interface name on every host. No all-Linux selector or additional customer-maintained label list is used.

Before activation, verify each declared Agent hostname matches its intended `Node.metadata.name` and standard hostname label. Reject absent, ambiguous, duplicated, unexpected or hostname/FQDN-mismatched identities; do not guess or strip suffixes. The selected node set must exactly equal the resolved participant set. On each participant, observe the expected interface and exactly its declared eligible IPv4 address/prefix. Before installation these are desired identities, not verified live identities. V1 rejects unequal interface names across participants sharing this PeerConfig, with the exact host/interface paths; no implicit grouping. Another BGP configuration owner selecting a participant is a conflict, not permission to delete it.

No advertisement object with that label is generated, and preflight rejects existing conflicting selected advertisements or another cluster config selecting these nodes. Source interface has exactly one IPv4 address on each node; no movable secondary/SNAT address. Imported routes are not enabled. Never create controller-owned IsovalentBGPNodeConfig. Cilium enablement input is the documented enterprise.bgpControlPlane.enabled=true with OSS bgpControlPlane.enabled=false; exact selected CLife CiliumConfig mapping/gate is checked against that release before emitting/activating it. No generic helm upgrade/restart or installation instructions are executed by this proposal. The starter remains sessions-only, not an end-to-end product validation.

## 4. Customization/ownership/impact — no hidden settings to discover

| Customer required facts | Ordinary explicit maintained defaults | Exceptions and impact |
|---|---|---|
| Cluster name/domain, compact selection, reviewed release | Derived resource names, roles, pod/service ranges and Cilium operational settings; all shown resolved | Name/domain/CIDRs initial-install contract; CIDR override required on conflict. Upgrades follow supported upgrade flow, not ISO replay |
| Per-host disk, MACs, OS NIC names, bond membership/mode/primary, port attachment | miimon100; no LACP in this access-port case | Supported bond-policy overrides explicit. LACP requires actual matching port-channel/vPC facts; not inferred from two links. NIC/bond changes installation/migration sensitive |
| Native/tagged allocation, host addresses, gateway, MTU, ASNs and actual leaf/router/SVI addresses | Prefix/interface-name derivation and ordinary BGP timers; no guessed addressing | VLAN/address/VRF/ASN changes affect linked Linux/ACI/BGP owners; initial install or coordinated released migration. No safe-live-update promise |
| Managed APIC target/identity/private trust and actual commissioned objects | Deterministic physical/routed domain/AAEP/access/profile naming; explicit protocol/security policy | Existing identities may be deliberately overridden; collisions/dual port ownership fail. Native/routed domain merge needed for shared ports |
| Real DNS/NTP, image destinations/delivery/trust and private references | One maintained selected release/image/source catalogue; no customer payload/version duplication | Public trust/upstream-connected mapping optional when applicable; mirrors need real receipts. Secrets stay private. Image/trust update needs relevant runtime rollout |

**Bond-mode compatibility:** Active-backup is this example’s explicit choice, not a new universal default. Existing valid `802.3ad` compositions retain their mode, members, options and actual port-channel/vPC facts for the supported execution adapter. Missing or unsupported mode-to-port-policy combinations fail before render/apply; the tool must never convert them to active-backup or discard topology. Options default from the selected mode: `primary` is active-backup specific; LACP rate/hash settings apply only to `802.3ad` where supported. No blind option carryover or newly implied managed-ACI capability.

Fully resolved review lists the actual defaults and ownership for the selected site, then produces existing domain artifacts: Compute install/Agent/Cilium/metadata, Fabric NAC data and selected Isovalent CR desired YAML. No mandatory downstream GitOps repository/Argo root is needed to define/install base; optional BGP CR delivery must use an owning reviewed post-install mechanism or selected GitOps root. Output examples describe desired objects; they do not claim base boot automatically creates them. Optional BGP requires installed Cilium/enterprise capability and exactly one configuration owner; namespace/controller/release readiness checked before delivery. No tenant, storage, ILB/EGW or private-network prerequisite.

## 5. One change, all corresponding effects

BEFORE: networks.service-peering.vlan=2111. AFTER: change that ONE value to2211; leave network name, subnet, node addresses, ASNs and all native management facts unchanged.
| Output | Before -> after |
|---|---|
| Compute Agent network on all 3 hosts | bond0.2111/id2111 -> bond0.2211/id2211; still based on bond0, same 198.18.11.10/.11/.12/28 |
| Linux connected route | Same198.18.11.0/28 prefix, outgoing child changes tobond0.2211 |
| ACI shared pool | Native1701 unchanged; tagged 2111 ->2211 |
| ACI floatingSVI leaf103/104 | Encapsulation2111 ->2211; same leaf paths, peer/SVI addresses and ASNs |
| IsovalentBGPPeerConfig | transport.sourceInterface bond0.2111 ->bond0.2211; cluster-config peer addresses/ASNs unchanged |
| Preserved management intent | Same physical NICs/access ports, nativeEPG1701, bond0 IPv4192.0.2.x/24/default via192.0.2.1, API/appsVIPs and DNS/NTP |
This is an INITIAL-INSTALL comparison of intended configurations, not evidence of a hitless live VLAN conversion. If later deployed, the owning pipelines must sequence Linux/fabric/BGP updates; sessions may interrupt. Preserved declarative management route is necessary but does not prove real reachability. Installation preflight/console/observations establish that separately.

## 6. Minimal implementation work, source/evidence and review boundary

At Datacenter 85c9684ef894683ddbcebfb4da075637079a916a:
- Existing resolver -> new single cluster input normalizer directly into shared resolved objects; legacy mode stays separate, mixed authorities rejected. No old-format generated intermediate. Resource-specific defaults/versioned policy extracted, effective review produced once.
- templates/cluster-install/openshift/agent-config.yaml.j2 currently renders ONLY a common management bond. It needs per-host declared bond members and selected tagged children for this example, preserving base IPv4/default route. Compute staging validator must recognize/reject only supported selected attachments and retain manifest/private-enrichment checks.
- scripts/render-aci.py::_management_attachments currently requires distinct management/tenant ports and rejects duplicate ports. Implement one unioned access attachment per physical port with both domains; do not bypass its rejection or emit competing policies. _interface_profile_ctx currently uses global ACI MTU and forces BFD/as_override/peer-AS bypass; this example requires network MTU 1500 and explicit selected policy. Also remove HCP-only selection dependency for these foundation network objects. Templates/aci/management.yml.j2, management-domains.yml.j2, interface-attachments.yml.j2, access-policies.yml.j2, tenant.yml.j2 supply current NAC shapes.
- scripts/gitops_renderer/management.py::_render_prerequisites_bgp is currently tied to HCP networking/advertisements and hardcoded operational settings. Reuse its documented CR shapes from normalized foundation attachments; no fake HCP objects/selected VIPs to make it run. Pull only touched operational policy into maintained defaults. Keep the optional BGP delivery boundary explicit; no Argo bootstrap invention.
- Existing release catalogue supplies selected Cilium pins/CLife checksum but not complete qualified payload evidence. Verify exact OCP digest/archive/chart feature/schema prerequisites before implementation release; preserve source-based vs documented vs installed claims.

Local IEP docs at 02157c287639c55707d32f42397e214ae3653354: docs/configuration-guide/networking/bgp/advertise-routes-over-bgp.rst (enterprise enable, OSS disable); concepts/cluster-config.rst (localASN/peers/nodeSelector and one-config-per-node); concepts/peer-config.rst (transport.sourceInterface one eligible address/family, IPv4 families/selectors/timers); concepts/general.rst (operator owns IsovalentBGPNodeConfig). These support the fields shown; this snapshot is newer/general documentation, not proof of selected1.19.7 feature approval. No route-import preview fields used. Exact selected chart/source check remains a prerequisite rather than invented product behavior.
Red Hat 4.19 Agent-based installer documents bond plus VLAN networkConfig/base-iface/id: https://docs.redhat.com/en/documentation/openshift_container_platform/4.19/html-multi/installing_an_on-premise_cluster_with_the_agent-based_installer/installation-config-parameters-agent (official documentation; discovered20Oct). This establishes syntax, not installed Cilium/ACI interoperability.

Self-review: single input; NIC-to-port/bond/network/address trace local to each host; shared allocation declared once;6 unique leaf ports with one policy-group owner; native vs tagged semantics explicit; managed ACI object correspondence supplied; native default preserved; sessions-only BGP without off-link/service/import exceptions; all Isovalent fields tied to pinned docs; exact release evidence gap and current renderer limitations disclosed. Representative outputs are hand-authored design examples only. Only scratch YAML/address/reference/change-consistency checks performed; no platform render/installer/terraform plan/API validation/live test. Required implementation gates remain deterministic actual render/artifact manifest/target comparison, positive and conflict tests for joins, schema/Compute/Fabric validators and reviewed deployment ownership.

Tenant clusters (`tenants.yaml`), advanced worker multi-NIC/IPv6, lab backport and WET output migration are later scopes. This branch contains the foundation design only; implementation and publication of the resulting platform changes require separate agreement.
