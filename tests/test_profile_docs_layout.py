#!/usr/bin/env python3
"""Fail when docs/ grows a new folder or a singular/plural twin."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
ALLOWED = frozenset({"architectures", "diagrams", "validations"})


def singular_plural_twins(names: set[str]) -> list[tuple[str, str]]:
    twins = []
    for name in sorted(names):
        if name.endswith("s") and name[:-1] in names:
            twins.append((name[:-1], name))
    return twins


class DocsLayoutTests(unittest.TestCase):
    def test_docs_folders_stay_inside_the_existing_set(self) -> None:
        found = {path.name for path in DOCS.iterdir() if path.is_dir()}
        self.assertEqual(found, set(ALLOWED))

    def test_live_tree_has_no_singular_plural_twin(self) -> None:
        found = {path.name for path in DOCS.iterdir() if path.is_dir()}
        self.assertEqual(singular_plural_twins(found), [])

    def test_twin_check_rejects_architecture_beside_architectures(self) -> None:
        self.assertEqual(
            singular_plural_twins({"architecture", "architectures"}),
            [("architecture", "architectures")],
        )

    def test_unknown_folder_is_outside_the_allowlist(self) -> None:
        found = set(ALLOWED) | {"notes"}
        self.assertNotEqual(found, set(ALLOWED))


if __name__ == "__main__":
    unittest.main()
