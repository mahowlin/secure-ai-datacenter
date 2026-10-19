"""Render the passive target-chart bridge for receipt-resolved CiliumConfig."""

from .selected_inputs import api_group, template_root

from .common import HEADER, Path, ResolvedOCPCluster


_API_GROUP_TOKEN = "__SAIF_API_GROUP__"


def _template_dir() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "templates"
        / template_root("gitops")
        / "charts"
        / "cilium-config"
        / "templates"
    )


def _render_cilium_config_bridge(cluster: ResolvedOCPCluster) -> dict[str, str]:
    package = cluster.release.clife_package if cluster.release else None
    if not package or not cluster.management_clife.enabled:
        return {}
    output: dict[str, str] = {}
    group = api_group()
    template_dir = _template_dir()
    for name in (
        "ciliumconfig-resolved.yaml",
        "postsync-resolved-restart-hook.yaml",
    ):
        body = (template_dir / name).read_text().replace(_API_GROUP_TOKEN, group)
        output[f"charts/cilium-config/templates/{name}"] = HEADER + body
    return output
