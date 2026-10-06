"""Build-mode validation uses the same branch table as the actual configure path."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class NativeBuildModeTests(unittest.TestCase):
    def validate(self, branch, strict="true"):
        return subprocess.run(
            ["sh", str(ROOT / "scripts/build-native-components.sh"), "validate"],
            env={**os.environ, "RTORRENT_BRANCH": branch, "STRICT_WERROR": strict},
            capture_output=True, text=True, timeout=5, check=False,
        )

    def test_native_patch_releases_and_legacy_choose_their_standards(self):
        for branch in ("v0.16.24", "v0.16.25", "v0.16.100"):
            with self.subTest(branch=branch):
                result = self.validate(branch)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "c++20 legacy=false\n")
        result = self.validate("v0.9.8")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "c++17 legacy=true\n")

    def test_unreviewed_families_malformed_labels_and_options_are_refused(self):
        for branch in ("v0.16.", "v0.16.25evil", "v0.17.1", "v0.9.9"):
            with self.subTest(branch=branch):
                self.assertEqual(self.validate(branch).returncode, 64)
        self.assertEqual(self.validate("v0.16.25", "yes").returncode, 64)

    def test_configure_retains_component_flags_and_backend_contract(self):
        for branch, component, strict in (("v0.16.25", "rtorrent", "true"), ("v0.16.25", "libtorrent", "false"), ("v0.9.8", "rtorrent", "true"), ("v0.9.8", "libtorrent", "true")):
            with self.subTest(branch=branch, component=component, strict=strict), tempfile.TemporaryDirectory(prefix="docker-build-mode-") as temporary:
                temporary = Path(temporary)
                fixture_bin = temporary / "bin"
                fixture_bin.mkdir()
                for name in ("autoreconf", "make"):
                    path = fixture_bin / name
                    path.write_text("#!/bin/sh\nexit 0\n")
                    path.chmod(0o755)
                configure = temporary / "configure"
                configure.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENT_LOG"\n')
                configure.chmod(0o755)
                log = temporary / "arguments"
                result = subprocess.run(["sh", str(ROOT / "scripts/build-native-components.sh"), component], cwd=temporary, env={**os.environ, "PATH": str(fixture_bin) + os.pathsep + os.environ["PATH"], "RTORRENT_BRANCH": branch, "STRICT_WERROR": strict, "DIST_PATH": str(temporary / "dist"), "ARGUMENT_LOG": str(log)}, capture_output=True, text=True, timeout=5, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                arguments = log.read_text().splitlines()
                flags = next(argument for argument in arguments if argument.startswith("CXXFLAGS="))
                self.assertIn("-std=c++17" if branch == "v0.9.8" else "-std=c++20", flags)
                self.assertNotIn(" -w", flags)
                self.assertEqual("-Werror=lto-type-mismatch" in flags, strict == "true")
                if component == "rtorrent":
                    self.assertIn("--with-xmlrpc-c" if branch == "v0.9.8" else "--with-xmlrpc-tinyxml2", arguments)
                else:
                    self.assertEqual("--enable-aligned" in arguments, branch == "v0.9.8")

    def test_docker_uses_the_shared_validation_table(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("sh /build-native validate", dockerfile)
        self.assertNotIn("case \"${RTORRENT_BRANCH}\" in v0.16.24|v0.9.8", dockerfile)


if __name__ == "__main__":
    unittest.main()
