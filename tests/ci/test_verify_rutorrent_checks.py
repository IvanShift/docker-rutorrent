"""Behavior tests for the public ruTorrent check-runs release gate."""

import copy
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(
    os.environ.get(
        "RUTORRENT_CHECK_SCRIPT", REPO_ROOT / "scripts" / "verify_rutorrent_checks.py"
    )
)
FIXTURES = Path(__file__).parent / "fixtures"
SHA = "1fa8defc018bf769dd42147323ab02d460431357"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def verify(payload, sha=SHA):
    return subprocess.run(
        [sys.executable, str(SCRIPT), sha],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


class CheckRunGateTests(unittest.TestCase):
    def test_exact_six_successes_allow_unrelated_advisory_failure(self):
        result = verify(fixture("checks-success.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("six required checks succeeded", result.stdout)

    def test_incomplete_response_rejects_missing_phpstan(self):
        result = verify(fixture("checks-incomplete.json"))
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("phpstan", result.stderr)

    def test_empty_response_is_rejected(self):
        result = verify({"total_count": 0, "check_runs": []})
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("No check runs", result.stderr)

    def test_failed_required_check_is_rejected(self):
        payload = fixture("checks-success.json")
        payload["check_runs"][0]["conclusion"] = "failure"
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("php (7.4)", result.stderr)

    def test_pending_required_check_is_rejected(self):
        payload = fixture("checks-success.json")
        payload["check_runs"][0].update(status="in_progress", conclusion=None)
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("php (7.4)", result.stderr)

    def test_similar_name_cannot_replace_required_name(self):
        payload = fixture("checks-success.json")
        payload["check_runs"][5]["name"] = "phpstan (8.1)"
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("phpstan", result.stderr)

    def test_check_from_wrong_commit_is_rejected(self):
        payload = fixture("checks-success.json")
        payload["check_runs"][0]["head_sha"] = "0" * 40
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("php (7.4)", result.stderr)

    def test_truncated_page_is_rejected(self):
        payload = fixture("checks-success.json")
        payload["total_count"] += 1
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Incomplete check-runs response", result.stderr)

    def test_duplicate_failed_required_check_is_rejected(self):
        payload = fixture("checks-success.json")
        failed = copy.deepcopy(payload["check_runs"][0])
        failed.update(id=109, conclusion="failure")
        payload["check_runs"].append(failed)
        payload["total_count"] += 1
        result = verify(payload)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("php (7.4)", result.stderr)

    def test_invalid_json_is_rejected(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), SHA],
            input="{",
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Invalid check-runs JSON", result.stderr)


if __name__ == "__main__":
    unittest.main()
