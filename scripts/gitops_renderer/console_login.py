"""Render a declared htpasswd console login without credential material."""

from __future__ import annotations

from pathlib import Path

import yaml

from .common import HEADER, _annotation, _k8s_yaml


POLICY_NAME = "login.yaml"
SEALED_DIR = "login-sealed"
HTPASSWD_TYPE = "HTPasswd"
HTPASSWD_KEY = "htpasswd"
FORBIDDEN_KEYS = {
    "password", "passwd", "hash", "bcrypt", "token", "secretvalue",
    "htpasswdhash", "stringdata",
}


def load_console_login(cluster_dir: Path) -> dict:
    """Return the console-login spec, or an empty mapping when the cluster has none."""
    path = cluster_dir / POLICY_NAME
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError(f"{POLICY_NAME} must be a mapping")
    spec = loaded.get("spec", loaded)
    if not isinstance(spec, dict):
        raise ValueError(f"{POLICY_NAME} spec must be a mapping")
    if loaded.get("kind") not in (None, "ConsoleLogin"):
        raise ValueError(f"{POLICY_NAME} kind must be ConsoleLogin")
    return spec


def secret_namespace(target: dict) -> str:
    scope = target.get("scope")
    if scope == "management":
        namespace = target.get("secretNamespace")
        if not isinstance(namespace, str) or not namespace:
            raise ValueError("management console login target requires secretNamespace")
        return namespace
    if scope == "hosted":
        name = target.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError("hosted console login target requires a name")
        declared = target.get("secretNamespace")
        derived = f"clusters-{name}"
        if declared not in (None, derived):
            raise ValueError("hosted console login secretNamespace differs from the hosted cluster namespace")
        return derived
    raise ValueError("console login target scope must be management or hosted")


def expand_console_login(cluster, spec: dict) -> dict:
    """Select hosted targets from deployment subjects so a retired cluster drops out."""
    if not spec:
        return {}
    policy = dict(spec)
    if policy.get("hostedTargets") != "deployment-subjects":
        return policy
    lifecycle = getattr(cluster, "hosted_cluster_lifecycle", None) or {}
    subjects = lifecycle.get("deploymentSubjects") or {}
    staged = subjects.get("stagedOnly") if isinstance(subjects, dict) else []
    staged_names = {item for item in staged if isinstance(item, str)} if isinstance(staged, list) else set()
    known = {item.name for item in getattr(cluster, "hosted_clusters", []) or []}
    hosted = []
    if isinstance(subjects, dict):
        for key, value in subjects.items():
            if key == "stagedOnly" or not isinstance(value, str) or not value:
                continue
            if value in staged_names or value not in known or any(item["name"] == value for item in hosted):
                continue
            hosted.append({"name": value, "scope": "hosted"})
    namespace = policy.get("managementSecretNamespace")
    policy["targets"] = [
        {"name": cluster.name, "scope": "management", "secretNamespace": namespace},
        *hosted,
    ]
    return policy


def hosted_identity_provider(cluster, hosted_name: str) -> dict | None:
    policy = getattr(cluster, "console_login", None) or {}
    target = next((item for item in policy.get("targets") or []
                   if item.get("scope") == "hosted" and item.get("name") == hosted_name), None)
    if target is None:
        return None
    return _provider(policy)


def validate_console_login(cluster, defaults, errors: list[str]) -> None:
    policy = getattr(cluster, "console_login", None) or {}
    if not policy:
        return
    prefix = f"OCPCluster({cluster.name}) {POLICY_NAME}"
    _reject_secret_material(policy, prefix, errors)
    for key in ("type", "providerName", "mappingMethod", "username", "group",
                "role", "secretName", "secretKey", "credentialRef", "hostedTargets",
                "managementSecretNamespace", "targets"):
        if not policy.get(key):
            errors.append(f"{prefix}: spec.{key} is required")
    if policy.get("hostedTargets") != "deployment-subjects":
        errors.append(f"{prefix}: spec.hostedTargets must select deployment subjects")
    if policy.get("type") != HTPASSWD_TYPE:
        errors.append(f"{prefix}: spec.type must be {HTPASSWD_TYPE}")
    if policy.get("secretKey") != HTPASSWD_KEY:
        errors.append(f"{prefix}: spec.secretKey must be the HTPasswd file key")
    username = policy.get("username")
    if isinstance(username, str) and (":" in username or "\n" in username):
        errors.append(f"{prefix}: spec.username must be a single htpasswd name")
    credential = policy.get("credentialRef")
    if not isinstance(credential, dict) or not credential.get("keychainService") or not credential.get("keychainAccount"):
        errors.append(f"{prefix}: spec.credentialRef keychainService and keychainAccount are required")
    targets = policy.get("targets")
    if not isinstance(targets, list) or not targets:
        errors.append(f"{prefix}: spec.targets must list at least one cluster")
        return
    names = [item.get("name") for item in targets if isinstance(item, dict)]
    if len(names) != len(set(names)) or any(not isinstance(name, str) or not name for name in names):
        errors.append(f"{prefix}: spec.targets must have distinct names")
    management = [item for item in targets if isinstance(item, dict) and item.get("scope") == "management"]
    hosted = [item for item in targets if isinstance(item, dict) and item.get("scope") == "hosted"]
    if len(management) != 1 or management[0].get("name") != cluster.name:
        errors.append(f"{prefix}: exactly one management target must name this cluster")
    known = {item.name for item in cluster.hosted_clusters}
    unknown = [item.get("name") for item in hosted if item.get("name") not in known]
    if unknown or len(management) + len(hosted) != len(targets):
        errors.append(f"{prefix}: hosted targets must be rendered hosted clusters")
    root = getattr(defaults, "input_root", None)
    if root is None:
        errors.append(f"{prefix}: selected input root is required")
        return
    for target in targets:
        if not isinstance(target, dict):
            continue
        try:
            namespace = secret_namespace(target)
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")
            continue
        path = sealed_path(Path(root), cluster.name, target["name"])
        if not path.is_file():
            continue
        problems = _sealed_problems(yaml.safe_load(path.read_text()), policy, namespace)
        errors.extend(f"{prefix}: {target['name']} {problem}" for problem in problems)


def sealed_path(root: Path, cluster_name: str, target_name: str) -> Path:
    return root / "data" / "clusters" / cluster_name / SEALED_DIR / f"{target_name}.yaml"


def sealed_secret_present(defaults, cluster_name: str, target_name: str) -> bool:
    root = getattr(defaults, "input_root", None)
    return root is not None and sealed_path(Path(root), cluster_name, target_name).is_file()


def render_console_login(cluster, defaults, files: dict[str, str]) -> dict[str, str]:
    policy = getattr(cluster, "console_login", None) or {}
    if not policy:
        return {}
    if defaults.input_root is None:
        raise ValueError("selected input root is required")
    rendered: dict[str, str] = {}
    management = next(item for item in policy["targets"] if item["scope"] == "management")
    rendered.update(_render_management(cluster, defaults, policy, management))
    _insert_resource(files, f"clusters/{cluster.name}/kustomization.yaml", "console-login.yaml")
    for target in policy["targets"]:
        if target["scope"] != "hosted":
            continue
        rendered.update(_render_hosted(cluster, defaults, policy, target))
        _insert_resource(files, f"clusters/{cluster.name}/kustomization.yaml",
                         f"{target['name']}-console-login.yaml")
        _insert_resource(files, f"clusters/{target['name']}/kustomization.yaml", "console-login.yaml")
    return rendered


def _provider(policy: dict) -> dict:
    return {
        "name": policy["providerName"],
        "mappingMethod": policy["mappingMethod"],
        "type": policy["type"],
        "htpasswd": {"fileData": {"name": policy["secretName"]}},
    }


def _binding(policy: dict, wave: int) -> dict:
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRoleBinding",
        "metadata": {"name": policy["group"], "annotations": _annotation(wave)},
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": policy["role"],
        },
        "subjects": [{
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Group",
            "name": policy["group"],
        }],
    }


def _group(policy: dict, wave: int) -> dict:
    return {
        "apiVersion": "user.openshift.io/v1",
        "kind": "Group",
        "metadata": {"name": policy["group"], "annotations": _annotation(wave)},
        "users": [policy["username"]],
    }


def _application(name: str, namespace: str, path: str, wave: int, destination: dict,
                 repo_url: str, revision: str) -> dict:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "annotations": _annotation(wave),
            "finalizers": ["resources-finalizer.argocd.argoproj.io"],
        },
        "spec": {
            "project": "default",
            "source": {
                "repoURL": repo_url,
                "targetRevision": revision,
                "path": path,
            },
            "destination": destination,
            "syncPolicy": {
                "automated": {"prune": True, "selfHeal": True},
                "syncOptions": ["SkipDryRunOnMissingResource=true"],
            },
        },
    }


def _render_management(cluster, defaults, policy: dict, target: dict) -> dict[str, str]:
    namespace = secret_namespace(target)
    sealed = _load_sealed(defaults, cluster.name, target["name"], policy, namespace)
    secret_wave = defaults.sync_wave("consoleLoginSecret")
    identity_wave = defaults.sync_wave("consoleLoginManagement")
    oauth = {
        "apiVersion": "config.openshift.io/v1",
        "kind": "OAuth",
        "metadata": {
            "name": "cluster",
            "annotations": {
                **_annotation(identity_wave),
                "argocd.argoproj.io/sync-options": "Prune=false",
            },
        },
        "spec": {"identityProviders": [_provider(policy)]},
    }
    overlay = f"apps/hcp/management/console-login/overlays/{cluster.name}"
    gitops_ns = defaults.namespace("gitops")
    app = _application(
        f"{cluster.name}-console-login", gitops_ns, overlay, identity_wave,
        {"server": "https://kubernetes.default.svc"},
        defaults.gitops_repo_url, defaults.gitops_target_revision,
    )
    resources = ["oauth.yaml", "group.yaml", "binding.yaml"]
    rendered = {
        f"{overlay}/oauth.yaml": HEADER + _k8s_yaml(oauth),
        f"{overlay}/group.yaml": HEADER + _k8s_yaml(_group(policy, identity_wave)),
        f"{overlay}/binding.yaml": HEADER + _k8s_yaml(_binding(policy, identity_wave)),
        f"clusters/{cluster.name}/console-login.yaml": HEADER + _k8s_yaml(app),
    }
    if sealed is not None:
        sealed["metadata"].setdefault("annotations", {}).update(_annotation(secret_wave))
        resources.insert(0, "console-login-sealedsecret.yaml")
        rendered[f"{overlay}/console-login-sealedsecret.yaml"] = _dump(sealed)
    rendered[f"{overlay}/kustomization.yaml"] = _kustomization(resources)
    return rendered


def _render_hosted(cluster, defaults, policy: dict, target: dict) -> dict[str, str]:
    namespace = secret_namespace(target)
    sealed = _load_sealed(defaults, cluster.name, target["name"], policy, namespace)
    secret_wave = defaults.sync_wave("consoleLoginSecret")
    guest_wave = defaults.sync_wave("consoleLoginGuest")
    hosted_overlay = (
        f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{cluster.name}/{target['name']}"
    )
    guest_overlay = f"apps/hcp/tenant/console-login/overlays/{target['name']}"
    gitops_ns = defaults.namespace("gitops")
    app = _application(
        f"{target['name']}-console-login", gitops_ns, guest_overlay, guest_wave,
        {"name": target["name"]},
        defaults.gitops_repo_url, defaults.gitops_target_revision,
    )
    rendered = {
        f"{guest_overlay}/kustomization.yaml": _kustomization(["group.yaml", "binding.yaml"]),
        f"{guest_overlay}/group.yaml": HEADER + _k8s_yaml(_group(policy, guest_wave)),
        f"{guest_overlay}/binding.yaml": HEADER + _k8s_yaml(_binding(policy, guest_wave)),
        f"clusters/{cluster.name}/{target['name']}-console-login.yaml": HEADER + _k8s_yaml(app),
        f"clusters/{target['name']}/console-login.yaml": HEADER + _k8s_yaml(app),
    }
    if sealed is not None:
        sealed["metadata"].setdefault("annotations", {}).update(_annotation(secret_wave))
        rendered[f"{hosted_overlay}/console-login-sealedsecret.yaml"] = _dump(sealed)
    return rendered


def _load_sealed(defaults, cluster_name: str, target_name: str, policy: dict, namespace: str) -> dict | None:
    path = sealed_path(Path(defaults.input_root), cluster_name, target_name)
    if not path.is_file():
        return None
    loaded = yaml.safe_load(path.read_text())
    problems = _sealed_problems(loaded, policy, namespace)
    if problems:
        raise ValueError(f"sealed console login secret for {target_name} is not usable")
    return loaded


def _sealed_problems(loaded: object, policy: dict, namespace: str) -> list[str]:
    if not isinstance(loaded, dict):
        return ["must be a mapping"]
    problems = []
    if loaded.get("kind") != "SealedSecret" or loaded.get("apiVersion") != "bitnami.com/v1alpha1":
        problems.append("must be a bitnami SealedSecret")
    metadata = loaded.get("metadata") if isinstance(loaded.get("metadata"), dict) else {}
    if metadata.get("name") != policy.get("secretName") or metadata.get("namespace") != namespace:
        problems.append("name or namespace differs from the declared secret")
    spec = loaded.get("spec") if isinstance(loaded.get("spec"), dict) else {}
    encrypted = spec.get("encryptedData") if isinstance(spec.get("encryptedData"), dict) else {}
    if set(encrypted) != {policy.get("secretKey")} or not encrypted.get(policy.get("secretKey")):
        problems.append("encrypted data must contain only the declared file key")
    if "stringData" in loaded or "data" in loaded:
        problems.append("must not carry secret data")
    blob = "\n".join(str(value) for value in encrypted.values())
    if "$2a$" in blob or "$2b$" in blob or "$2y$" in blob:
        problems.append("ciphertext exposes a password hash")
    return problems


def _reject_secret_material(node: object, prefix: str, errors: list[str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if str(key).lower() in FORBIDDEN_KEYS:
                errors.append(f"{prefix}: spec must not carry credential material")
            _reject_secret_material(value, prefix, errors)
        return
    if isinstance(node, list):
        for item in node:
            _reject_secret_material(item, prefix, errors)
        return
    if isinstance(node, str) and any(marker in node for marker in ("$2a$", "$2b$", "$2y$")):
        errors.append(f"{prefix}: spec must not carry a password hash")


def _insert_resource(files: dict[str, str], path: str, resource: str) -> None:
    text = files.get(path)
    if text is None:
        raise ValueError(f"console login requires generated kustomization {path}")
    line = f"- {resource}\n"
    if line in text:
        return
    lines = text.splitlines(keepends=True)
    for index, existing in enumerate(lines):
        if existing == "patches:\n" or existing == "patches:":
            lines.insert(index, line)
            files[path] = "".join(lines)
            return
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(line)
    files[path] = "".join(lines)


def _kustomization(resources: list[str]) -> str:
    return HEADER + _k8s_yaml({
        "apiVersion": "kustomize.config.k8s.io/v1beta1",
        "kind": "Kustomization",
        "resources": resources,
    })


def _dump(document: dict) -> str:
    body = yaml.safe_dump(document, sort_keys=False, width=10**9, explicit_start=True)
    return HEADER + body
