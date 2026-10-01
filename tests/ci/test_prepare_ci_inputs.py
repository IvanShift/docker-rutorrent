"""Verify pin parsing independently of shell evaluation and remote branch heads."""

import datetime
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from prepare_ci_inputs import (  # noqa: E402 - import repository helper after path setup
    dockerfile_defaults,
    prepare,
)

SHA = "a" * 40
NOW = datetime.datetime(2026, 10, 1, 23, 59, 59, tzinfo=datetime.timezone.utc)
DEFAULTS = f"""ARG RTORRENT_BRANCH=v0.16.24
ARG RTORRENT_VERSION={SHA}
ARG RUTORRENT_REPO=https://github.com/example/ui.git
ARG RUTORRENT_REF={SHA}
FROM alpine AS src
ARG RUTORRENT_REF
"""


class PrepareTests(unittest.TestCase):
    def test_pinned_sources_and_one_timestamp(self):
        values = prepare(DEFAULTS, NOW)
        self.assertEqual(values["rutorrent_repo_slug"], "example/ui")
        self.assertEqual(values["rutorrent_commit"], SHA)
        self.assertEqual(values["rtorrent_label"], "0.16.24")
        self.assertEqual(values["build_date"], "2026-10-01T23:59:59Z")
        self.assertEqual(values["tag_date"], "2026.10.01")

    def test_equals_quoted_values_and_argument_expansion_are_retained(self):
        values = dockerfile_defaults(
            '  arg REPO="https://example.invalid/repo.git?token=abc"\nARG SHA="'
            + SHA
            + '"\nARG REF=${SHA}\nARG BRANCH=refs/heads/native=2026\nFROM alpine\n'
        )
        self.assertEqual(values["REPO"], "https://example.invalid/repo.git?token=abc")
        self.assertEqual(values["REF"], SHA)
        self.assertEqual(values["BRANCH"], "refs/heads/native=2026")

    def test_git_suffix_is_not_part_of_api_repository(self):
        for url in (
            "https://github.com/example/ui",
            "https://github.com/example/ui.git",
        ):
            self.assertEqual(
                prepare(
                    DEFAULTS.replace("https://github.com/example/ui.git", url), NOW
                )["rutorrent_repo_slug"],
                "example/ui",
            )

    def test_unpinned_or_contradictory_source_is_rejected(self):
        for text in (
            DEFAULTS.replace(f"ARG RUTORRENT_REF={SHA}", "ARG RUTORRENT_REF=master"),
            DEFAULTS.replace(
                "FROM alpine", f"ARG RUTORRENT_COMMIT_SHA={'b' * 40}\nFROM alpine"
            ),
            DEFAULTS.replace(
                f"ARG RTORRENT_VERSION={SHA}", "ARG RTORRENT_VERSION=HEAD"
            ),
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                prepare(text, NOW)

    def test_mutable_ref_requires_separate_exact_commit(self):
        text = DEFAULTS.replace(
            f"ARG RUTORRENT_REF={SHA}",
            f"ARG RUTORRENT_REF=master\nARG RUTORRENT_COMMIT_SHA={SHA}",
        )
        self.assertEqual(prepare(text, NOW)["rutorrent_commit"], SHA)

    def test_duplicate_and_undefined_argument_defaults_are_rejected(self):
        for text in ("ARG A=x\nARG A=y", "ARG A=${MISSING}"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                dockerfile_defaults(text)

    def test_current_dockerfile_provides_immutable_inputs(self):
        values = prepare((REPO_ROOT / "Dockerfile").read_text(), NOW)
        self.assertRegex(values["rutorrent_commit"], r"^[0-9a-f]{40}$")
        self.assertNotIn(
            "rtorrent_repo", values
        )  # Dockerfile uses its own unchanged source defaults.


if __name__ == "__main__":
    unittest.main()
