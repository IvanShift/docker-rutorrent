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

from dockerfile_parser import run_scripts, stages

ROOT = Path(__file__).resolve().parents[2]
FFMPEG_PACKAGES = ("ffmpeg", "ffmpeg-libavcodec", "ffmpeg-libavdevice", "ffmpeg-libavfilter",
                   "ffmpeg-libavformat", "ffmpeg-libavutil", "ffmpeg-libswresample", "ffmpeg-libswscale")


def run_instructions(stage_name):
    """The shell text of each RUN of STAGE_NAME, as the build shell receives it."""
    return run_scripts(stages((ROOT / "Dockerfile").read_text())[stage_name])


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
            command, = run_instructions("geoip2-db-source")
            command = command.replace("/database", str(database))
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
                    command, = run_instructions(stage)
                    command = command.replace("/fetch-source", str(ROOT / "scripts/fetch-build-source.sh"))
                    command = re.sub(r"/plugin(?=/|\s|$)", str(output), command)
                    build_env = {**env, f"{name}_REPO": remote.as_uri(), f"{name}_REF": "moving-ref", f"{name}_COMMIT_SHA": expected}
                    result = subprocess.run(["bash", "-eo", "pipefail", "-c", command], env=build_env, capture_output=True, text=True, timeout=10, check=False)
                    self.assertEqual(result.returncode == 0, success, result.stderr)
                    if success:
                        self.assertEqual((output / "init.js").read_text(), "moved\n")
                    else:
                        self.assertIn("Source identity mismatch", result.stderr)

    @staticmethod
    def run_filebot_payload(temporary, requested, checksum=None):
        """Run the filebot-payload RUN in TEMPORARY; a fake curl serves a small portable archive.

        CHECKSUM defaults to the archive's own SHA256. The stage output is
        TEMPORARY/payload and every curl call is a line of TEMPORARY/curl-calls.
        """
        fixture_bin, archive = temporary / "bin", temporary / "portable.tar.xz"
        fixture_bin.mkdir()
        with tarfile.open(archive, "w:xz") as portable:
            for name, data in (("filebot.sh", b"java -Dapplication.deployment=tar\n"),
                               ("lib/Linux-x86_64/libjnidispatch.so", b"jna"),
                               ("lib/Linux-x86_64/libunused.so", b"unused")):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                portable.addfile(member, io.BytesIO(data))
        curl = fixture_bin / "curl"
        curl.write_text('#!/bin/sh\necho "$@" >> "$CURL_TRACE"\n'
                        'while [ "$#" -gt 0 ]; do if [ "$1" = -o ]; then cp "$FAKE_ARCHIVE" "$2"; exit 0; fi; shift; done\nexit 1\n')
        curl.chmod(0o755)
        command, = run_instructions("filebot-payload")
        command = command.replace("/payload", str(temporary / "payload"))
        command = re.sub(r"(?<![\w.])/filebot(?=/|\s|$)", str(temporary / "filebot"), command)
        env = {**os.environ, "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"],
               "CURL_TRACE": str(temporary / "curl-calls"), "FAKE_ARCHIVE": str(archive), "FILEBOT": requested,
               "FILEBOT_VER": "5.3.0",
               "FILEBOT_SHA256": hashlib.sha256(archive.read_bytes()).hexdigest() if checksum is None else checksum}
        return subprocess.run(["bash", "-eo", "pipefail", "-c", command], env=env, capture_output=True, text=True, timeout=10, check=False)

    def test_filebot_payload_downloads_and_creates_filebot_only_when_requested(self):
        for requested in ("false", "true"):
            with self.subTest(filebot=requested), tempfile.TemporaryDirectory(prefix="docker-filebot-") as temporary:
                temporary = Path(temporary)
                result = self.run_filebot_payload(temporary, requested)
                payload, trace = temporary / "payload", temporary / "curl-calls"
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

    def test_filebot_payload_with_a_wrong_checksum_fails_and_exports_nothing(self):
        for checksum in ("0" * 64, ""):
            with self.subTest(checksum=checksum), tempfile.TemporaryDirectory(prefix="docker-filebot-") as temporary:
                temporary = Path(temporary)
                result = self.run_filebot_payload(temporary, "true", checksum)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len((temporary / "curl-calls").read_text().splitlines()), 1)
                # The stage stops before unpacking: nothing reaches /payload.
                self.assertEqual(list((temporary / "payload").iterdir()), [])
                self.assertFalse((temporary / "filebot/filebot.sh").exists())

    def test_application_export_keeps_operator_tools_and_drops_development_scripts(self):
        tests = [line for line in (ROOT / "scripts/rutorrent-image-tests.list").read_text().splitlines()
                 if line and not line.startswith("#")]
        kept = [".source-revision", "LICENSE.md", "conf/config.php", "php/settings.php",
                "plugins/erasedata/LICENSE.md", "tools/.htaccess", "tools/native-helpers.list",
                "tools/retrackers-clear-markers.php", "tools/update-asset-version.php", *tests]
        removed = [".github/workflows/ci.yml", "README.md", "plugins/_cloudflare/init.php", "plugins/geoip/init.php",
                   "plugins/erasedata/README", "plugins/erasedata/.github/notes.yml", "tests/php/CacheTest.php",
                   "tools/README.md", "tools/matrix.sh", "tools/rt-lab.sh"]
        with tempfile.TemporaryDirectory(prefix="docker-app-") as temporary:
            export = Path(temporary) / "export"
            for relative in kept + removed:
                path = export / "rutorrent/app" / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("version\t1\n" if relative == "tools/native-helpers.list" else relative + "\n")
            cleanup, _normalize = run_instructions("rutorrent-app")
            command = re.sub(r"(?<![\w.])/export(?=/)", str(export), cleanup)
            command = command.replace("/prune-rutorrent-tests", str(ROOT / "scripts/prune-rutorrent-tests.sh"))
            command = command.replace("/rutorrent-image-tests.list", str(ROOT / "scripts/rutorrent-image-tests.list"))
            result = subprocess.run(["bash", "-eo", "pipefail", "-c", command], cwd=temporary, capture_output=True, text=True, timeout=30, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            exported = sorted(path.relative_to(export).as_posix() for path in export.rglob("*") if path.is_file())
            self.assertEqual(exported, sorted(["rutorrent/conf-dist/config.php", "usr/share/build-info/rutorrent-revision",
                                               "usr/share/licenses/rutorrent/LICENSE.md",
                                               *("rutorrent/app/" + relative for relative in kept)]))

    def default_smoke_ffmpeg_check(self, packages, version="8.1.3"):
        """Run the FFmpeg package check of tests/smoke/default.sh on an apk info -v listing."""
        source = (ROOT / "tests/smoke/default.sh").read_text()
        start = source.index("# The local FFmpeg backport installs exactly")
        end = source.index('docker exec --user 991:991 "$container" sh -eu -c \'command -v mktorrent', start)
        with tempfile.TemporaryDirectory(prefix="docker-ffmpeg-") as temporary:
            listing = Path(temporary) / "listing"
            if packages is not None:
                listing.write_text("".join(package + "\n" for package in packages))
            stubs = ('docker() { [ "$*" = "exec c apk info -v" ] && cat "$LISTING"; }\n'
                     'fail() { printf "not ok - %s\\n" "$1" >&2; exit 1; }\n')
            return subprocess.run(["sh", "-eu", "-c", stubs + source[start:end]], capture_output=True, text=True, timeout=10,
                                  check=False, env={"PATH": os.environ["PATH"], "LISTING": str(listing), "container": "c",
                                                    "workspace": temporary, "ffmpeg_package_version": version})

    def test_default_smoke_requires_exactly_the_eight_ffmpeg_packages_at_the_literal_version(self):
        exact = [f"{name}-8.1.3-r0" for name in FFMPEG_PACKAGES]
        accepted = {"exact": exact, "other order and packages": ["curl-8.23.0-r0", *reversed(exact), "zlib-1.3.2-r0"],
                    "later release": [package.replace("-r0", "-r12") for package in exact]}
        refused = {
            "one name replaced": exact[:2] + ["ffmpeg-doc-8.1.3-r0"] + exact[3:],
            "regex-like version": exact[:1] + ["ffmpeg-libavcodec-8x1x3-r0"] + exact[2:],
            "repository library": exact[:4] + ["ffmpeg-libavformat-8.1.2-r0"] + exact[5:],
            "extra package": exact + ["ffmpeg-libpostproc-8.1.3-r0"],
            "missing package": exact[1:],
            "release suffix": exact[:-1] + ["ffmpeg-libswscale-8.1.3-r0x"],
            "no release": exact[:-1] + ["ffmpeg-libswscale-8.1.3"],
            "apk unavailable": None,
        }
        for case, packages in accepted.items():
            with self.subTest(case=case):
                result = self.default_smoke_ffmpeg_check(packages)
                self.assertEqual(result.returncode, 0, result.stderr)
        for case, packages in refused.items():
            with self.subTest(case=case):
                result = self.default_smoke_ffmpeg_check(packages)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("not ok - ", result.stderr)

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
