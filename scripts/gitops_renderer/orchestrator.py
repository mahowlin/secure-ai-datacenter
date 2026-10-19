from .hcp_base import merge_declared_hcp_base
"""Top-level GitOps render orchestration."""

from pathlib import Path

import yaml

from .common import HEADER, ResolvedOCPCluster, ResolvedDefaults, _k8s_yaml, _kustomization, _tenant_bond_interface
from .core import (
    _render_argocd_config,
    _render_platform_idms,
    _render_cluster_types,
    _render_isovalent_root,
)
from .catalogsources import _render_platform_catalogsources
from .applications import _render_cluster_entrypoints
from .management import (
    _render_prerequisites_operators,
    _render_prerequisites_operands,
    _render_prerequisites_nmstate,
    _render_prerequisites_nads,
    _render_prerequisites_bgp,
    _render_prerequisites_kustomizations,
)
from .worker_dhcp import _render_worker_dhcp
from .console_login import render_console_login
from .hosted_clusters import _render_hosted_clusters
from .local_registry import _render_local_registry
from .oc_mirror_trial import render_oc_mirror_trial
from .tenant_datapath import _render_tenant_apps
from .cilium_config_bridge import _render_cilium_config_bridge
from .ilb_install import _render_ilb_install
from .storage import render_storage
from .selected_inputs import bind_defaults, ilb_paths
from .hcp_bootstrap import render_hcp_bootstrap
from .native_monitoring import render as render_native_monitoring
from .tenant_observability import render_hosted_writer_intent
from .addons import render_addon_capabilities


@bind_defaults
def render_gitops(cluster: ResolvedOCPCluster, fabric_asn: int,
                  defaults: ResolvedDefaults) -> dict[str, str]:
    """Render all GitOps manifests. Returns {relative_path: content}."""
    storage_files = render_storage(cluster, defaults) if cluster.storage else {}
    addon_files = render_addon_capabilities(cluster, defaults)
    if not cluster.features.get("hostedControlPlanes", False):
        storage_files.update(addon_files)
        return storage_files

    gitops_repo_url = defaults.gitops_repo_url
    gitops_target_revision = defaults.gitops_target_revision
    bond_interface = _tenant_bond_interface(cluster, defaults)

    files: dict[str, str] = dict(storage_files)
    files.update(addon_files)
    files.update(render_native_monitoring(cluster, defaults))
    files.update(render_hosted_writer_intent(cluster, defaults))

    files.update(_render_argocd_config(gitops_repo_url, gitops_target_revision, defaults))
    files.update(_render_platform_idms(cluster, defaults))
    files.update(_render_platform_catalogsources(defaults))
    files.update(_render_cluster_types())
    files.update(_render_isovalent_root(gitops_repo_url, gitops_target_revision, defaults))
    files.update(_render_cilium_config_bridge(cluster))
    files.update(_render_cluster_entrypoints(cluster, gitops_repo_url, gitops_target_revision, defaults))
    if addon_files:
        root = f"clusters/{cluster.name}/kustomization.yaml"
        root_doc = yaml.safe_load(files[root])
        root_doc["resources"].append("addons")
        files[root] = HEADER + _k8s_yaml(root_doc)

    files.update(_render_prerequisites_operators(cluster, defaults))
    files.update(_render_prerequisites_operands(cluster, defaults))
    files.update(_render_prerequisites_nmstate(cluster, bond_interface, defaults))
    files.update(_render_prerequisites_nads(cluster, defaults))
    files.update(_render_worker_dhcp(cluster, defaults))
    files.update(_render_prerequisites_bgp(cluster, fabric_asn, defaults))
    files.update(_render_prerequisites_kustomizations(cluster, defaults))
    files.update(_render_ilb_install(cluster, defaults))
    declared_ilb = ilb_paths()
    if declared_ilb["stagedKustomization"] in files:
        if defaults.input_root is None:
            raise ValueError("selected input root is required")
        source = Path(defaults.input_root) / declared_ilb["source"] / "activation.yaml"
        activation = yaml.safe_load(source.read_text())
        if not isinstance(activation, dict) or type(activation.get("active")) is not bool:
            raise ValueError("ILB activation must be an explicit boolean")
        if activation["active"]:
            root = f"clusters/{cluster.name}/kustomization.yaml"
            root_doc = yaml.safe_load(files[root])
            root_doc["resources"].append(declared_ilb["stagedResource"])
            files[root] = HEADER + _k8s_yaml(root_doc)
    files.update(_render_local_registry(cluster, defaults))
    files.update(render_oc_mirror_trial(cluster, defaults))
    files.update(_render_hosted_clusters(cluster, defaults))
    if cluster.hosted_cluster_lifecycle.get("gitopsBootstrap") is True:
        if defaults.input_root is None:
            raise ValueError("selected input root is required")
        files.update(render_hcp_bootstrap(
            Path(defaults.input_root), cluster.name,
            cluster.hosted_cluster_lifecycle["deploymentSubjects"],
            defaults.registry_url, defaults.release_image_repository,
        ))

    files.update(_render_tenant_apps(cluster, fabric_asn, defaults))

    # This explicit, source-owned opt-in places the reviewed installer in the
    # same Argo tree as its mutator target. The peer remains held separately.
    files.update(render_console_login(cluster, defaults, files))
    if cluster.cache_production.get('roots'):
        from hcp_cache import rewrite_images
        files = rewrite_images(files, cluster.cache_production)
    merge_declared_hcp_base(files, defaults, cluster.name)
    return files
