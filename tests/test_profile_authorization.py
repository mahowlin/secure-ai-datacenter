from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPO_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from platform_contract import ProfileContractError
import platform_contract.legacy as legacy


class ProfileAuthorizationTests(unittest.TestCase):
    def test_partial_legacy_release_fails_actionably(self) -> None:
        release_path = REPO_ROOT / "platform-release.yaml"
        release_data = yaml.safe_load(release_path.read_text())
        release_data = copy.deepcopy(release_data)
        release_data["releases"]["saif-ocp-4.19-cilium-1.18"]["cilium"].pop("operator_image")
        original_read = legacy._read_yaml

        def read(candidate: Path):
            if Path(candidate).resolve() == release_path.resolve():
                return copy.deepcopy(release_data)
            return original_read(candidate)

        with patch.object(legacy, "_read_yaml", side_effect=read):
            with self.assertRaisesRegex(ProfileContractError, "operator_image.*required"):
                legacy.resolve_legacy_root(REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
