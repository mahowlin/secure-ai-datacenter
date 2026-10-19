from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from platform_contract import SCHEMA_VERSION, WORKER_PROFILE_REFS


class ProfileSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schema = json.loads((REPO_ROOT / "schemas" / "saif-platform-v2.schema.json").read_text())

    def test_schema_declares_versioned_closed_documents(self) -> None:
        self.assertEqual(self.schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertEqual(self.schema["$defs"]["schemaVersion"]["const"], SCHEMA_VERSION)
        for definition in (
            "managementCluster",
            "hostedCluster",
            "releaseTransition",
            "openShiftReleaseCatalog",
            "iepReleaseCatalog",
            "releaseCompatibilityCatalog",
            "managementProfileCatalog",
            "workerProfileCatalog",
            "authorizationGateCatalog",
        ):
            self.assertFalse(self.schema["$defs"][definition]["additionalProperties"])

    def test_worker_profile_vocabulary_is_exact(self) -> None:
        spec = self.schema["$defs"]["hostedCluster"]["properties"]["spec"]["properties"]
        enum = set(spec["workerNetworkProfileRef"]["enum"])
        self.assertEqual(enum, WORKER_PROFILE_REFS)
        self.assertEqual(spec["workerNetworks"]["maxItems"], 1)


if __name__ == "__main__":
    unittest.main()
