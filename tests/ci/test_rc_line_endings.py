"""CRLF line endings in the literal rc migration.

rTorrent's parse_command_file splits only at LF and rejects the CR left before
it ("Junk at end of input"), and startup.php refuses such session and download
lines for the same reason. The migrator therefore converts CRLF to LF before
startup reads the paths. Other CR bytes stay for the existing refusals.
"""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php
import test_startup_repair as startup

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
FIXTURES = ROOT / "tests/update-config/fixtures"
GUARD = b'catch = {"system.file_name.allow_legacy_utf8.set=1"}'
HOOK = b'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="'
CONVERTED = "Converted test.rc line endings from CRLF to LF: rTorrent rejects a carriage return at the end of a line\n"
ENVIRONMENT = {**os.environ, "UPDATE_PHP_PATH": "/usr/bin/php85"}


@requires_host_php("literal rc migration", extensions=("tokenizer", "posix"))
class LineEndingMigrationTests(unittest.TestCase):
    def migrate(self, path, mode="native"):
        return subprocess.run(["php", "-n", str(MIGRATOR), str(path), "test.rc", mode],
                              check=True, capture_output=True, text=True, env=ENVIRONMENT).stdout

    def migrated(self, content, mode="native"):
        """The migrated bytes and the first run's messages; a second run changes nothing."""
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_bytes(content)
            output = self.migrate(path, mode)
            first = path.read_bytes()
            self.assertEqual(self.migrate(path, mode), "")
            self.assertEqual(path.read_bytes(), first, "second migration must be byte-identical")
            return first, output

    def test_fixture_pairs(self):
        for name in ("crlf-indent", "crlf-paths"):
            with self.subTest(name=name):
                migrated, output = self.migrated((FIXTURES / f"{name}.input.rc").read_bytes())
                self.assertEqual(migrated, (FIXTURES / f"{name}.expected.rc").read_bytes())
                self.assertEqual(output.count(CONVERTED), 1, output)

    def test_startup_admits_the_paths_only_after_migration(self):
        # startup migrates the rc before it reads these paths.
        with tempfile.TemporaryDirectory() as scratch:
            rc = Path(scratch) / "rtorrent.rc"
            rc.write_bytes((FIXTURES / "crlf-paths.input.rc").read_bytes())
            probe = Path(scratch) / "probe.php"
            probe.write_text(startup.PROBE + "echo rcPath($argv[3], 'session.path.set'), \"\\n\", "
                             "rcPath($argv[3], 'directory.default.set'), \"\\n\";\n")
            command = ["php", str(probe), str(startup.HELPER), str(Path(scratch) / "functions.php"), str(rc)]
            refused = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(refused.returncode, 78, refused.stderr)
            self.assertIn("CRLF line ending", refused.stderr)
            self.migrate(rc)
            admitted = subprocess.run(command, text=True, capture_output=True)
        self.assertEqual((admitted.returncode, admitted.stdout), (0, "/data/.session\n/data/downloads\n"),
                         admitted.stderr)

    def test_only_a_cr_that_ends_a_line_is_converted(self):
        for mode in ("native", "legacy"):
            with self.subTest(mode=mode):
                guard = GUARD + b"\n" if mode == "native" else b""
                # A final CR without LF ends the last line as well.
                migrated, output = self.migrated(b"# a\r\n# b\r", mode)
                self.assertEqual(migrated, guard + b"# a\n# b\n" + HOOK + b"\n")
                self.assertIn(CONVERTED, output)
                # Every CR that ends a line goes; one inside a line stays for rTorrent to refuse.
                migrated, output = self.migrated(b'# a\r\r\nexecute.nothrow = echo,"x\ry"\n', mode)
                self.assertEqual(migrated, guard + b'# a\nexecute.nothrow = echo,"x\ry"\n' + HOOK + b"\n")
                self.assertIn(CONVERTED, output)

    def test_lf_files_report_no_conversion(self):
        migrated, output = self.migrated(b"# a\n")
        self.assertEqual(migrated, GUARD + b"\n# a\n" + HOOK + b"\n")
        self.assertNotIn("Converted", output)


if __name__ == "__main__":
    unittest.main()
