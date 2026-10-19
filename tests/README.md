# Tests

`tests/` checks the input contracts, the renderer, and the profile schema. `golden/` holds renderer output the parity checks compare against. Nothing here needs a cluster.

From the repository root:

```bash
python3 -m unittest discover -s tests
```

The [documentation index](../docs/README.md) separates what these tests prove from what a rendered file, a delivery, or a running cluster would still have to show.
