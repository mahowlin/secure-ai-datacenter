"""The GitOps exception register is declared input and copied into the render."""

from __future__ import annotations

import copy
import importlib.util
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from gitops_exceptions import (
    REGISTER_OUTPUT,
    path_matches,
    rendered_register,
    validate_document,
    validate_register,
)
from site_identity import load_identity


def _document() -> dict:
    return yaml.safe_load((REPO_ROOT / "data/gitops/exceptions.yaml").read_text())


def _entry(**overrides) -> dict:
    entry = {
        "path": "manual/example.yaml",
        "category": "waiver",
        "reason": "temporary published file",
        "owner": "platform",
        "decision": {"bead": "example-note"},
        "expiry": "2099-01-01",
    }
    entry.update(overrides)
    return entry


class GitOpsExceptionRegisterTests(unittest.TestCase):
    def test_checked_in_register_is_valid(self):
        rendered = {
            REGISTER_OUTPUT,
            "charts/cilium-config/templates/ciliumconfig-resolved.yaml",
            "charts/cilium-config/templates/postsync-resolved-restart-hook.yaml",
            "apps/sealed-secrets/hosted-writer-intent.json",
            "clusters/foundation/core/argocd-config.yaml",
            "clusters/foundation/core/platform-idms.yaml",
            "clusters/foundation/isovalent/kustomization.yaml",
            "clusters/foundation/isovalent/vector-exporter.yaml",
        }
        self.assertEqual(validate_register(REPO_ROOT, rendered), [])
        self.assertGreater(len(_document()["spec"]["exceptions"]), 0)

    def test_rendered_copy_matches_the_declared_input(self):
        source = (REPO_ROOT / "data/gitops/exceptions.yaml").read_text()
        self.assertEqual(rendered_register(REPO_ROOT), {REGISTER_OUTPUT: source})
        repo = load_identity(REPO_ROOT)["generatedRepos"]["gitops"]
        generated = REPO_ROOT / "generated" / "repos" / repo / REGISTER_OUTPUT
        self.assertEqual(generated.read_text(), source)

    def test_two_copies_match(self):
        self.assertEqual(rendered_register(REPO_ROOT), rendered_register(REPO_ROOT))

    def test_a_changed_register_is_what_gets_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            destination = root / "data" / "gitops"
            destination.mkdir(parents=True)
            text = "kind: replaced\n"
            (destination / "exceptions.yaml").write_text(text)
            self.assertEqual(rendered_register(root)[REGISTER_OUTPUT], text)

    def test_exception_cannot_cover_a_rendered_path(self):
        document = _document()
        document["spec"]["exceptions"].append(_entry(path=REGISTER_OUTPUT, expiry="2099-01-01"))
        errors = validate_document(REPO_ROOT, document, {REGISTER_OUTPUT}, date(2026, 10, 5))
        self.assertTrue(any("overlaps rendered path" in error for error in errors))

    def test_changed_path_follows_the_input(self):
        document = _document()
        document["spec"]["exceptions"].append(
            _entry(path="manual/**", decision={"path": "docs/validations/README.md"}, category="bootstrap")
        )
        document["spec"]["exceptions"][-1].pop("expiry")
        self.assertEqual(validate_document(REPO_ROOT, document, {REGISTER_OUTPUT}), [])
        changed = copy.deepcopy(document)
        changed["spec"]["exceptions"][0]["path"] = f"{REGISTER_OUTPUT[:6]}*"
        errors = validate_document(REPO_ROOT, changed, {REGISTER_OUTPUT})
        self.assertTrue(errors)

    def test_expired_waiver_fails(self):
        document = _document()
        document["spec"]["exceptions"].append(_entry(expiry="2000-01-01"))
        errors = validate_document(REPO_ROOT, document, set(), date(2026, 10, 5))
        self.assertTrue(any("2000-01-01" in error for error in errors))

    def test_missing_decision_path_fails(self):
        document = _document()
        document["spec"]["exceptions"].append(_entry(decision={"path": "docs/missing.md"}))
        errors = validate_document(REPO_ROOT, document, set())
        self.assertTrue(any("does not exist" in error for error in errors))

    def test_every_entry_has_owner_category_reason_and_decision(self):
        for entry in _document()["spec"]["exceptions"]:
            self.assertTrue(entry["owner"].strip())
            self.assertTrue(entry["category"].strip())
            self.assertTrue(entry["reason"].strip())
            decision = entry["decision"]
            self.assertTrue(decision.get("bead") or decision.get("path"))

    def test_hcp_root_waivers_expire_when_pull_request_255_lands(self):
        waivers = [
            entry for entry in _document()["spec"]["exceptions"] if entry["category"] == "waiver"
        ]
        self.assertEqual(len(waivers), 10)
        for entry in waivers:
            self.assertEqual(entry["expiry"], "2027-01-05")
            self.assertIn("pull request 255", entry["reason"])
            self.assertNotIn(":", entry["reason"])

    def test_waiver_without_owner_decision_or_expiry_fails(self):
        for field in ("owner", "decision", "expiry"):
            document = _document()
            document["spec"]["exceptions"].append(_entry())
            document["spec"]["exceptions"][-1].pop(field)
            errors = validate_document(REPO_ROOT, document, set(), date(2026, 10, 5))
            self.assertTrue(errors, field)

    def test_bad_bead_id_fails(self):
        document = _document()
        document["spec"]["exceptions"].append(_entry(decision={"bead": "not-a-bead"}))
        self.assertTrue(validate_document(REPO_ROOT, document, set()))

    def test_globs_stay_inside_one_path_unless_doubled(self):
        self.assertTrue(path_matches("manual/**", "manual/a.yaml"))
        self.assertTrue(path_matches("manual/**", "manual/a/b.yaml"))
        self.assertFalse(path_matches("manual/*", "manual/a/b.yaml"))
        self.assertFalse(path_matches("../secret", "secret"))

    def test_contract_inputs_include_the_register(self):
        spec = importlib.util.spec_from_file_location(
            "write_artifact_manifest",
            REPO_ROOT / "scripts" / "write-artifact-manifest.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        inputs = module.CONTRACT_INPUTS
        self.assertIn("data/gitops/", inputs)
        self.assertIn("schemas/gitops-exception-register-v1.schema.json", inputs)


if __name__ == "__main__":
    unittest.main()
