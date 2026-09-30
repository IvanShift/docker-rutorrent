"""Exercise the Dockerfile's rTorrent source fetch against a local Git remote."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


DOCKERFILE = Path(__file__).resolve().parents[2] / "Dockerfile"


def git(*args, cwd):
    return subprocess.check_output(["git", *args], cwd=cwd, text=True).strip()


def source_fetch_command():
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    block = dockerfile.split("# ---- rTorrent sources (", 1)[1].split(
        "# ---- mktorrent sources (", 1
    )[0]
    command = block.split("\nRUN ", 1)[1].strip()
    return command


class RTorrentSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rtorrent-source-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote"
        self.remote.mkdir()
        git("init", "--quiet", cwd=self.remote)
        git("config", "user.name", "CI Fixture", cwd=self.remote)
        git("config", "user.email", "fixture@example.invalid", cwd=self.remote)
        (self.remote / "source.txt").write_text("release\n", encoding="utf-8")
        git("add", "source.txt", cwd=self.remote)
        git("commit", "--quiet", "-m", "release", cwd=self.remote)
        self.release_sha = git("rev-parse", "HEAD", cwd=self.remote)
        git("tag", "-a", "v0.16.24", "-m", "release", cwd=self.remote)
        (self.remote / "source.txt").write_text("native\n", encoding="utf-8")
        git("commit", "--quiet", "-am", "native", cwd=self.remote)
        self.native_sha = git("rev-parse", "HEAD", cwd=self.remote)

    def fetch(self, ref, expected):
        command = source_fetch_command()
        self.assertIn("${RTORRENT_REPO}", command)
        self.assertIn("${RTORRENT_REF}", command)
        work = self.root / "build"
        work.mkdir()
        environment = os.environ.copy()
        environment.update(
            RTORRENT_REPO=self.remote.as_uri(),
            RTORRENT_REF=ref,
            RTORRENT_VERSION=expected,
            RTORRENT_BRANCH="v0.16.24",
        )
        result = subprocess.run(
            ["/bin/sh", "-ec", command],
            cwd=work,
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        return result, work / "rtorrent"

    def test_default_release_tag_fetches_expected_source(self):
        result, source = self.fetch("v0.16.24", self.release_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "source.txt").read_text(), "release\n")
        self.assertFalse((source / ".git").exists())

    def test_exact_native_commit_ignores_release_label_for_source_selection(self):
        result, source = self.fetch(self.native_sha, self.native_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "source.txt").read_text(), "native\n")
        self.assertFalse((source / ".git").exists())

    def test_ref_must_resolve_to_expected_commit(self):
        result, source = self.fetch(self.native_sha, self.release_sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((source / "source.txt").exists())


if __name__ == "__main__":
    unittest.main()
