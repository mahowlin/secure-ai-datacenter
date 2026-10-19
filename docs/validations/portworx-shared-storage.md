# Portworx shared storage

Hosted workers are virtual machines, and a virtual machine that can move needs its root disk to be visible from more than one management node. A local disk on one server cannot do that. This example uses Portworx on the compact management cluster to provide a shared ReadWriteMany block volume, and KubeVirt consumes that volume as the worker root disk.

The storage class for those roots is `px-rwx-block-kubevirt`. The cluster default for ordinary replicated volumes is `px-csi-replicated-default`. The rendered StorageCluster uses local PX-StoreV2, an internal key-value store, and three replicas. Each of the three management nodes contributes five NVMe devices: one for metadata and four for data. Automatic disk discovery is off, so the device list in the input is the list that gets used.

The parallel worker pool for a hosted cluster is rendered with `ReadWriteMany` raw block roots on `px-rwx-block-kubevirt`, `maxUnavailable: 1` and `autoRepair: false`. The selected parent lifecycle phase is `workers`: both hosted-cluster kustomizations include `nodepool-workers.yaml`, and their parent Application uses automated prune/self-heal. The parent overrides select five `example-multus-a` workers and three `example-shared` workers. The Portworx Application is selected but manual. Importing and synchronizing the root can therefore create NodePools; supply private bootstrap/CA material, ready operators, licensed storage and accepted disks first. A render does not prove any of those prerequisites or create a running worker by itself.

The single-node cluster is a different design. It keeps a local RAID set and LVMS, rendered as GitOps under `apps/storage/sno-mdadm-lvms/example-sno`. Do not treat that layout as the hosted-worker disk.

What to check if you build this:

- Portworx, its internal store, CSI and Stork are ready on the management nodes.
- A PVC that does not name a class binds to `px-csi-replicated-default`.
- A KubeVirt root disk that must move uses `px-rwx-block-kubevirt` and a ReadWriteMany block volume.
- The StorageCluster device list matches the disks you intend to give Portworx, because discovery will not pick them for you.
- License and registry credentials are supplied at deploy time. They are not in this tree.

The Portworx 3.7 guides for hosted clusters assume a FlashArray behind the management cluster. This example uses local disks, which is why `autoRepair` stays false. A successful generic virtual-machine move is not the same thing as moving a hosted worker.
