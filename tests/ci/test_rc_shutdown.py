"""Early session-save hook semantics of the literal rc migration.

The image-level fixtures in tests/update-config (shutdown-no-newline and
legacy-shutdown-no-newline) already pin insertion, a missing final newline,
metadata and idempotency through the real update-config wrapper. These cases
pin how existing operator hooks are recognized.
"""

from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
HOOK = 'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="'
MODES = ("native", "legacy")


@requires_host_php("literal rc migration")
class ShutdownSessionMigrationTests(unittest.TestCase):
    def migrate(self, path, mode):
        return subprocess.run(["php", str(MIGRATOR), str(path), "test.rc", mode],
                              check=True, text=True, capture_output=True)

    def migrated(self, content, mode):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_text(content)
            self.migrate(path, mode)
            first = path.read_text()
            self.migrate(path, mode)
            self.assertEqual(path.read_text(), first, "second migration must be byte-identical")
            return first

    def test_operator_early_save_is_recognized_in_every_spelling(self):
        # Both setter spellings and all three quotings of the command name an
        # existing save; a second hook would save the session twice per stop.
        for spelling in ("method.set_key", "system.method.set_key"):
            for command in ('"session.save="', "'session.save='", "session.save="):
                for mode in MODES:
                    with self.subTest(spelling=spelling, command=command, mode=mode):
                        migrated = self.migrated(
                            f"{spelling} = event.system.shutdown,operator_save,{command}\n", mode)
                        self.assertNotIn("docker_rutorrent_early_save", migrated)
                        self.assertEqual(migrated.count("event.system.shutdown,"), 1)
                        self.assertIn(f"event.system.shutdown,operator_save,{command}", migrated)

    def test_reserved_key_collision_keeps_the_operator_command(self):
        custom = 'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"execute.nothrow=echo,custom"'
        for mode in MODES:
            with self.subTest(mode=mode):
                migrated = self.migrated(custom + "\n", mode)
                self.assertIn(custom, migrated)
                self.assertIn('event.system.shutdown,docker_rutorrent_early_save_,"session.save="', migrated)
                self.assertEqual(migrated.count(HOOK), 0)


if __name__ == "__main__":
    unittest.main()
