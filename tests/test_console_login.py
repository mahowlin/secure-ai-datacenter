"""Console login follows the declared identity and target list."""

from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gitops_renderer import render_gitops
from gitops_renderer.console_login import expand_console_login
from resolver import resolve


def _cluster(platform):
    return next(item for item in platform.clusters if item.console_login)


def _providers(files: dict[str, str], cluster_name: str) -> dict[str, list[str]]:
    found = {}
    prefix = f"apps/hcp/management/hosted-cluster-lifecycle/overlays/{cluster_name}/"
    for path, content in files.items():
        if not path.startswith(prefix) or not path.endswith("/hostedcluster.yaml"):
            continue
        hosted = path.split("/")[-2]
        document = yaml.safe_load(content)
        providers = document["spec"]["configuration"].get("oauth", {}).get("identityProviders", [])
        found[hosted] = [item["name"] for item in providers]
    return found


class ConsoleLoginTests(unittest.TestCase):
    def test_declared_targets_render_group_binding_without_credential_material(self) -> None:
        platform = resolve(REPO_ROOT)
        cluster = _cluster(platform)
        policy = cluster.console_login
        files = render_gitops(cluster, platform.fabric.fabric_asn, platform.defaults)
        providers = _providers(files, cluster.name)
        hosted = [item["name"] for item in policy["targets"] if item["scope"] == "hosted"]
        staged = set((cluster.hosted_cluster_lifecycle.get("deploymentSubjects") or {}).get("stagedOnly") or [])
        self.assertTrue(hosted)
        self.assertTrue(set(hosted).isdisjoint(staged))
        for name, names in providers.items():
            if name in hosted:
                self.assertEqual(names, [policy["providerName"]])
            else:
                self.assertEqual(names, [])
        management = files[
            f"apps/hcp/management/console-login/overlays/{cluster.name}/binding.yaml"
        ]
        binding = yaml.safe_load(management)
        self.assertEqual(binding["subjects"], [{
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "Group",
            "name": policy["group"],
        }])
        self.assertEqual(binding["roleRef"]["name"], policy["role"])
        login_blob = "\n".join(
            content for path, content in files.items() if "console-login" in path
        )
        self.assertNotIn("$2a$", login_blob)
        self.assertNotIn("$2b$", login_blob)
        self.assertNotIn("$2y$", login_blob)
        self.assertNotIn("stringData:", login_blob)
        self.assertIn(policy["username"], files[
            f"apps/hcp/management/console-login/overlays/{cluster.name}/group.yaml"
        ])

    def test_changed_provider_and_target_follow_the_inputs(self) -> None:
        platform = resolve(REPO_ROOT)
        cluster = copy.deepcopy(_cluster(platform))
        hosted = [item["name"] for item in cluster.console_login["targets"] if item["scope"] == "hosted"]
        self.assertTrue(hosted)
        cluster.console_login["providerName"] = "renamed-provider"
        files = render_gitops(cluster, platform.fabric.fabric_asn, platform.defaults)
        providers = _providers(files, cluster.name)
        for name in hosted:
            self.assertEqual(providers[name], ["renamed-provider"])
        oauth = yaml.safe_load(files[
            f"apps/hcp/management/console-login/overlays/{cluster.name}/oauth.yaml"
        ])
        self.assertEqual(oauth["spec"]["identityProviders"][0]["name"], "renamed-provider")

    def test_removing_a_deployment_subject_removes_its_login(self) -> None:
        platform = resolve(REPO_ROOT)
        cluster = copy.deepcopy(_cluster(platform))
        subjects = cluster.hosted_cluster_lifecycle["deploymentSubjects"]
        key = next(name for name, value in subjects.items() if isinstance(value, str))
        removed = subjects.pop(key)
        cluster.console_login = expand_console_login(cluster, cluster.console_login)
        selected = [item["name"] for item in cluster.console_login["targets"]]
        self.assertNotIn(removed, selected)


if __name__ == "__main__":
    unittest.main()
