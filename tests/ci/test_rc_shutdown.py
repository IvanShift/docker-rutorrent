from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MIGRATOR = ROOT / "rootfs/usr/local/libexec/migrate-rtorrent-config.php"
HOOK = 'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="'


@unittest.skipUnless(shutil.which("php"), "PHP is required for literal rc migration")
class ShutdownSessionMigrationTests(unittest.TestCase):
    def migrate(self, path, mode):
        return subprocess.run(["php", str(MIGRATOR), str(path), "test.rc", mode],
                              check=True, text=True, capture_output=True)

    def test_missing_hook_is_added_and_preserves_custom_hook_without_final_newline(self):
        # Omitting the shutdown hook must make this fail before an image can ship.
        custom = 'method.set_key = event.system.shutdown,operator_hook,"execute.nothrow=echo,custom"'
        for mode in ("native", "legacy"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / "config.rc"
                path.write_text(custom)
                path.chmod(0o640)
                before = path.stat()
                self.migrate(path, mode)
                migrated = path.read_text()
                self.assertIn(custom + "\n", migrated)
                self.assertEqual(migrated.count(HOOK), 1)
                self.assertEqual((path.stat().st_uid, path.stat().st_gid, path.stat().st_mode),
                                 (before.st_uid, before.st_gid, before.st_mode))
                self.migrate(path, mode)
                self.assertEqual(path.read_text(), migrated)

    def test_operator_early_save_is_preserved_without_duplicate_registration(self):
        custom = 'method.set_key = event.system.shutdown,operator_save,"session.save="'
        for mode in ("native", "legacy"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / "config.rc"
                path.write_text(custom + "\n")
                self.migrate(path, mode)
                self.assertIn(custom, path.read_text())
                self.assertNotIn(HOOK, path.read_text())

    def test_reserved_key_collision_keeps_the_operator_command(self):
        custom = 'method.set_key = event.system.shutdown,docker_rutorrent_early_save,"execute.nothrow=echo,custom"'
        for mode in ("native", "legacy"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as scratch:
                path = Path(scratch) / "config.rc"
                path.write_text(custom + "\n")
                self.migrate(path, mode)
                migrated = path.read_text()
                self.assertIn(custom, migrated)
                self.assertIn('event.system.shutdown,docker_rutorrent_early_save_,"session.save="', migrated)
                self.migrate(path, mode)
                self.assertEqual(path.read_text(), migrated)


if __name__ == "__main__":
    unittest.main()
