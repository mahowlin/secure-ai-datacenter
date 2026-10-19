"""Selected writer composition must preserve target ownership and fail closed."""
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from stage_timescape_writer import compose


class WriterCompositionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source, self.target, self.artifact = [root / x for x in ("source", "target", "artifact")]
        self.cluster = "renamed-guest"
        self.parent = "renamed-management"
        self.relative = Path("apps/sealed-secrets/renamed-guest/telemetry/writer.yaml")
        self.identity = {"hostedClusterUid": "hc-uid", "controllerUid": "controller-uid",
                         "writerNamespaceUid": "namespace-uid", "certificateSha256": "a" * 64}
        manifest = {"managementCluster": self.parent, "targets": [{"cluster": self.cluster,
                    "namespace": "telemetry", "secretName": "writer",
                    "sealedPath": self.relative.as_posix(),
                    "keys": ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]}],
                    "enrollments": [{"cluster": self.cluster, "enrollment": "selected", "centralIngest": True}]}
        for base in (self.source, self.target):
            self.put(base / "apps/sealed-secrets/hosted-writer-intent.json", json.dumps(manifest))
        self.index = Path("clusters") / self.parent / "kustomization.yaml"
        self.child = self.cluster + "-sealed-secrets.yaml"
        self.put(self.source / self.index, yaml.safe_dump({"resources": [self.child]}))
        self.original = "# target retained\nresources:\n- retained.yaml\npatches:\n- patch: keep-exact-text\n"
        self.put(self.target / self.index, self.original)
        app = {"kind": "Application", "metadata": {"name": self.cluster + "-sealed-secrets"},
               "spec": {"source": {"path": self.relative.parent.as_posix()},
                        "destination": {"name": self.cluster}}}
        self.put(self.source / self.index.parent / self.child, yaml.safe_dump(app))
        metadata = {"name": "writer", "namespace": "telemetry"}
        self.secret = {"apiVersion": "bitnami.com/v1alpha1", "kind": "SealedSecret",
                       "metadata": metadata, "spec": {"template": {"metadata": dict(metadata)},
                       "encryptedData": {"AWS_ACCESS_KEY_ID": "cipher-a", "AWS_SECRET_ACCESS_KEY": "cipher-b"}}}
        self.save_artifact()

    def put(self, path, text):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def save_artifact(self):
        payload = yaml.safe_dump(self.secret)
        self.put(self.artifact / self.relative, payload)
        self.receipt = dict(self.identity, cluster=self.cluster, sealedPath=self.relative.as_posix(),
                            sealedSha256=hashlib.sha256(payload.encode()).hexdigest())
        self.put(self.artifact / "identity.json", json.dumps(self.receipt))

    def run_compose(self):
        return compose(self.source, self.target, self.artifact, self.cluster, self.identity)

    def test_changed_names_preserve_retained_index_and_idempotence(self):
        paths = self.run_compose()
        expected = self.original.replace("patches:", "- " + self.child + "\npatches:")
        self.assertEqual((self.target / self.index).read_text(), expected)
        self.assertEqual(len(paths), 3)
        before = {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()}
        self.run_compose()
        self.assertEqual(before, {p.relative_to(self.target): p.read_bytes() for p in self.target.rglob("*") if p.is_file()})

    def test_missing_artifact_and_changed_identity_make_no_writes(self):
        for key in self.identity:
            with self.subTest(key=key):
                bad = dict(self.identity, **{key: "different"})
                with self.assertRaisesRegex(ValueError, "identity differs"):
                    compose(self.source, self.target, self.artifact, self.cluster, bad)
                self.assertEqual((self.target / self.index).read_text(), self.original)
                self.assertFalse((self.target / self.relative).exists())
        (self.artifact / self.relative).unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_compose()
        self.assertEqual((self.target / self.index).read_text(), self.original)

    def test_scope_and_digest_fail_before_enrollment(self):
        self.secret["metadata"]["annotations"] = {"sealedsecrets.bitnami.com/namespace-wide": "true"}
        self.save_artifact()
        with self.assertRaisesRegex(ValueError, "strict scope"):
            self.run_compose()
        self.assertEqual((self.target / self.index).read_text(), self.original)
        del self.secret["metadata"]["annotations"]
        self.save_artifact()
        self.receipt["sealedSha256"] = "0" * 64
        self.put(self.artifact / "identity.json", json.dumps(self.receipt))
        with self.assertRaisesRegex(ValueError, "digest differs"):
            self.run_compose()
        self.assertFalse((self.target / self.relative).exists())

    def test_excluded_and_cross_guest_app_are_rejected(self):
        app = yaml.safe_load((self.source / self.index.parent / self.child).read_text())
        app["spec"]["destination"]["name"] = "other-guest"
        self.put(self.source / self.index.parent / self.child, yaml.safe_dump(app))
        with self.assertRaisesRegex(ValueError, "child App differs"):
            self.run_compose()
        manifest_path = self.source / "apps/sealed-secrets/hosted-writer-intent.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["enrollments"][0]["enrollment"] = "excluded"
        for root in (self.source, self.target):
            self.put(root / "apps/sealed-secrets/hosted-writer-intent.json", json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "uniquely selected"):
            self.run_compose()

    def test_empty_document_prefix_is_preserved_but_multiple_secrets_rejected(self):
        payload = "---\n---\n" + yaml.safe_dump(self.secret)
        self.put(self.artifact / self.relative, payload)
        self.receipt["sealedSha256"] = hashlib.sha256(payload.encode()).hexdigest()
        self.put(self.artifact / "identity.json", json.dumps(self.receipt))
        self.run_compose()
        self.assertEqual((self.target / self.relative).read_text(), payload)
        (self.target / self.relative).unlink()
        payload += "---\n" + yaml.safe_dump(self.secret)
        self.put(self.artifact / self.relative, payload)
        self.receipt["sealedSha256"] = hashlib.sha256(payload.encode()).hexdigest()
        self.put(self.artifact / "identity.json", json.dumps(self.receipt))
        with self.assertRaisesRegex(ValueError, "exactly one Secret"):
            self.run_compose()
        self.assertFalse((self.target / self.relative).exists())
