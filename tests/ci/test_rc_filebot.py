"""FileBot hooks of the literal rc migration.

startup asks update-config for them in an image with FileBot. The migrator
appends each hook unless a method.set_key line that rTorrent runs already
names the same event and key: a later definition of a key replaces the earlier
command, so a second hook would override the operator's. The fixture pairs
cover the file shapes; these cases cover recognition.
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
FINISHED = ('method.set_key = event.download.finished,filebot,'
            '"execute.nothrow=/usr/local/bin/postdl,$d.base_path=,$d.name="')
ERASED = 'method.set_key = event.download.erased,filebot_cleaner,"execute.nothrow=/usr/local/bin/postrm"'
MODES = ("native", "legacy")
ENVIRONMENT = {**os.environ, "UPDATE_PHP_PATH": "/usr/bin/php85"}


@requires_host_php("literal rc migration")
class FileBotHookMigrationTests(unittest.TestCase):
    def migrated(self, content, mode="native", hooks="filebot"):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "config.rc"
            path.write_bytes(content.encode())
            command = ["php", "-n", str(MIGRATOR), str(path), "test.rc", mode, hooks]
            subprocess.run(command, check=True, capture_output=True, env=ENVIRONMENT)
            first = path.read_bytes()
            subprocess.run(command, check=True, capture_output=True, env=ENVIRONMENT)
            self.assertEqual(path.read_bytes(), first, "second migration must be byte-identical")
            return first.decode()

    def test_fixture_pairs(self):
        for name, mode in (("filebot-no-final-newline", "native"), ("filebot-final-comment", "native"),
                           ("filebot-crlf", "native"), ("filebot-existing", "native"),
                           ("legacy-filebot", "legacy")):
            with self.subTest(name=name):
                source = (FIXTURES / f"{name}.input.rc").read_bytes().decode()
                expected = (FIXTURES / f"{name}.expected.rc").read_bytes().decode()
                self.assertEqual(self.migrated(source, mode), expected)

    def test_hooks_are_added_only_when_asked(self):
        for mode in MODES:
            for hooks in ("", "none"):
                with self.subTest(mode=mode, hooks=hooks):
                    self.assertNotIn("filebot", self.migrated("dht.mode.set = off\n", mode, hooks))

    def test_an_operator_definition_of_the_event_and_key_stays(self):
        variants = {
            FINISHED: ['method.set_key = event.download.finished, filebot, "execute.nothrow=/operator/postdl"',
                       '  method.set_key=event.download.finished ,filebot ,"execute.nothrow=/operator/postdl"',
                       '\tsystem.method.set_key = event.download.finished,filebot,"execute2=/operator/postdl"',
                       'method.set_key = "event.download.finished","filebot","execute.nothrow=/operator/postdl"',
                       'method.set_key = event.download.finished,filebot,\\\n  "execute.nothrow=/operator/postdl"',
                       "# /operator/ erases the key\nmethod.set_key = event.download.finished,filebot"],
            ERASED: ['method.set_key = event.download.erased , filebot_cleaner , "execute.nothrow=/operator/postrm"',
                     '    system.method.set_key=event.download.erased,filebot_cleaner,"execute2=/operator/postrm"'],
        }
        for image, lines in variants.items():
            other = ERASED if image == FINISHED else FINISHED
            for line in lines:
                for mode in MODES:
                    with self.subTest(line=line, mode=mode):
                        migrated = self.migrated(line + "\n", mode)
                        self.assertNotIn(image, migrated)
                        self.assertIn("/operator/", migrated)
                        self.assertEqual(migrated.count(other), 1)

    def test_other_keys_events_and_lines_rtorrent_skips_do_not_count(self):
        for line in ('method.set_key = event.download.finished,filebot_custom,"execute.nothrow=echo"',
                     'method.set_key = event.download.erased,filebot_cleaner_custom,"execute.nothrow=echo"',
                     'method.set_key = event.download.erased,filebot,"execute.nothrow=echo"',
                     'method.set_key = event.download.finished,filebot_cleaner,"execute.nothrow=echo"',
                     'method.set_key = event.download.finished,"filebot ","execute.nothrow=echo"',
                     'method.insert = event.download.finished,filebot,"execute.nothrow=echo"',
                     'execute.nothrow = echo,"method.set_key = event.download.finished,filebot,x"',
                     '# method.set_key = event.download.finished,filebot,"execute.nothrow=echo"',
                     '# a continued comment \\\nmethod.set_key = event.download.erased,filebot_cleaner,echo'):
            for mode in MODES:
                with self.subTest(line=line, mode=mode):
                    self.assertTrue(self.migrated(line + "\n", mode).endswith(f"{FINISHED}\n{ERASED}\n"))

    def test_a_hook_continued_at_the_end_of_the_file_does_not_count(self):
        # rTorrent never runs that last line; the hooks go before it.
        tail = f"{FINISHED} \\\n"
        migrated = self.migrated("dht.mode.set = off\n" + tail)
        self.assertTrue(migrated.endswith(f"{FINISHED}\n{ERASED}\n{tail}"), migrated)


if __name__ == "__main__":
    unittest.main()
