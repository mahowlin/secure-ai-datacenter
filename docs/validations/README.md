# Validation notes

These notes say what you should observe if you build a cluster from the example. The automated check is `tests/`.

| Note | What it covers |
|---|---|
| [Multus worker scenarios](multus-worker-scenarios.md) | What to test on `example-multus-a`, including DHCP address ownership, and what to observe. |
| [Shared storage](portworx-shared-storage.md) | Why hosted workers need shared ReadWriteMany block disks, and what to check. |
| [Direct-worker scaling](tenant-cluster-scaling.md) | How to size the Multus composition when hosted workers peer with the tenant fabric. |

From the repository root:

```bash
python3 -m unittest discover -s tests
```
