"""FileBot hooks of the literal rc migration.

startup asks update-config for them in an image with FileBot. The migrator
appends each hook unless a method.set_key line that rTorrent runs already
names the same event and key, or may name it: a later definition of a key
replaces the earlier command, so a second hook would override the operator's.
The arguments are read by rTorrent's own rules. The fixture pairs cover the
file shapes; these cases cover recognition.
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
                           ("filebot-literal-forms", "native"), ("legacy-filebot", "legacy"),
                           ("legacy-filebot-literal-forms", "legacy")):
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

    def test_literal_forms_name_the_event_and_key_that_rtorrent_parses(self):
        # rpc/parse.cc and parse_commands.cc at 56194a38 and 0.9.8: an unquoted
        # ';' ends the command and parse_command_file never runs the rest of the
        # line; a {list} holds the arguments; a backslash takes the next
        # character literally, quoted or not. Each line sets or erases exactly
        # the image's event and key in both daemons.
        variants = {
            FINISHED: ['method.set_key = event.download.finished,filebot; intentionally disabled',
                       'method.set_key = event.download.finished,filebot ;',
                       'method.set_key = event.download.finished,filebot,"execute.nothrow=/operator/postdl";echo',
                       'method.set_key = {event.download.finished,filebot,"execute.nothrow=/operator/postdl"}',
                       'method.set_key = { event.download.finished, filebot,"execute.nothrow=/operator/postdl"}',
                       'method.set_key = event.download.finished,"file\\bot","execute.nothrow=/operator/postdl"',
                       'method.set_key = event.download.finished,file\\bot,"execute.nothrow=/operator/postdl"',
                       'method.set_key = "event.download\\.finished",filebot,"execute.nothrow=/operator/postdl"',
                       'method.set_key = event.download.finished,filebot,{"execute.nothrow=/operator/postdl"}'],
            ERASED: ['method.set_key = {event.download.erased,filebot_cleaner}; erased',
                     'method.set_key = event.download.erased,"filebot_cleaner";"execute.nothrow=/operator/postrm"'],
        }
        for image, lines in variants.items():
            other = ERASED if image == FINISHED else FINISHED
            for line in lines:
                for mode in MODES:
                    with self.subTest(line=line, mode=mode):
                        migrated = self.migrated(line + "\n", mode)
                        self.assertIn(line, migrated.splitlines())
                        self.assertNotIn(image, migrated)
                        self.assertEqual(migrated.count(other), 1)

    def test_a_set_key_rtorrent_cannot_read_literally_counts_as_present(self):
        # A (call) or a $command runs while rTorrent reads the line, and a line
        # it refuses stops the daemon until the operator fixes it. Either may
        # name the image's key: appending the hook could replace the operator's
        # command, so it is left out and the reason is printed.
        both = (FINISHED, ERASED)
        for line, skipped in (
                ('method.set_key = event.download.finished,(cat,file,bot),"execute.nothrow=/operator/postdl"', (FINISHED,)),
                ('method.set_key = event.download.finished,$cat=filebot,"execute.nothrow=/operator/postdl"', (FINISHED,)),
                ('method.set_key = {event.download.erased,"$cat=filebot_cleaner"}', (ERASED,)),
                ('method.set_key = (cat,event.download.,finished),filebot,"execute.nothrow=/operator/postdl"', both),
                ('method.set_key = "$cat=event.download.erased",filebot_cleaner', both),
                ('method.set_key = event.download.erased,"filebot_cleaner', both),
                ('method.set_key = event.download.finished,"filebot"x,"execute.nothrow=/operator/postdl"', both),
                ('method.set_key = {event.download.finished,filebot', both),
                ('method.set_key = {event.download.finished,filebot},"execute.nothrow=/operator/postdl"', both)):
            for mode in MODES:
                with self.subTest(line=line, mode=mode):
                    migrated = self.migrated(line + "\n", mode)
                    self.assertIn(line, migrated.splitlines())
                    for hook in both:
                        self.assertEqual(migrated.count(hook), 0 if hook in skipped else 1)
                    with tempfile.TemporaryDirectory() as scratch:
                        path = Path(scratch) / "config.rc"
                        path.write_text(line + "\n")
                        result = subprocess.run(["php", "-n", str(MIGRATOR), str(path), "test.rc", mode, "filebot"],
                                                check=True, capture_output=True, text=True, env=ENVIRONMENT)
                    notices = result.stderr.splitlines()
                    self.assertEqual(len(notices), len(skipped), result.stderr)
                    for notice, hook in zip(notices, skipped):
                        self.assertTrue(notice.startswith("test.rc:1: "), notice)
                        self.assertIn(",".join(hook.split(" = ", 1)[1].split(",")[:2]), notice)

    def test_other_keys_events_and_lines_rtorrent_skips_do_not_count(self):
        for line in ('method.set_key = event.download.finished,filebot_custom,"execute.nothrow=echo"',
                     'method.set_key = event.download.erased,filebot_cleaner_custom,"execute.nothrow=echo"',
                     'method.set_key = event.download.erased,filebot,"execute.nothrow=echo"',
                     'method.set_key = event.download.finished,filebot_cleaner,"execute.nothrow=echo"',
                     'method.set_key = event.download.finished,"filebot ","execute.nothrow=echo"',
                     'method.set_key = event.download.finished,filebot\\ ,"execute.nothrow=echo"',
                     'method.set_key = {event.download.finished,filebot ,"execute.nothrow=echo"}',
                     'method.set_key = event.download.finished,"file\\\\bot","execute.nothrow=echo"',
                     'method.set_key = event.download.finished,filebot_cleaner;filebot',
                     'dht.mode.set = off; method.set_key = event.download.finished,filebot,"execute.nothrow=echo"',
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
