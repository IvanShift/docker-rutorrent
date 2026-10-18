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

    def test_literal_forms_of_an_operator_save_are_recognized(self):
        # rTorrent reads these arguments as event.system.shutdown, operator_save
        # and session.save= (see test_rc_filebot for the parser rules).
        for hook in ('method.set_key = event.system.shutdown,operator_save,"session.save="; note',
                     'method.set_key = {event.system.shutdown,operator_save,"session.save="}',
                     'method.set_key = event.system.shutdown,"operator\\_save",session.save=;'):
            for mode in MODES:
                with self.subTest(hook=hook, mode=mode):
                    migrated = self.migrated(hook + "\n", mode)
                    self.assertNotIn("docker_rutorrent_early_save", migrated)
                    self.assertEqual(migrated.count("event.system.shutdown"), 1)

    def test_a_continued_operator_save_is_recognized(self):
        # rTorrent joins the continued line, so this is one complete save hook.
        for mode in MODES:
            with self.subTest(mode=mode):
                hook = 'method.set_key = event.system.shutdown,operator_save,\\\n  "session.save="\n'
                migrated = self.migrated(hook, mode)
                self.assertNotIn("docker_rutorrent_early_save", migrated)
                self.assertTrue(migrated.endswith(hook))

    def test_a_literal_erase_of_the_image_key_opts_out(self):
        # README "Shutdown and health": the hook is optional (the image daemon
        # saves on its own; the hook serves rollbacks), and a commented hook is
        # an opt-out. Erasing one of its keys says the same: appending the hook
        # after the erase would undo it. The keys are those of the comment rule.
        for erase in ("method.set_key = event.system.shutdown,docker_rutorrent_early_save",
                      'method.set_key = {event.system.shutdown,"docker_rutorrent_early_save"}; opt out',
                      "system.method.set_key = event.system.shutdown , docker_rutorrent_early_save__"):
            for mode in MODES:
                with self.subTest(erase=erase, mode=mode):
                    migrated = self.migrated(erase + "\n", mode)
                    self.assertEqual(migrated.count("event.system.shutdown"), 1)
                    self.assertIn(erase.split("=", 1)[1], migrated)  # native renames system.method.*

    def test_other_erases_do_not_opt_out(self):
        # Another key, a computed key and an image key that is set, not erased.
        for line in ("method.set_key = event.system.shutdown,operator_save",
                     "method.set_key = event.system.shutdown,docker_rutorrent_early_save_custom",
                     "method.set_key = event.system.shutdown,(cat,docker_rutorrent_early_save)",
                     "method.set_key = event.download.erased,docker_rutorrent_early_save",
                     "dht.mode.set = off; method.set_key = event.system.shutdown,docker_rutorrent_early_save"):
            for mode in MODES:
                with self.subTest(line=line, mode=mode):
                    self.assertTrue(self.migrated(line + "\n", mode).endswith(HOOK + "\n"))

    def test_erasing_an_operator_save_requires_the_image_hook(self):
        # Only the final hook state counts: the daemon removes operator_save,
        # and erasing that non-image key does not opt out of the image hook.
        save = 'method.set_key = event.system.shutdown,operator_save,"session.save="\n'
        for erase in ("method.set_key = event.system.shutdown,operator_save",
                      'method.set_key = {event.system.shutdown,"operator\\_save"}; erased'):
            for mode in MODES:
                with self.subTest(erase=erase, mode=mode):
                    source = save + erase + "\n"
                    migrated = self.migrated(source, mode)
                    self.assertTrue(migrated.endswith(source + HOOK + "\n"), migrated)
                    self.assertEqual(migrated.count(HOOK), 1)

    def test_the_last_set_after_an_erase_decides_if_a_save_exists(self):
        prefix = ('method.set_key = event.system.shutdown,operator_save,"session.save="\n'
                  'method.set_key = event.system.shutdown,operator_save\n')
        for command, needs_image_hook in (('"session.save="', False),
                                          ('"execute.nothrow=echo,custom"', True)):
            for mode in MODES:
                with self.subTest(command=command, mode=mode):
                    source = prefix + f"method.set_key = event.system.shutdown,operator_save,{command}\n"
                    migrated = self.migrated(source, mode)
                    self.assertTrue(migrated.endswith(source + (HOOK + "\n" if needs_image_hook else "")), migrated)
                    self.assertEqual(migrated.count(HOOK), int(needs_image_hook))

    def test_erasing_one_operator_key_keeps_another_save(self):
        source = ('method.set_key = event.system.shutdown,old_save,"session.save="\n'
                  'method.set_key = event.system.shutdown,current_save,"session.save="\n'
                  'method.set_key = event.system.shutdown,old_save\n')
        for mode in MODES:
            with self.subTest(mode=mode):
                migrated = self.migrated(source, mode)
                self.assertTrue(migrated.endswith(source), migrated)
                self.assertNotIn("docker_rutorrent_early_save", migrated)

    def test_setting_then_erasing_an_image_key_stays_opted_out(self):
        source = ('method.set_key = event.system.shutdown,docker_rutorrent_early_save_,"session.save="\n'
                  'method.set_key = event.system.shutdown,docker_rutorrent_early_save_\n')
        for mode in MODES:
            with self.subTest(mode=mode):
                migrated = self.migrated(source, mode)
                self.assertTrue(migrated.endswith(source), migrated)
                self.assertEqual(migrated.count("event.system.shutdown"), 2)

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
