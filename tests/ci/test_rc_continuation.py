"""Continued lines in the literal rc migration.

rTorrent's parse_command_file (src/rpc/parse_commands.cc) drops an odd run of
backslashes that ends a physical line and appends the next line, so an alias on
a continuation line belongs to the command above it, and a continued comment
hides the next line. The migrator reads the same logical lines and keeps the
operator's line breaks. The fixture pairs also suit tests/update-config.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
FIXTURES = ROOT / "tests/update-config/fixtures"
GUARD = b'catch = {"system.file_name.allow_legacy_utf8.set=1"}'
HOOK = b'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="'
# update-config passes the PHP CLI that it found in the image.
ENVIRONMENT = {**os.environ, "UPDATE_PHP_PATH": "/usr/bin/php85"}


@requires_host_php("literal rc migration")
class ContinuationMigrationTests(unittest.TestCase):
    def run_migrator(self, path, mode, check=True):
        return subprocess.run(["php", "-n", str(MIGRATOR), str(path), "test.rc", mode], check=check,
                              capture_output=True, text=True, env=ENVIRONMENT)

    def migrated(self, content, mode="native"):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_bytes(content)
            self.run_migrator(path, mode)
            first = path.read_bytes()
            self.run_migrator(path, mode)
            self.assertEqual(path.read_bytes(), first, "second migration must be byte-identical")
            return first

    def test_fixture_pairs(self):
        for name, mode in (("continuation", "native"), ("legacy-continuation", "legacy")):
            with self.subTest(name=name):
                expected = (FIXTURES / f"{name}.expected.rc").read_bytes()
                self.assertEqual(self.migrated((FIXTURES / f"{name}.input.rc").read_bytes(), mode), expected)

    def test_legacy_continuation_returns_to_native_unchanged(self):
        legacy = (FIXTURES / "legacy-continuation.expected.rc").read_bytes()
        self.assertEqual(self.migrated(legacy), GUARD + b"\n" + (FIXTURES / "legacy-continuation.input.rc").read_bytes())

    def test_crlf_continuation_keeps_its_break(self):
        # The CR is converted first, so the backslash ends the physical line.
        migrated = self.migrated(b'schedule2 = example,0,60,\\\r\n  "execute2=echo,hello"\r\n')
        self.assertEqual(migrated, GUARD + b'\nschedule = example,0,60,\\\n'
                         b'  "execute.nothrow=echo,hello"\n' + HOOK + b"\n")

    def test_a_line_continued_at_the_end_is_kept_last(self):
        # rTorrent never runs it: no hook may join it, and its save does not count.
        for ending in (b"\n", b""):
            with self.subTest(ending=ending):
                tail = b'method.set_key = event.system.shutdown,save,"session.save=" \\' + ending
                migrated = self.migrated(b"schedule2 = example,0,60,echo\n" + tail)
                self.assertEqual(migrated, GUARD + b"\nschedule = example,0,60,echo\n" + HOOK + b"\n" + tail)

    def test_lines_rtorrent_cannot_read_are_refused(self):
        # Checked with the 0.16.25 daemon: 4094 bytes load and 4095 do not. Only
        # the current physical line's backslash run counts; earlier runs are dropped.
        cases = ((b"#" * 4094, True), (b"#" * 4095, False),
                 (b"#" * 2000 + b"\\\n" + b"y" * 2094, True), (b"#" * 2000 + b"\\\n" + b"y" * 2095, False),
                 (b"#" * 4094 + b"\\\n", False))
        for line, readable in cases:
            with self.subTest(length=len(line), readable=readable):
                with tempfile.TemporaryDirectory() as scratch:
                    path = Path(scratch) / "config.rc"
                    path.write_bytes(line + b"\n")
                    result = self.run_migrator(path, "native", check=False)
                self.assertEqual(result.returncode, 0 if readable else 1, result.stderr)
                if not readable:
                    self.assertEqual(result.stderr, "test.rc:1: Line is 4095 bytes or longer, counting continued "
                                     "lines; rTorrent cannot read it; shorten it while stopped\n")
        # A rename must not push a readable line over the limit either.
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            line = b"execute2 = echo," + b"x" * (4094 - len(b"execute2 = echo,"))
            path.write_bytes(line + b"\n")
            result = self.run_migrator(path, "native", check=False)
            self.assertEqual(path.read_bytes(), line + b"\n")
        self.assertEqual(result.returncode, 1)
        self.assertIn("test.rc:2: Migrated line would be 4095 bytes or longer", result.stderr)

    def test_refusals_name_the_first_line_of_the_command(self):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_bytes(b"# note\n\nnetwork.proxy_address.set = \\\n  (cat,host)\n")
            result = self.run_migrator(path, "native", check=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "test.rc:3: Proxy migration requires a literal host:port or proxy URL\n")


if __name__ == "__main__":
    unittest.main()
