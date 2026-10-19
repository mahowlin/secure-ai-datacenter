"""The GitOps operator subscription follows the declared adoption input."""

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
from resolver import resolve


def _cluster(platform):
    return next(item for item in platform.clusters if item.name and item.hosted_cluster_lifecycle)


def _adopted(defaults):
    found = []
    for name in sorted(defaults.gitops_operator_subscriptions):
        subscription = defaults.operator_subscription(name)
        if subscription.get("placement") == "management-operators":
            found.append(subscription)
    return found


class GitOpsOperatorSubscriptionTests(unittest.TestCase):
    def test_adopted_subscription_matches_the_input_and_skips_install_namespace(self) -> None:
        platform = resolve(REPO_ROOT)
        cluster = _cluster(platform)
        adopted = _adopted(platform.defaults)
        self.assertEqual(len(adopted), 1)
        subscription = adopted[0]
        first = render_gitops(cluster, platform.fabric.fabric_asn, platform.defaults)
        second = render_gitops(cluster, platform.fabric.fabric_asn, platform.defaults)
        prefix = f"apps/hcp/management/operators/overlays/{cluster.name}/{subscription['overlay']}"
        self.assertEqual(first[f"{prefix}/subscription.yaml"], second[f"{prefix}/subscription.yaml"])
        self.assertNotIn(f"{prefix}/namespace.yaml", first)
        self.assertNotIn(f"{prefix}/operatorgroup.yaml", first)
        document = yaml.safe_load(first[f"{prefix}/subscription.yaml"])
        self.assertEqual(document["spec"], {
            "channel": subscription["channel"],
            "name": subscription["name"],
            "source": subscription["source"],
            "sourceNamespace": subscription["sourceNamespace"],
            "installPlanApproval": subscription["installPlanApproval"],
        })
        self.assertNotIn("startingCSV", document["spec"])
        self.assertNotIn("config", document["spec"])
        self.assertEqual(
            document["metadata"]["annotations"]["argocd.argoproj.io/sync-options"],
            subscription["resourceSyncOptions"],
        )
        parent = yaml.safe_load(first[
            f"apps/hcp/management/operators/overlays/{cluster.name}/kustomization.yaml"
        ])
        self.assertIn(subscription["overlay"], parent["resources"])

    def test_changed_channel_follows_the_input(self) -> None:
        platform = resolve(REPO_ROOT)
        cluster = _cluster(platform)
        defaults = copy.deepcopy(platform.defaults)
        subscription = _adopted(defaults)[0]
        defaults.gitops_operator_subscriptions[subscription["name"]]["channel"] = "renamed-channel"
        files = render_gitops(cluster, platform.fabric.fabric_asn, defaults)
        path = (
            f"apps/hcp/management/operators/overlays/{cluster.name}/"
            f"{subscription['overlay']}/subscription.yaml"
        )
        self.assertEqual(yaml.safe_load(files[path])["spec"]["channel"], "renamed-channel")


if __name__ == "__main__":
    unittest.main()
