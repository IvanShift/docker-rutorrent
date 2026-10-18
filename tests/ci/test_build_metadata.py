"""Exercise Docker RUN inputs locally without a daemon or registry access."""

import hashlib
import io
import os
import re
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def run_instruction(stage_name):
    stages = re.split(r"(?im)^FROM\s", (ROOT / "Dockerfile").read_text())[1:]
    stage = next(stage for stage in stages if stage.splitlines()[0].endswith(f" AS {stage_name}"))
    match = re.search(r"(?ms)^RUN (.*?)(?=\n\n|\Z)", stage)
    command = match[1].replace("\\\n", " ")
    return re.sub(r"^--mount=\S+\s+", "", command)


class BuildMetadataTests(unittest.TestCase):
    def test_mirror_credentials_and_signed_query_do_not_enter_notice(self):
        with tempfile.TemporaryDirectory(prefix="docker-notice-") as temporary:
            temporary = Path(temporary)
            database, fixture_bin = temporary / "database", temporary / "bin"
            fixture_bin.mkdir()
            fixture = temporary / "asset"
            fixture.write_bytes(b"synthetic database")
            curl = fixture_bin / "curl"
            curl.write_text('#!/bin/sh\nwhile [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then cp "$FAKE_DATABASE" "$2"; exit 0; fi; shift; done\nexit 1\n')
            curl.chmod(0o755)
            command = run_instruction("geoip2-db-source").replace("/database", str(database))
            env = {
                **os.environ, "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                "FAKE_DATABASE": str(fixture), "GEOIP2_DB_VERSION": "2026.10.04",
                "GEOIP2_DB_SHA256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
                "GEOIP2_DB_URL": "https://mirror-user:secret-password@example.invalid/db?signature=secret-query",
            }
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", command], env=env, capture_output=True, text=True, timeout=10, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            notice = (database / "NOTICE").read_text()
            self.assertIn("Release: 2026.10.04", notice)
            for secret in ("mirror-user", "secret-password", "secret-query", "example.invalid"):
                self.assertNotIn(secret, notice)

    def test_plugin_source_run_verifies_the_supplied_ref_against_its_pin(self):
        for name, stage in (("GEOIP2", "geoip2-source"), ("RATIOCOLOR", "ratiocolor-source")):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory(prefix="docker-plugin-") as temporary:
                temporary = Path(temporary)
                remote = temporary / "remote"
                remote.mkdir()
                env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
                env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
                def git(*args):
                    return subprocess.check_output(["git", *args], cwd=remote, env=env, text=True, timeout=10).strip()
                git("init", "--quiet")
                git("config", "user.name", "Build Fixture")
                git("config", "user.email", "fixture@example.invalid")
                for path in ("plugin.info", "init.js", "init.php", "geoip2.phar"):
                    (remote / path).write_text("reviewed\n")
                git("add", ".")
                git("commit", "--quiet", "-m", "reviewed")
                reviewed = git("rev-parse", "HEAD")
                (remote / "init.js").write_text("moved\n")
                git("commit", "--quiet", "-am", "moved")
                current = git("rev-parse", "HEAD")
                git("branch", "moving-ref")
                for expected, success in ((reviewed, False), (current, True)):
                    output = temporary / expected
                    command = run_instruction(stage).replace("/fetch-source", str(ROOT / "scripts/fetch-build-source.sh"))
                    command = re.sub(r"/plugin(?=/|\s|$)", str(output), command)
                    build_env = {**env, f"{name}_REPO": remote.as_uri(), f"{name}_REF": "moving-ref", f"{name}_COMMIT_SHA": expected}
                    result = subprocess.run(["bash", "-eo", "pipefail", "-c", command], env=build_env, capture_output=True, text=True, timeout=10, check=False)
                    self.assertEqual(result.returncode == 0, success, result.stderr)
                    if success:
                        self.assertEqual((output / "init.js").read_text(), "moved\n")
                    else:
                        self.assertIn("Source identity mismatch", result.stderr)

    def test_filebot_payload_downloads_and_creates_filebot_only_when_requested(self):
        for requested in ("false", "true"):
            with self.subTest(filebot=requested), tempfile.TemporaryDirectory(prefix="docker-filebot-") as temporary:
                temporary = Path(temporary)
                fixture_bin, archive = temporary / "bin", temporary / "portable.tar.xz"
                fixture_bin.mkdir()
                with tarfile.open(archive, "w:xz") as portable:
                    for name, data in (("filebot.sh", b"java -Dapplication.deployment=tar\n"),
                                       ("lib/Linux-x86_64/libjnidispatch.so", b"jna"),
                                       ("lib/Linux-x86_64/libunused.so", b"unused")):
                        member = tarfile.TarInfo(name)
                        member.size = len(data)
                        portable.addfile(member, io.BytesIO(data))
                trace = temporary / "curl-calls"
                curl = fixture_bin / "curl"
                curl.write_text('#!/bin/sh\necho "$@" >> "$CURL_TRACE"\n'
                                'while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then cp "$FAKE_ARCHIVE" "$2"; exit 0; fi; shift; done\nexit 1\n')
                curl.chmod(0o755)
                payload = temporary / "payload"
                command = run_instruction("filebot-payload").replace("/payload", str(payload))
                command = re.sub(r"(?<![\w.])/filebot(?=/|\s|$)", str(temporary / "filebot"), command)
                env = {**os.environ, "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
                       "CURL_TRACE": str(trace), "FAKE_ARCHIVE": str(archive), "FILEBOT": requested,
                       "FILEBOT_VER": "5.3.0", "FILEBOT_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest()}
                result = subprocess.run(["bash", "-eo", "pipefail", "-c", command], env=env, capture_output=True, text=True, timeout=10, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertFalse((temporary / "filebot").exists())
                if requested == "false":
                    self.assertFalse(trace.exists())
                    self.assertEqual(list(payload.iterdir()), [])
                    continue
                self.assertEqual(len(trace.read_text().splitlines()), 1)
                self.assertEqual((payload / "filebot/filebot.sh").read_text(), "java -Dapplication.deployment=docker\n")
                self.assertEqual(sorted(path.name for path in (payload / "filebot/lib").rglob("*") if path.is_file()),
                                 ["libjnidispatch.so"])
                self.assertFalse((payload / "filebot/filebot.tar.xz").exists())

    def test_canonical_rename_smoke_is_selected_by_packaged_capability(self):
        source = (ROOT / "tests/smoke/native-image-check.sh").read_text()
        start = source.index("    helper=/usr/local/bin/rutorrent-erasedata-rename-noreplace")
        end = source.index("    expect_status()", start)
        for supported in (False, True):
            with self.subTest(supported=supported), tempfile.TemporaryDirectory(prefix="docker-rename-cap-") as temporary:
                temporary = Path(temporary)
                canonical = temporary / "rename.sh"
                marker = temporary / "marker"
                canonical.write_text('#!/bin/bash\nset -euo pipefail\n[[ "$1" = /usr/local/bin/rutorrent-erasedata-rename-noreplace ]]\nprintf "called\\n" > "$MARKER"\n')
                capabilities = temporary / "capabilities"
                if supported:
                    capabilities.write_text("rename-probe=1\n")
                command = source[start:end].replace("/rutorrent/app/tests/plugins/erasedata/rename-noreplace-test.sh", str(canonical)).replace("/usr/local/share/rutorrent-native-capabilities", str(capabilities))
                command = 'fail() { echo "$*" >&2; exit 1; };\n' + command
                result = subprocess.run(["sh", "-eu", "-c", command], env={**os.environ, "MARKER": str(marker)}, capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(marker.exists(), supported)
                if not supported:
                    self.assertIn("SKIP pre-manifest source contract", result.stdout)


if __name__ == "__main__":
    unittest.main()
