"""Exercise the source ILB renewal rules with Prometheus' rule evaluator."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/ilb/example-tenant-poc/credential-renewal.yaml"
SCENARIOS = ROOT / "tests/promql/ilb-credential-renewal.test.yaml"


class ILBRenewalPromQLTests(unittest.TestCase):
    def test_positive_negative_paused_missing_and_recovery(self):
        promtool = os.environ.get("PROMTOOL_BIN") or shutil.which("promtool")
        if not promtool:
            self.skipTest("promtool is unavailable")
        source = next(item for item in yaml.safe_load_all(SOURCE.read_text())
                      if item.get("kind") == "PrometheusRule")
        with tempfile.TemporaryDirectory(prefix="ilb-promql-") as directory:
            rules = Path(directory) / "rules.yaml"
            scenarios = Path(directory) / "scenarios.yaml"
            rules.write_text(yaml.safe_dump({"groups": source["spec"]["groups"]}))
            test = yaml.safe_load(SCENARIOS.read_text())
            test["rule_files"] = [str(rules)]
            scenarios.write_text(yaml.safe_dump(test, sort_keys=False))
            result = subprocess.run([promtool, "test", "rules", str(scenarios)],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
