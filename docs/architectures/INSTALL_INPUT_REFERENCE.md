# Installation delivery, trust and release reference

Use this reference while editing the inputs for the [compact installation](COMPACT_BAREMETAL_INSTALL.md). The procedure owns the deployment sequence.

## Select delivery and trust explicitly

The example selects **4.19.21 / 1.19.7**, reusing existing source image digests and the `clife-1.19.7` archive facts in optional `secure-ai-airgap/mirror/releases/`. `qualification.validationStatus: render-only` is not an installed result. The existing policy permits 4.19/1.18, 4.19/1.19 and 4.20/1.19; selection remains fail-closed. A policy row is a reviewed family allowance, not evidence of exact installed support. Check the vendor documentation for your installation method/release and preserve independent support and validation states.

In `installConfig.openshift.imageDelivery`, choose:

- `mode: connected`, `scopes: []`: nodes use reachable upstream/site-provided image services. Validate raw artifact hashes and the entitled official archive. No mirror-workflow receipt is invented or required. Configure `imageDigestSources` for any site mirrors actually used; remove example rewrites when pulling directly.
- `mode: mirrored`, `scopes: [ocp-install-core, cilium-day1]`: require actual promoted receipts and exact selected release/images. Select `gitops-bootstrap` / `platform-day2` only when you intend those later operations. Unselected optional bundles are not Day 1 prerequisites.

`registryTrustPolicy: additional-ca` requires a trusted private PEM file at executable render time. `public` uses normal system trust and rejects an unrelated extra CA. Both retain TLS verification and authenticated access where required. Existing external services can be supplied; running airgap service provisioning is optional.

To change release: review the exact payload/image/archive facts and vendor compatibility; update the named release and (only with reviewed evidence) `spec.day1Compatibility`; align the optional mirror ImageSet selection, catalogs and release recipe; re-render and check the metadata/artifact joins. Never edit renderer code or rename a payload to evade compatibility checks. Adding a policy row alone does not qualify a release. Actual archive acquisition, authenticated pulls and installation remain unproven by offline tests.
