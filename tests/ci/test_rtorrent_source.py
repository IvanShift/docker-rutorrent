"""Exercise the shared Docker source fetch against isolated Git fixtures."""

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = REPO_ROOT / "Dockerfile"
SCRIPT = REPO_ROOT / "scripts/fetch-build-source.sh"


def fixture_env():
    # Git hooks export GIT_DIR/GIT_INDEX_FILE; fixture git must never touch them.
    env = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return env


def git(*args, cwd):
    return subprocess.check_output(
        ["git", *args], cwd=cwd, env=fixture_env(), text=True, timeout=30
    ).strip()


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
        work = self.root / "build"
        env = fixture_env()
        # Negative control: changing the release label must not select other sources.
        env["RTORRENT_BRANCH"] = "v0.16.24"
        result = subprocess.run(
            ["/bin/sh", str(SCRIPT), self.remote.as_uri(), ref, expected, str(work)],
            cwd=self.root,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        return result, work

    def test_release_tag_fetches_expected_source(self):
        result, source = self.fetch("v0.16.24", self.release_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "source.txt").read_text(), "release\n")
        self.assertEqual(
            (source / ".source-revision").read_text().strip(), self.release_sha
        )
        self.assertFalse((source / ".git").exists())

    def test_exact_native_commit_ignores_release_label_for_source_selection(self):
        result, source = self.fetch(self.native_sha, self.native_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "source.txt").read_text(), "native\n")
        self.assertFalse((source / ".git").exists())

    def test_empty_ref_fetches_expected_sha(self):
        result, source = self.fetch("", self.release_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((source / "source.txt").read_text(), "release\n")

    def test_ref_must_resolve_to_expected_commit(self):
        result, source = self.fetch(self.native_sha, self.release_sha)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(f"resolved {self.native_sha}", result.stdout)
        self.assertIn(
            f"Source identity mismatch: expected {self.release_sha}, "
            f"got {self.native_sha}",
            result.stderr,
        )
        self.assertFalse((source / "source.txt").exists())
        self.assertFalse((source / ".source-revision").exists())

    def test_moving_ref_cannot_change_the_pinned_identity(self):
        result, source = self.fetch("HEAD", self.release_sha)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("Source identity mismatch", result.stderr)
        self.assertFalse((source / "source.txt").exists())

    def test_git_environment_and_signing_settings_cannot_escape_the_fixture(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        git("init", "--quiet", cwd=foreign)
        before = (foreign / ".git/config").read_bytes()
        config = self.root / "hostile.gitconfig"
        config.write_text(
            '[commit]\n gpgSign = true\n[tag]\n gpgSign = true\n'
            '[protocol "file"]\n allow = never\n'
        )
        with mock.patch.dict(
            os.environ,
            {
                "GIT_DIR": str(foreign / ".git"),
                "GIT_INDEX_FILE": str(foreign / ".git/index"),
                "GIT_CONFIG_GLOBAL": str(config),
            },
        ):
            self.assertEqual(fixture_env()["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(fixture_env()["GIT_CONFIG_SYSTEM"], os.devnull)
            git("config", "user.name", "Still Isolated", cwd=self.remote)
            result, source = self.fetch(self.native_sha, self.native_sha)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((foreign / ".git/config").read_bytes(), before)
        self.assertFalse((foreign / ".git/index").exists())
        self.assertEqual((source / "source.txt").read_text(), "native\n")

    def test_docker_source_stage_declares_and_passes_all_identity_inputs(self):
        text = DOCKERFILE.read_text(encoding="utf-8")
        stages = re.split(r"(?im)^FROM\s", text)[1:]
        src = next(
            (
                stage
                for stage in stages
                if re.search(r"(?im)\sAS\s+src\s*$", stage.splitlines()[0])
            ),
            None,
        )
        self.assertIsNotNone(
            src, "Dockerfile must retain a source stage using the shared fetch script"
        )
        for name in ("RTORRENT_REPO", "RTORRENT_REF", "RTORRENT_VERSION"):
            self.assertRegex(src, rf"(?m)^ARG {name}(?:\s|$)")
        self.assertIn("source=scripts/fetch-build-source.sh,target=/fetch-source", src)
        self.assertRegex(
            src,
            r'sh /fetch-source "\$\{RTORRENT_REPO\}" "\$\{RTORRENT_REF\}" '
            r'"\$\{RTORRENT_VERSION\}" /src/rtorrent',
        )


if __name__ == "__main__":
    unittest.main()
