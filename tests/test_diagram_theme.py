#!/usr/bin/env python3
"""Focused compatibility checks for optional diagram zone colors."""

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
THEME_PATH = ROOT / "docs" / "diagrams" / "diagram_theme.py"
SPEC = importlib.util.spec_from_file_location("diagram_theme", THEME_PATH)
assert SPEC and SPEC.loader
diagram_theme = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(diagram_theme)


class DiagramThemeZoneTests(unittest.TestCase):
    def test_default_zone_style_remains_unchanged(self) -> None:
        rendered = "\n".join(
            diagram_theme._zone(
                {"id": "default", "x": 1, "y": 2, "width": 3, "height": 4}
            )
        )
        self.assertIn(
            'fill="#FFFFFF" fill-opacity="0.72" stroke="#C7D1E0"',
            rendered,
        )

    def test_zone_can_override_fill_opacity_and_stroke(self) -> None:
        rendered = "\n".join(
            diagram_theme._zone(
                {
                    "id": "hosted",
                    "x": 1,
                    "y": 2,
                    "width": 3,
                    "height": 4,
                    "fill": "#F2ECFF",
                    "fill_opacity": 0.8,
                    "stroke": "#8B6BD9",
                }
            )
        )
        self.assertIn(
            'fill="#F2ECFF" fill-opacity="0.8" stroke="#8B6BD9"',
            rendered,
        )


if __name__ == "__main__":
    unittest.main()
