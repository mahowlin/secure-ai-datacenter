from .common import *


def _render_platform_catalogsources(defaults: ResolvedDefaults) -> dict[str, str]:
    """Render canonical disconnected OperatorHub/catalog sources."""
    certified = defaults.catalog_source("certified")
    redhat = defaults.catalog_source("redhat")
    return {
        "apps/platform-catalogsources/certified-operators-local.yaml": HEADER + _k8s_yaml({
            "apiVersion": "operators.coreos.com/v1alpha1",
            "kind": "CatalogSource",
            "metadata": {
                "name": certified["name"],
                "namespace": certified["namespace"],
            },
            "spec": {
                "sourceType": "grpc",
                "image": certified["image"],
                "displayName": certified["displayName"],
                "publisher": certified["publisher"],
                "updateStrategy": {"registryPoll": {"interval": certified["pollInterval"]}},
            },
        }),
        "apps/platform-catalogsources/redhat-operators-local.yaml": HEADER + _k8s_yaml({
            "apiVersion": "operators.coreos.com/v1alpha1",
            "kind": "CatalogSource",
            "metadata": {
                "name": redhat["name"],
                "namespace": redhat["namespace"],
            },
            "spec": {
                "sourceType": "grpc",
                "image": redhat["image"],
                "displayName": redhat["displayName"],
                "publisher": redhat["publisher"],
                "updateStrategy": {"registryPoll": {"interval": redhat["pollInterval"]}},
            },
        }),
        "apps/platform-catalogsources/kustomization.yaml": _kustomization([
            "certified-operators-local.yaml",
            "redhat-operators-local.yaml",
        ]),
    }
