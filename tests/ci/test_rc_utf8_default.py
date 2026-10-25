"""UTF-8 path defaults must not replace an existing operator's setting."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
TEMPLATE = ROOT / "rootfs/home/torrent/.rtorrent.rc"
DEFAULT = 'catch = {"system.file_name.allow_legacy_utf8.set=1"}\n'
ZERO = 'catch = {"system.file_name.allow_legacy_utf8.set=0"}\n'
HOOK = 'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="\n'
LOAD = "load.normal = /operator.torrent\n"
ENVIRONMENT = {**os.environ, "UPDATE_PHP_PATH": "/usr/bin/php85"}


@requires_host_php("UTF-8 filename default migration")
class Utf8DefaultMigrationTests(unittest.TestCase):
    def migrated(self, source, mode="native"):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_text(source)
            command = ["php", "-n", str(MIGRATOR), str(path), "test.rc", mode]
            subprocess.run(command, check=True, capture_output=True, text=True, env=ENVIRONMENT)
            first = path.read_bytes()
            second = subprocess.run(command, check=True, capture_output=True, text=True, env=ENVIRONMENT)
            self.assertEqual(path.read_bytes(), first, "second migration must preserve bytes and order")
            self.assertEqual(second.stdout, "", "second migration must report no change")
            return first.decode()

    def test_default_is_set_before_load_when_no_override_exists(self):
        for source in ("", LOAD, "# operator configuration\n" + LOAD):
            with self.subTest(source=source):
                self.assertEqual(self.migrated(source), DEFAULT + source + HOOK)

    def test_image_template_enables_utf8_aliases(self):
        template = TEMPLATE.read_text()
        self.assertEqual(template.splitlines()[0], DEFAULT.rstrip("\n"))
        migrated = self.migrated(template)
        self.assertEqual(migrated.splitlines().count(DEFAULT.rstrip("\n")), 1)

    def test_existing_catch_values_and_duplicates_keep_their_order(self):
        for source in (DEFAULT, ZERO, ZERO + ZERO, ZERO + DEFAULT,
                       DEFAULT + ZERO, DEFAULT + ZERO + DEFAULT,
                       DEFAULT + DEFAULT + ZERO + DEFAULT):
            with self.subTest(source=source):
                expected = source if source.startswith(DEFAULT) else DEFAULT + source
                self.assertEqual(self.migrated(source + LOAD), expected + LOAD + HOOK)

    def test_bare_values_are_wrapped_without_losing_later_overrides(self):
        for value, wrapped in (("0", ZERO), ("1", DEFAULT)):
            with self.subTest(value=value):
                source = f"system.file_name.allow_legacy_utf8.set = {value}\n"
                self.assertEqual(self.migrated(source + LOAD), DEFAULT + wrapped + LOAD + HOOK)
        bare_zero = "system.file_name.allow_legacy_utf8.set = 0\n"
        bare_one = "system.file_name.allow_legacy_utf8.set = 1\n"
        for source in (bare_zero + bare_one, ZERO + bare_one, bare_zero + DEFAULT):
            with self.subTest(source=source):
                self.assertEqual(self.migrated(source + LOAD), DEFAULT + ZERO + DEFAULT + LOAD + HOOK)

    def test_default_does_not_move_a_later_override_before_an_earlier_load(self):
        source = ZERO + LOAD + DEFAULT + "load.normal = /second.torrent\n"
        self.assertEqual(self.migrated(source), DEFAULT + source + HOOK)

    def test_indented_and_dynamic_overrides_are_preserved(self):
        for source, expected in (
            ('  catch = {"system.file_name.allow_legacy_utf8.set=0"}\n',
             '  catch = {"system.file_name.allow_legacy_utf8.set=0"}\n'),
            ('  system.file_name.allow_legacy_utf8.set = 0\n',
             '  catch = {"system.file_name.allow_legacy_utf8.set=0"}\n'),
            ('system.file_name.allow_legacy_utf8.set = (value,1)\n',
             'catch = {"system.file_name.allow_legacy_utf8.set=(value,1)"}\n'),
            ('catch = {"system.file_name.allow_legacy_utf8.set=$operator.utf8="}\n',
             'catch = {"system.file_name.allow_legacy_utf8.set=$operator.utf8="}\n'),
        ):
            with self.subTest(source=source):
                self.assertEqual(self.migrated(source + LOAD), DEFAULT + expected + LOAD + HOOK)

    def test_legacy_mode_keeps_catches_and_comments_bare_native_commands(self):
        for value in ("0", "1"):
            with self.subTest(value=value):
                source = f"system.file_name.allow_legacy_utf8.set = {value}\n"
                self.assertEqual(self.migrated(source, "legacy"), "# docker-rutorrent: native " + source + HOOK)
        for source in (ZERO + DEFAULT + LOAD, DEFAULT + ZERO + LOAD, DEFAULT + LOAD):
            with self.subTest(source=source):
                self.assertEqual(self.migrated(source, "legacy"), source + HOOK)


if __name__ == "__main__":
    unittest.main()
