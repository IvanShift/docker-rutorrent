"""The captured Hub listing supports only an offline, age-qualified legacy plan."""
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
SCRIPT = ROOT / "scripts/candidate_cleanup_plan.py"
FIXTURE = Path(__file__).parent / "fixtures/registry/hub-tags-live-2026-10-06.json"


class CleanupPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location("cleanup_plan", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.plan = staticmethod(module.cleanup_plan)
        cls.now = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)

    def snapshot(self, rows):
        return {"count": len(rows), "next": None, "results": rows}

    def test_complete_live_hub_capture_is_accepted_with_its_actual_timestamp_shape(self):
        snapshot = json.loads(FIXTURE.read_text())
        result = self.plan(snapshot, now=self.now)
        self.assertEqual(result["mode"], "dry-run-only")
        self.assertEqual(result["retained_count"] + len(result["candidate_tags"]), snapshot["count"])
        self.assertTrue(set(result["candidate_tags"]) <= {row["name"] for row in snapshot["results"]})

    def test_only_exact_old_run_candidates_are_selected(self):
        names = ["candidate-123-1-default-amd64", "candidate-124-1-filebot-arm64",
                 "candidate-default-amd64", "candidate-filebot-arm64", "latest", "filebot",
                 "rtorrent-0.16.25-2026.10.01-run123-attempt1", "candidate-123-1-default-amd64-extra"]
        rows = [{"name": name, "last_updated": "2026-09-01T01:02:03.123456Z"} for name in names]
        rows[1]["last_updated"] = "2026-10-05T01:02:03Z"
        result = self.plan(self.snapshot(rows), now=self.now)
        self.assertEqual(result["candidate_tags"], [names[0]])
        self.assertEqual(result["retained_count"], len(names) - 1)

    def test_unknown_age_incomplete_listing_and_duplicate_names_are_not_cleanup_authority(self):
        name = "candidate-123-1-default-amd64"
        for timestamp in (None, "nonsense", "2026-09-01T00:00:00"):
            result = self.plan(self.snapshot([{"name": name, "last_updated": timestamp}]), now=self.now)
            self.assertEqual(result["candidate_tags"], [])
            self.assertEqual(result["skipped"], [{"name": name, "reason": "unproven-age"}])
        for snapshot in ({"count": 2, "next": "page2", "results": []},
                         {"count": True, "next": None, "results": [{"name": name}]},
                         self.snapshot([{"name": name}, {"name": name}])):
            with self.subTest(snapshot=snapshot), self.assertRaises(ValueError):
                self.plan(snapshot, now=self.now)

    def test_cli_has_no_registry_write_or_apply_operation(self):
        result = subprocess.run([sys.executable, str(SCRIPT), str(FIXTURE), "--apply"],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unrecognized arguments: --apply", result.stderr)
        before = FIXTURE.read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPT), str(FIXTURE)],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["mode"], "dry-run-only")
        self.assertEqual(FIXTURE.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
