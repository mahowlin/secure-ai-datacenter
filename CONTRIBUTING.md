# Contributing and getting help

These repositories contain reference automation. Product compatibility, commercial support and software entitlement come from the relevant vendor; this code does not provide a support agreement.

## Report a problem

Use the affected repository's issue tracker or your existing maintainer contact. Include:

- The source commit, selected release and operating system/tool versions.
- The command and working directory, expected result and actual error.
- A minimal **redacted** input example and whether the failure is local rendering, installation or a running cluster.

Do not attach tokens, pull secrets, kubeconfigs, private CA/key files or customer inventories. A public example that renders is not evidence that a customer's images, hardware or cluster are supported.

## Propose a change

Keep a pull request focused. Edit maintained inputs or their owning renderer rather than patching generated YAML alone. Show the changed input, affected output and focused checks. Include documentation updates for a changed command or handoff; retain existing licenses and third-party attribution.

For documentation and diagrams, inspect an actual rendered page at desktop and narrow widths. Keep the diagram source next to its SVG, describe the relationship in text, and use labels or line styles as well as colour.

The [repository map](repo-map.yaml) identifies the component owners. Submit an execution change in its consumer repository and a shared-input change in Datacenter, with linked context when both are needed.
