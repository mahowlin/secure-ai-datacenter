"""Render one pinned OLM owner and isolated selected mirror resources from input."""

from __future__ import annotations

import copy
import re

import yaml


from .common import HEADER, ResolvedOCPCluster, ResolvedDefaults, _k8s_yaml, _kustomization


def _manifest(value: dict | list[dict]) -> str:
    values = value if isinstance(value, list) else [value]
    return HEADER + "".join(_k8s_yaml(item) for item in values)



def _resources(value: dict, role: str) -> dict:
    resources = value[role]
    for field in ("cpu", "memory"):
        amounts = []
        for dimension in ("requests", "limits"):
            quantity = resources[dimension][field]
            pattern = r"([1-9][0-9]*)(m)?" if field == "cpu" else r"([1-9][0-9]*)(Ki|Mi|Gi)"
            match = re.fullmatch(pattern, quantity) if isinstance(quantity, str) else None
            if not match:
                raise ValueError(f"invalid mirror {role} {dimension} {field}")
            number, unit = match.groups()
            amounts.append(int(number) * ({"m": 1, None: 1000} if field == "cpu"
                                          else {"Ki": 1024, "Mi": 1024**2, "Gi": 1024**3})[unit])
        if amounts[0] > amounts[1]:
            raise ValueError(f"mirror {role} {field} request exceeds limit")
    return copy.deepcopy(resources)


def render_oc_mirror_trial(cluster: ResolvedOCPCluster,
                           defaults: ResolvedDefaults) -> dict[str, str]:
    policy = (cluster.local_registry or {}).get("ocMirror") or {}
    if type(policy.get("publish", False)) is not bool:
        raise ValueError("operator publish must be an explicit boolean")
    if not policy.get("publish"):
        return {}
    install = policy["installation"]
    if install["method"] != "olm":
        raise ValueError("selected operator installation must have one OLM owner")
    if install["installPlanApproval"] != "Manual" or policy["syncMode"] != "manual":
        raise ValueError("operator installation and upgrades require manual approval")
    if install["mirrorSourcePolicy"] != "NeverContactSource":
        raise ValueError("operator bootstrap requires its explicit disconnected mirror policy")
    if policy["expose"] != {"type": "Service"}:
        raise ValueError("selected operator example exposes its target as a Service only")
    release = policy["release"]
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", release["version"]) or not re.fullmatch(r"[0-9a-f]{40}", release["commit"]):
        raise ValueError("operator release version/commit must be explicit pins")
    registry = cluster.local_registry
    namespace = policy["namespace"]
    target_prefix = policy["targetPrefix"]
    sync_host = f"{registry['syncService']}.{registry['namespace']}.svc:{registry['port']}"
    trial_repo = target_prefix[len(sync_host) + 1:] if target_prefix.startswith(sync_host + "/") else ""
    if (not re.fullmatch(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*", trial_repo)
            or trial_repo == registry["namespace"]
            or trial_repo.startswith(registry["namespace"] + "/")):
        raise ValueError("operator trial destination must use a separate sync repository prefix")
    if (namespace == registry["namespace"]
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", namespace)):
        raise ValueError("operator trial must use an isolated namespace")
    if any(type(policy[key]) is not int or not 1 <= policy[key] <= 2
           for key in ("concurrency", "batchSize")):
        raise ValueError("trial concurrency and batch size must be bounded")

    inventory = cluster.oc_mirror_inventory
    if not inventory or "roots" not in inventory:
        raise ValueError("operator image inventory must be resolved before rendering")
    selected = []
    selections = policy["selectedImages"]
    if not selections or len(selections) > 2:
        raise ValueError("trial must select one or two inventory images")
    seen_names, seen_targets = set(), set()
    for selection in selections:
        name, target_repo = selection["inventoryName"], selection["targetRepo"]
        if (name in seen_names or target_repo in seen_targets
                or not re.fullmatch(r"[a-z0-9]+(?:[._/-][a-z0-9]+)*", target_repo)
                or target_repo.startswith(registry["namespace"] + "/")):
            raise ValueError("trial image selection or target repository is duplicate or invalid")
        seen_names.add(name)
        seen_targets.add(target_repo)
        matches = [item for item in inventory["roots"] if item["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"operator trial image {name} is not unique in selected inventory")
        item = matches[0]
        if (item["source"].rsplit("@", 1)[-1] != item["digest"]
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", item["digest"])):
            raise ValueError(f"trial image {name} must be pinned by inventory digest")
        selected.append((item, target_repo))
    # Runtime trust is supplied by the owning pipeline. Public output contains
    # its ConfigMap reference only; no site certificate is exported.
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", policy["caConfigMap"]):
        raise ValueError("operator runtime CA ConfigMap reference is required")

    resolved = cluster.oc_mirror_installation
    if (resolved.get("csvPath") != install["csvPath"] or resolved.get("csvSha256") != install["csvSha256"]
            or resolved.get("bindingPins") != install["namespaceBindings"]):
        raise ValueError("operator installation differs from resolved artifact pins")
    if resolved.get("bindingNamespaces") != [namespace]:
        raise ValueError("selected published OLM bundle requires its namespace binding")
    csv = resolved["csv"]
    if (csv["metadata"]["name"] != install["startingCSV"]
            or str(csv["spec"]["version"]) != release["version"].removeprefix("v")
            or not any(x["type"] == "OwnNamespace" and x["supported"] for x in csv["spec"]["installModes"])):
        raise ValueError("operator CSV/version/OwnNamespace differs from selected install")
    if install["consolePluginEnabled"] is not True:
        raise ValueError("selected OLM release has no supported plugin-disable switch")
    bootstrap = release["bootstrapImages"]
    roles = {item["role"]: item for item in bootstrap}
    if len(roles) != len(bootstrap) or not {"controller", "manager", "worker", "bundle", "catalog"} <= roles.keys():
        raise ValueError("operator bootstrap closure is incomplete or duplicated")
    mirrors = {}
    for item in bootstrap:
        source_repo, digest = item["source"].rsplit("@", 1)
        destination_repo = item["destination"].rsplit(":", 1)[0]
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest) or digest != item["digest"]:
            raise ValueError("operator bootstrap image is not digest pinned")
        previous = mirrors.setdefault(source_repo, destination_repo)
        if previous != destination_repo:
            raise ValueError("operator bootstrap source has conflicting mirrors")
    pinned_catalog = roles["catalog"]["destination"].rsplit(":", 1)[0] + "@" + roles["catalog"]["digest"]
    if install["catalogImage"] != pinned_catalog:
        raise ValueError("catalog image differs from bootstrap closure")
    controller = csv["spec"]["install"]["spec"]["deployments"][0]["spec"]["template"]["spec"]["containers"][0]
    if controller["image"] != roles["controller"]["source"]:
        raise ValueError("controller bootstrap image differs from selected CSV container")
    env = []
    for key, role in (("OPERATOR_IMAGE", "controller"), ("MANAGER_IMAGE", "manager"), ("WORKER_IMAGE", "worker")):
        source = roles[role]["source"]
        if source != next((e.get("value") for e in controller.get("env", []) if e["name"] == key), None):
            raise ValueError("runtime image differs from selected CSV")
        reference = roles[role]["destination"].rsplit(":", 1)[0] + "@" + roles[role]["digest"]
        if release[role] != reference:
            raise ValueError("runtime image differs from bootstrap closure")
        env.append({"name": key, "value": reference})
    for item in controller.get("env", []):
        if item["name"].startswith("RELATED_IMAGE_PLUGIN_"):
            match = next((image for image in bootstrap if image["source"] == item["value"]), None)
            if match is None:
                raise ValueError("plugin image missing from bootstrap closure")
            value = match["destination"].rsplit(":", 1)[0] + "@" + match["digest"]
            env.append({"name": item["name"], "value": value})
    catalog = {"apiVersion": "operators.coreos.com/v1alpha1", "kind": "CatalogSource",
               "metadata": {"name": install["catalogName"], "namespace": namespace},
               "spec": {"sourceType": "grpc", "image": install["catalogImage"],
                        "displayName": install["catalogDisplayName"],
                        "grpcPodConfig": {"securityContextConfig": "restricted", "memoryTarget": install["catalogMemoryTarget"]}}}
    group = {"apiVersion": "operators.coreos.com/v1", "kind": "OperatorGroup",
             "metadata": {"name": install["operatorGroup"], "namespace": namespace},
             "spec": {"targetNamespaces": [namespace]}}
    subscription = {"apiVersion": "operators.coreos.com/v1alpha1", "kind": "Subscription",
                    "metadata": {"name": install["package"], "namespace": namespace},
                    "spec": {"name": install["package"], "channel": install["channel"],
                             "source": install["catalogName"], "sourceNamespace": namespace,
                             "startingCSV": install["startingCSV"], "installPlanApproval": install["installPlanApproval"],
                             "config": {"env": env, "resources": _resources(policy["resources"], "controller")}}}
    digest_mirrors = {"apiVersion": "config.openshift.io/v1", "kind": "ImageDigestMirrorSet",
                      "metadata": {"name": install["mirrorPolicyName"]},
                      "spec": {"imageDigestMirrors": [
                          {"source": source, "mirrors": [destination], "mirrorSourcePolicy": install["mirrorSourcePolicy"]}
                          for source, destination in sorted(mirrors.items())]}}
    imageset = {"apiVersion": "mirror.openshift.io/v1alpha1", "kind": "ImageSet",
                "metadata": {"name": policy["imageSet"], "namespace": namespace},
                "spec": {"mirror": {"additionalImages": [
                    {"name": item["source"],
                     "targetRepo": target_repo,
                     "targetTag": item["digest"].replace(":", "-", 1)}
                    for item, target_repo in selected]}}}
    target = {"apiVersion": "mirror.openshift.io/v1alpha1", "kind": "MirrorTarget",
              "metadata": {"name": policy["mirrorTarget"], "namespace": namespace},
              "spec": {"registry": target_prefix, "authSecret": policy["authSecret"],
                       "insecure": False, "caBundle": {"configMapName": policy["caConfigMap"],
                                                        "key": "ca-bundle.crt"},
                       "imageSets": [policy["imageSet"]], "expose": copy.deepcopy(policy["expose"]),
                       "concurrency": policy["concurrency"], "batchSize": policy["batchSize"],
                       "maxRetries": policy["maxRetries"], "pollInterval": policy["pollInterval"],
                       "checkExistInterval": policy["checkExistInterval"],
                       "manager": {"resources": _resources(policy["resources"], "manager")},
                       "worker": {"resources": _resources(policy["resources"], "worker")}}}
    trial_ns = {"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": namespace}}
    base = f"apps/hcp/management/oc-mirror-trial/overlays/{cluster.name}"
    contents = {"namespace.yaml": trial_ns, "catalogsource.yaml": catalog,
                "operatorgroup.yaml": group, "subscription.yaml": subscription,
                "image-mirrors.yaml": digest_mirrors,
                "imageset.yaml": imageset, "mirrortarget.yaml": target}
    from hcp_cache import production_resources
    production = production_resources(cluster)
    resources = list(contents) + [name.rsplit("/",1)[1] for name in production]

    result = {f"{base}/{name}": _manifest(value) for name, value in contents.items()}
    result.update(production)
    result[f"{base}/kustomization.yaml"] = _kustomization(resources)
    return result
