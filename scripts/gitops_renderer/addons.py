"""Optional add-on Applications selected by day2GitOps.addOns."""

from pathlib import Path

from .common import HEADER, ResolvedDefaults, ResolvedOCPCluster, _annotation, _k8s_yaml, _kustomization
from .selected_inputs import api_label

import yaml


_FLOATING_TAGS = {"main", "latest", "master"}
_CATALOG = Path("data/capabilities/addons.yaml")


def render_addon_capabilities(cluster: ResolvedOCPCluster,
                              defaults: ResolvedDefaults) -> dict[str, str]:
    """Return addon Applications. Disabled switches contribute no files."""
    errors = addon_errors(cluster, defaults)
    if errors:
        raise ValueError("; ".join(errors))
    enabled = _enabled(cluster)
    if not enabled:
        return {}
    catalog = _catalog(defaults)
    selections = cluster.gitops_addon_selections
    files: dict[str, str] = {}
    names: list[str] = []
    for switch in enabled:
        for spec in _selected_applications(catalog[switch], selections.get(switch) or {}):
            filename = f"{spec['name']}.yaml"
            names.append(filename)
            app = _application(spec, cluster, selections.get(switch) or {}, defaults)
            files[f"clusters/{cluster.name}/addons/{filename}"] = HEADER + _k8s_yaml(app)
    files[f"clusters/{cluster.name}/addons/kustomization.yaml"] = _kustomization(sorted(names))
    return files


def addon_errors(cluster: ResolvedOCPCluster, defaults: ResolvedDefaults) -> list[str]:
    """Return pin and selection errors for enabled switches."""
    errors = []
    for name, value in sorted((cluster.gitops_addons or {}).items()):
        if type(value) is not bool:
            errors.append(f"day2GitOps.addOns.{name} must be a boolean")
    selections = getattr(cluster, "gitops_addon_selections", {}) or {}
    if not isinstance(selections, dict):
        errors.append("day2GitOps.addonSelections must be a map")
        selections = {}
    enabled = _enabled(cluster)
    if errors or not enabled:
        return errors
    try:
        catalog = _catalog(defaults)
    except ValueError as exc:
        return [str(exc)]
    for switch in enabled:
        entry = catalog.get(switch)
        if not isinstance(entry, dict):
            errors.append(f"{switch} addon catalog entry is required")
            continue
        selected = selections.get(switch) or {}
        if not isinstance(selected, dict):
            errors.append(f"day2GitOps.addonSelections.{switch} must be a map")
            continue
        errors.extend(_entry_errors(cluster, switch, entry, selected))
    return errors


def _enabled(cluster: ResolvedOCPCluster) -> list[str]:
    return sorted(name for name, value in (cluster.gitops_addons or {}).items() if value is True)


def _catalog(defaults: ResolvedDefaults) -> dict:
    root = getattr(defaults, "input_root", None)
    if root is None:
        raise ValueError("selected input root is required")
    path = Path(root) / _CATALOG
    if not path.is_file():
        raise ValueError("addon capability catalog is required")
    document = yaml.safe_load(path.read_text())
    addons = document.get("addons") if isinstance(document, dict) else None
    if not isinstance(addons, dict):
        raise ValueError("addon capability catalog is required")
    return addons


def _entry_errors(cluster: ResolvedOCPCluster, switch: str, entry: dict, selected: dict) -> list[str]:
    errors = []
    applications = list(entry.get("applications") or [])
    model_field = entry.get("modelSelection")
    if model_field:
        model_name = selected.get(model_field)
        models = entry.get("models") or {}
        if not isinstance(model_name, str) or model_name not in models:
            errors.append(f"{switch} addon requires one declared model")
        else:
            applications.append(models[model_name])
    for spec in applications:
        errors.extend(_application_errors(cluster, switch, spec, selected))
    return errors


def _selected_applications(entry: dict, selected: dict) -> list[dict]:
    applications = list(entry.get("applications") or [])
    model_field = entry.get("modelSelection")
    if model_field:
        applications.append(entry["models"][selected[model_field]])
    return applications


def _application_errors(cluster: ResolvedOCPCluster, switch: str, spec: dict, selected: dict) -> list[str]:
    errors = []
    prefix = f"{switch} addon application"
    if not isinstance(spec, dict):
        return [f"{prefix} is required"]
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        errors.append(f"{prefix} name is required")
    path = spec.get("path")
    if not isinstance(path, str) or not path:
        errors.append(f"{prefix} path is required")
    elif path.startswith("charts/") and not spec.get("chartVersion"):
        errors.append(f"{prefix} chartVersion is required")
    if not spec.get("destinationNamespace"):
        errors.append(f"{prefix} destinationNamespace is required")
    for parameter in spec.get("parameters") or []:
        if _parameter_value(cluster, switch, parameter, selected) is None:
            errors.append(f"{prefix} {parameter.get('name')} is required")
    if spec.get("imageFromSelection"):
        repository = selected.get("repository")
        tag = selected.get("tag")
        if not isinstance(repository, str) or not repository:
            errors.append(f"{prefix} image repository is required")
        if not isinstance(tag, str) or not tag or tag in _FLOATING_TAGS:
            errors.append(f"{prefix} image tag must be an immutable pin")
    errors.extend(_pin_errors(prefix, spec.get("pins") or {}))
    errors.extend(_pin_errors(prefix, (spec.get("helmValues") or {}).get("operator", {}).get("image") or {}))
    return errors


def _pin_errors(prefix: str, pins: dict) -> list[str]:
    errors = []
    if not isinstance(pins, dict):
        return [f"{prefix} pins must be a map"]
    for key, value in pins.items():
        if not isinstance(value, str) or not value:
            errors.append(f"{prefix} pin {key} is required")
        elif key.lower().endswith("tag") and value in _FLOATING_TAGS:
            errors.append(f"{prefix} pin {key} must be an immutable pin")
    return errors


def _parameter_value(cluster: ResolvedOCPCluster, switch: str, parameter: dict, selected: dict):
    source = parameter.get("valueFrom")
    if source == "clusterFqdn":
        if cluster.name and cluster.base_domain:
            return f"{cluster.name}.{cluster.base_domain}"
        return None
    if source == "selection":
        value = selected.get(parameter.get("selectionKey"))
        if isinstance(value, str) and value:
            return value
        return None
    return None


def _application(spec: dict, cluster: ResolvedOCPCluster, selected: dict,
                 defaults: ResolvedDefaults) -> dict:
    annotations = _annotation(spec.get("syncWave") or "0")
    for key, value in (spec.get("pins") or {}).items():
        annotations[api_label(key)] = value
    if spec.get("chartVersion"):
        annotations[api_label("chart-version")] = str(spec["chartVersion"])
    if spec.get("imageFromSelection"):
        annotations[api_label("image-repository")] = selected["repository"]
        annotations[api_label("image-tag")] = selected["tag"]
    source = {
        "repoURL": defaults.gitops_repo_url,
        "targetRevision": defaults.gitops_target_revision,
        "path": spec["path"],
    }
    parameters = []
    for parameter in spec.get("parameters") or []:
        parameters.append({
            "name": parameter["name"],
            "value": _parameter_value(cluster, "", parameter, selected),
        })
    helm = {}
    if parameters:
        helm["parameters"] = parameters
    if spec.get("helmValues"):
        helm["values"] = yaml.safe_dump(spec["helmValues"], sort_keys=False)
    if helm:
        source["helm"] = helm
    if spec.get("directory"):
        source["directory"] = spec["directory"]
    policy = {"syncOptions": list(spec.get("syncOptions") or [])}
    if "prune" in spec or "selfHeal" in spec:
        policy["automated"] = {
            "prune": bool(spec.get("prune")),
            "selfHeal": bool(spec.get("selfHeal")),
        }
    if spec.get("retry"):
        retry = spec["retry"]
        policy["retry"] = {
            "limit": retry["limit"],
            "backoff": {
                "duration": retry["duration"],
                "factor": retry["factor"],
                "maxDuration": retry["maxDuration"],
            },
        }
    app = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": spec["name"],
            "namespace": defaults.namespace("gitops"),
            "annotations": annotations,
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": "default",
            "source": source,
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": spec["destinationNamespace"],
            },
            "syncPolicy": policy,
        },
    }
    if spec.get("ignoreDifferences"):
        app["spec"]["ignoreDifferences"] = spec["ignoreDifferences"]
    return app
