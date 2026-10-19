"""Focused revision and alternate-site checks for the transition packet builder."""

import argparse
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from scripts.build_hcp_name_transition_packet import build


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def write(repo, path, value):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(yaml.safe_dump(value))


class TransitionPacketTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source, self.target = root / "source", root / "target"
        for repo in (self.source, self.target):
            repo.mkdir()
            git(repo, "init", "-q")
            git(repo, "config", "user.email", "test@example.invalid")
            git(repo, "config", "user.name", "Test")

        self.review_path = "data/reviews/site.yaml"
        self.input_path = "data/clusters/site/cluster.yaml"
        self.review = {
            "metadata": {"name": "site-transition"},
            "targetRepo": "site-target",
            "managementInput": self.input_path,
            "sourceInputs": [self.input_path],
            "selectedRoles": ["first", "second"],
            "targetPaths": {
                "oldStage": "clusters/_staged/site-old",
                "newStage": "clusters/_staged/site-new",
                "nodePool": "apps/site/{subject}/nodepool-workers.yaml",
            },
            "expectedWorkerCounts": {
                "nameAdoption": {"site-east": 2, "site-west": 1},
                "workerActivation": {"site-east": 4, "site-west": 4},
            },
            "applicationNames": {"site-old": "site-new"},
        }
        write(self.source, self.review_path, self.review)
        shutil.copytree(
            Path(__file__).resolve().parents[1] / "data" / "defaults",
            self.source / "data" / "defaults",
        )
        write(self.source, self.input_path, {
            "metadata": {"name": "site"},
            "spec": {"day2GitOps": {"hostedClusterLifecycle": {
                "deploymentSubjects": {"first": "site-east", "second": "site-west"}
            }}},
        })
        self.source_ref = commit(self.source, "site input")

        old = {"metadata": {"name": "site-old", "namespace": "argocd"},
               "spec": {"destination": {"server": "https://example.invalid"},
                        "source": {"path": "apps/site/old"}}}
        new = {"metadata": {"name": "site-new", "namespace": "argocd"},
               "spec": {"destination": old["spec"]["destination"],
                        "source": {"path": "apps/site/new"}}}
        write(self.target, "clusters/_staged/site-old/kustomization.yaml", {"resources": ["site-old.yaml"]})
        write(self.target, "clusters/_staged/site-old/site-old.yaml", old)
        self.main = commit(self.target, "old stage")
        write(self.target, "clusters/_staged/site-new/kustomization.yaml", {"resources": ["site-new.yaml"]})
        write(self.target, "clusters/_staged/site-new/site-new.yaml", new)
        self.prepare = commit(self.target, "prepare")
        for subject, replicas in self.review["expectedWorkerCounts"]["nameAdoption"].items():
            write(self.target, f"apps/site/{subject}/nodepool-workers.yaml", {"spec": {"replicas": replicas}})
        self.adoption = commit(self.target, "adopt")
        for subject, replicas in self.review["expectedWorkerCounts"]["workerActivation"].items():
            write(self.target, f"apps/site/{subject}/nodepool-workers.yaml", {"spec": {"replicas": replicas}})
        self.activation = commit(self.target, "activate")
        for subject, replicas in self.review["expectedWorkerCounts"]["nameAdoption"].items():
            write(self.target, f"apps/site/{subject}/nodepool-workers.yaml", {"spec": {"replicas": replicas}})
        self.worker_inverse = commit(self.target, "undo activation")
        for subject in self.review["expectedWorkerCounts"]["nameAdoption"]:
            (self.target / f"apps/site/{subject}/nodepool-workers.yaml").unlink()
        self.name_inverse = commit(self.target, "undo adoption")

    def args(self, source_ref=None):
        return argparse.Namespace(
            source_root=self.source, source_ref=source_ref or self.source_ref,
            review_input=self.review_path, target_root=self.target,
            main=self.main, prepare=self.prepare, adoption=self.adoption,
            name_inverse=self.name_inverse, activation=self.activation,
            worker_inverse=self.worker_inverse,
        )

    def test_alternate_site_and_counts_come_from_review_input(self):
        packet = build(self.args())
        self.assertEqual(packet["workerCounts"]["nameAdoption"], {"site-east": 2, "site-west": 1})
        self.assertEqual(packet["workerCounts"]["workerActivation"], {"site-east": 4, "site-west": 4})
        self.assertEqual(packet["appMappings"][0]["new"], "site-new")

    def test_input_hash_is_bound_to_selected_revision(self):
        packet = build(self.args())
        # An uncommitted edit cannot be presented as the selected source revision.
        changed_input = yaml.safe_load((self.source / self.input_path).read_text())
        changed_input["metadata"]["name"] = "changed-site"
        write(self.source, self.input_path, changed_input)
        self.assertEqual(build(self.args())["source"], packet["source"])
        next_ref = commit(self.source, "changed input")
        self.assertNotEqual(build(self.args(next_ref))["source"], packet["source"])


if __name__ == "__main__":
    unittest.main()
