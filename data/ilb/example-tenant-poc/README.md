# Isovalent Load Balancer inputs

These YAML files are the controller deployment and its RBAC. `scripts/gitops_renderer/ilb_install.py` copies them into the render and pins the controller image. The controller watches hosted nodes in the configured pool, finds the current launcher addresses, and logs the cluster name when its remote client refreshes.

Two permissions are narrower than a cluster-wide role. The management Secret role can read only the backend auth Secret named in that Role. The hosted Service role can update only the Service named in that Role.

Do not put a runtime credential, a Service UID, an allocated NodePort, or a generated load-balancer object in this directory. The credential is supplied outside Git. Renewal stays a separate job that reads an admin Secret only inside that job and writes a scoped token into the controller Secret. The controller Secret never holds the admin material.
