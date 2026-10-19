# Minimal standalone inputs

One synthetic three-node compact cluster, using existing resolver/schema/templates and the existing 4.19.21 / 1.19.7 stable release facts. No hosted inventory or conformance identities are required. These files are editable input examples, not executable credentials or an installed cluster.

Follow [the input/render handoff](../../docs/architectures/COMPACT_BAREMETAL_INSTALL.md). Copy this input set plus the checkout's `templates/` and `schemas/` to an operator-owned working root. Change actual hardware, network and service inputs there. Keep exact release/archive metadata aligned with any optional mirroring recipe. Current limits: static IPv4, three compact hosts and two management NICs with common names. Optional integrated examples stay in the main `data/` tree.

The optional GitOps bootstrap selection uses the existing `gitops.namespaces`,
`catalogSourceAliases` and `operatorSubscriptions` fields. The example selects
the upstream `redhat-operators` catalog; change source, namespace, channel and
approval explicitly for your site. No GitOps cluster/root is generated. For a
connected site without a local registry, omit the optional registry/mirror
fields, choose public trust and declare the actual upstream release repository.
See the linked procedure for the exact input and runtime boundary.
