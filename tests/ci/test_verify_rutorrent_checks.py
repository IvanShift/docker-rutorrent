"""Behavior tests for the public ruTorrent check-runs release gate."""

import copy
import io
import json
import os
import subprocess
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(
    os.environ.get(
        "RUTORRENT_CHECK_SCRIPT", REPO_ROOT / "scripts" / "verify_rutorrent_checks.py"
    )
)
FIXTURES = Path(__file__).parent / "fixtures"
SHA = "d108dddd286cd5eca9173b91b2cdee64adb27e6a"


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


# Test transport separately from the offline stdin gate, without live requests.

sys.path.insert(0, str(REPO_ROOT / "scripts"))
import verify_rutorrent_checks as gate  # noqa: E402 - repository helper path setup


class CheckRunTransportTests(unittest.TestCase):
    def test_required_check_must_be_from_github_actions(self):
        for app in (
            {"id": 7, "slug": "github-actions"},
            {"id": 15368, "slug": "fake"},
            None,
        ):
            with self.subTest(app=app):
                payload = fixture("checks-success.json")
                payload["check_runs"][0]["app"] = app
                result = verify(payload)
                self.assertEqual(result.returncode, 1)
                self.assertIn("not produced by GitHub Actions", result.stderr)

    def test_duplicate_id_cannot_satisfy_different_required_checks(self):
        payload = fixture("checks-success.json")
        payload["check_runs"][1]["id"] = payload["check_runs"][0]["id"]
        result = verify(payload)
        self.assertEqual(result.returncode, 1)
        self.assertIn("duplicate run ID", result.stderr)

    def test_second_page_contains_required_checks_and_is_verified(self):
        success = fixture("checks-success.json")
        advisory = [dict(id=1000 + i, name=f"advisory-{i}") for i in range(100)]
        requester = mock.Mock(
            side_effect=[
                {"total_count": 108, "check_runs": advisory},
                {"total_count": 108, "check_runs": success["check_runs"]},
            ]
        )
        response = gate.fetch("IvanShift/ruTorrent", SHA, "test-token", requester)
        gate.verify(response, SHA)
        self.assertEqual(requester.call_count, 2)
        self.assertIn("page=2", requester.call_args.args[0])
        self.assertIn("app_id=15368", requester.call_args.args[0])
        self.assertEqual(requester.call_args.args[1], "test-token")

    def test_incomplete_or_changing_pagination_is_rejected(self):
        page = fixture("checks-success.json")
        page["total_count"] += 1
        for second in (
            {"total_count": 9, "check_runs": []},
            {"total_count": 10, "check_runs": []},
        ):
            with self.subTest(second=second):
                with self.assertRaisesRegex(
                    ValueError, "Incomplete|changed during pagination"
                ):
                    gate.fetch(
                        "IvanShift/ruTorrent",
                        SHA,
                        None,
                        mock.Mock(side_effect=[page, second]),
                    )

    def test_repeated_page_is_rejected(self):
        page = fixture("checks-success.json")
        page["total_count"] *= 2
        with self.assertRaisesRegex(ValueError, "duplicate or missing run ID"):
            gate.fetch(
                "IvanShift/ruTorrent", SHA, None, mock.Mock(side_effect=[page, page])
            )

    def test_transient_http_failure_retries_and_uses_authenticated_api_request(self):
        opener = mock.Mock(
            side_effect=[
                urllib.error.HTTPError(
                    "https://api.github.com", 503, "Unavailable", {}, None
                ),
                io.BytesIO(b'{"ok":true}'),
            ]
        )
        sleep = mock.Mock()
        self.assertEqual(
            gate.request_json(
                "https://api.github.com/test", "secret-token", opener, sleep
            ),
            {"ok": True},
        )
        self.assertEqual(opener.call_count, 2)
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        self.assertEqual(opener.call_args.kwargs["timeout"], 30)
        sleep.assert_called_once_with(1)

    def test_forbidden_response_is_not_retried_or_reported_as_invalid_json(self):
        opener = mock.Mock(
            side_effect=urllib.error.HTTPError(
                "https://api.github.com", 403, "Forbidden", {}, None
            )
        )
        with self.assertRaisesRegex(ValueError, "HTTP 403") as failure:
            gate.request_json(
                "https://api.github.com/test", "secret-token", opener, mock.Mock()
            )
        self.assertEqual(opener.call_count, 1)
        self.assertNotIn("secret-token", str(failure.exception))

    def test_retry_budget_is_bounded(self):
        opener = mock.Mock(
            side_effect=urllib.error.HTTPError(
                "https://api.github.com", 500, "Error", {}, None
            )
        )
        sleep = mock.Mock()
        with self.assertRaisesRegex(ValueError, "HTTP 500"):
            gate.request_json("https://api.github.com/test", None, opener, sleep)
        self.assertEqual(opener.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])


class VersionedReleaseGateTests(unittest.TestCase):
    modern_sha = "b" * 40

    def aggregates(self):
        names = ("rutorrent-image-tests-v1", "rutorrent-image-lint-v1")
        return {
            "total_count": 2,
            "check_runs": [
                {
                    "id": 300 + i,
                    "name": name,
                    "head_sha": self.modern_sha,
                    "status": "completed",
                    "conclusion": "success",
                    "app": {"id": 15368, "slug": "github-actions"},
                }
                for i, name in enumerate(names)
            ],
        }

    def test_new_source_requires_successful_versioned_aggregates(self):
        result = verify(self.aggregates(), self.modern_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("two release aggregates succeeded", result.stdout)

    def test_old_named_checks_cannot_bypass_new_source_aggregates(self):
        payload = fixture("checks-success.json")
        for run in payload["check_runs"]:
            run["head_sha"] = self.modern_sha
        result = verify(payload, self.modern_sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("rutorrent-image-tests-v1", result.stderr)
        self.assertIn("rutorrent-image-lint-v1", result.stderr)

    def test_failed_skipped_or_cancelled_aggregate_rejects_release(self):
        for conclusion in ("failure", "skipped", "cancelled"):
            with self.subTest(conclusion=conclusion):
                payload = self.aggregates()
                payload["check_runs"][0]["conclusion"] = conclusion
                result = verify(payload, self.modern_sha)
                self.assertEqual(result.returncode, 1)
                self.assertIn("rutorrent-image-tests-v1", result.stderr)

    def test_producer_aggregate_from_another_commit_is_rejected(self):
        payload = self.aggregates()
        payload["check_runs"][0]["head_sha"] = SHA
        result = verify(payload, self.modern_sha)
        self.assertEqual(result.returncode, 1)
        self.assertIn("rutorrent-image-tests-v1", result.stderr)


if __name__ == "__main__":
    unittest.main()
