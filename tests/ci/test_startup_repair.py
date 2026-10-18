"""Shipped startup.php helpers: admission refusals with their repair guidance,
the rc paths they admit, when startup admits the rc, and the image-tree remover."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php
from shell_scan import commands, top_level_lists

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "rootfs/usr/local/lib/rutorrent/startup.php"
STARTUP = ROOT / "rootfs/usr/local/bin/startup"

# Load only the helper's top-level function declarations, found by the PHP
# tokenizer rather than by a literal source line, so its root-only body never
# runs here however the operational code is rearranged. Each test appends the
# call it checks; its own arguments follow the helper and scratch paths.
PROBE = r"""<?php
declare(strict_types=1);
[, $helper, $functions] = $argv;
$code = '';
$depth = 0;
$capture = false;
$statementStart = true;
foreach (token_get_all(file_get_contents($helper)) as $token) {
    [$id, $text] = is_array($token) ? [$token[0], $token[1]] : [null, $token];
    if (!$capture && $depth === 0 && $statementStart && $id === T_FUNCTION) {
        $capture = true;
    }
    if ($capture) {
        $code .= $text;
    }
    if ($text === '{' || $id === T_CURLY_OPEN || $id === T_DOLLAR_OPEN_CURLY_BRACES) {
        ++$depth;
    } elseif ($text === '}' && --$depth === 0 && $capture) {
        $capture = false;
        $code .= "\n";
    }
    if (!in_array($id, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
        $statementStart = $depth === 0 && ($id === T_OPEN_TAG || in_array($text, [';', '{', '}'], true));
    }
}
file_put_contents($functions, "<?php\ndeclare(strict_types=1);\n" . $code);
require $functions;
foreach (['attest', 'repairHint', 'refuse', 'rcPath', 'linkTo', 'removeTree'] as $required) {
    if (!function_exists($required)) {
        fwrite(STDERR, "startup.php no longer declares $required() at top level\n");
        exit(2);
    }
}
$uid = posix_geteuid();
$gid = 991;
"""


@requires_host_php("startup admission", extensions=("tokenizer", "posix"))
class StartupRepairDiagnosticsTests(unittest.TestCase):
    def probe(self, scratch, call, *args):
        probe = Path(scratch) / "probe.php"
        probe.write_text(PROBE + call + "\n")
        return subprocess.run(["php", str(probe), str(HELPER), str(Path(scratch) / "functions.php"), *args],
                              text=True, capture_output=True)

    def refuse(self, rule, mode):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "unsafe"
            if rule == "parent":
                path.mkdir()
            else:
                path.touch()
            path.chmod(mode)
            return self.probe(scratch, "attest($argv[3], 'fixture', $uid, $argv[4]);", str(path), rule)

    def rc_paths(self, text):
        """Both admitted paths of an rc holding TEXT, as written byte for byte."""
        with tempfile.TemporaryDirectory() as scratch:
            rc = Path(scratch) / "rtorrent.rc"
            rc.write_bytes(text if isinstance(text, bytes) else text.encode())
            return self.probe(scratch, "echo rcPath($argv[3], 'session.path.set'), \"\\n\", rcPath($argv[3], 'directory.default.set'), \"\\n\";", str(rc))

    def test_quoted_rc_path_refusal_names_double_quote(self):
        result = self.rc_paths('session.path.set = "/data/has' + chr(92) + '"quote"\n')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("double quotes", result.stderr)

    def test_rc_paths_admit_lf_lines_and_a_last_line_without_newline(self):
        for text in ("session.path.set = /data/.session\ndirectory.default.set = /data/downloads\n",
                     'session.path.set = "/data/.session"\ndirectory.default.set = /data/downloads'):
            with self.subTest(text=text):
                result = self.rc_paths(text)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, "/data/.session\n/data/downloads\n")

    def test_crlf_rc_path_refusal_names_the_line_endings_rtorrent_rejects(self):
        # rTorrent ends a command only at LF and rejects the CR before it as
        # junk, so admission refuses these lines with that cause, not quoting.
        for text, command in (
                ("session.path.set = /data/.session\r\ndirectory.default.set = /data/downloads\r\n", "session.path.set"),
                ('session.path.set = "/data/.session" \r\ndirectory.default.set = "/data/downloads"\r\n', "session.path.set"),
                ("session.path.set = /data/.session\ndirectory.default.set = /data/downloads # moved\r\n", "directory.default.set")):
            with self.subTest(text=text):
                result = self.rc_paths(text)
                self.assertEqual(result.returncode, 78, result.stderr)
                self.assertIn(f"{command} line in", result.stderr)
                self.assertIn("CRLF line ending", result.stderr)
                self.assertIn("convert the file to LF line endings while stopped", result.stderr)

    def test_unquoted_rc_path_semicolon_needs_quotes(self):
        # rTorrent ends an unquoted value at ';' and ignores the rest of the line,
        # so admission must not attest a path the daemon does not use.
        result = self.rc_paths("session.path.set = /data/.session;x\ndirectory.default.set = /data/downloads\n")
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("session.path.set must use a quoted literal path", result.stderr)
        result = self.rc_paths('session.path.set = "/data/.session;x"\ndirectory.default.set = /data/downloads\n')
        self.assertEqual((result.returncode, result.stdout), (0, "/data/.session;x\n/data/downloads\n"), result.stderr)

    def test_rc_comment_ending_in_backslash_hides_the_next_line(self):
        # rTorrent continues a line that ends in an odd run of backslashes, even a
        # comment, so the decoy below is never run and the split command is.
        result = self.rc_paths("# note \\\nsession.path.set = /data/decoy\nsession.path.\\\nset = /data/real\n"
                               "directory.default.set = /data/downloads\n")
        self.assertEqual((result.returncode, result.stdout), (0, "/data/real\n/data/downloads\n"), result.stderr)

    def test_rc_continuation_drops_the_whole_odd_backslash_run(self):
        result = self.rc_paths("session.path.\\\nset = /data/.session\ndirectory.default.set = /data/down\\\\\\\nloads\n")
        self.assertEqual((result.returncode, result.stdout), (0, "/data/.session\n/data/downloads\n"), result.stderr)

    def test_rc_even_backslash_run_and_open_continuation_at_end_do_not_continue(self):
        # An even run is literal; a continuation still open at end of file is never run.
        result = self.rc_paths("# note \\\\\nsession.path.set = /data/.session\ndirectory.default.set = /data/downloads\n"
                               "session.path.set = /data/decoy\\")
        self.assertEqual((result.returncode, result.stdout), (0, "/data/.session\n/data/downloads\n"), result.stderr)

    def test_rc_line_of_4095_bytes_is_refused_like_rtorrent(self):
        paths = "session.path.set = /data/.session\ndirectory.default.set = /data/downloads\n"
        # Measured on the image daemon: 4094 bytes run, 4095 fail, joined or last.
        for case, text, refused in (("4094", "#" + "x" * 4093 + "\n" + paths, False),
                                    ("4095", "#" + "x" * 4094 + "\n" + paths, True),
                                    ("joined 4094", "#" + "x" * 1999 + "\\\n" + "x" * 2094 + "\n" + paths, False),
                                    ("joined 4095", "#" + "x" * 1999 + "\\\n" + "x" * 2095 + "\n" + paths, True),
                                    ("last 4095 without LF", paths + "#" + "x" * 4094, True)):
            with self.subTest(case=case):
                result = self.rc_paths(text)
                if refused:
                    self.assertEqual(result.returncode, 78, result.stderr)
                    self.assertIn("has a line of 4095 or more bytes", result.stderr)
                else:
                    self.assertEqual((result.returncode, result.stdout), (0, "/data/.session\n/data/downloads\n"), result.stderr)

    def test_rc_no_break_space_is_part_of_the_value_as_in_rtorrent(self):
        # rTorrent separates only spaces and tabs, so a 0xA0 byte stays in the path.
        with tempfile.TemporaryDirectory() as scratch:
            rc = Path(scratch) / "rtorrent.rc"
            rc.write_bytes(b"session.path.set = /data/.session\xa0\n")
            result = self.probe(scratch, "echo bin2hex(rcPath($argv[3], 'session.path.set'));", str(rc))
            self.assertEqual((result.returncode, result.stdout), (0, b"/data/.session\xa0".hex()), result.stderr)
        result = self.rc_paths(b"session.path.set =\xa0/data/.session\ndirectory.default.set = /data/downloads\n")
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("must be an absolute literal path", result.stderr)

    def test_carriage_return_inside_rc_path_remains_a_control_character(self):
        result = self.rc_paths('session.path.set = "/data/has\rcr"\ndirectory.default.set = /data/downloads\n')
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("control characters", result.stderr)
        self.assertNotIn("CRLF", result.stderr)

    def test_unsafe_parent_names_the_complete_offline_permissions_command(self):
        result = self.refuse("parent", 0o777)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("README.md#repairing-an-existing-profile-or-changing-uidgid", result.stderr)
        self.assertIn("docker run", result.stderr)

    def test_unsafe_witness_names_the_validating_repair_command(self):
        result = self.refuse("witness", 0o600)
        self.assertEqual(result.returncode, 78, result.stderr)
        self.assertIn("--entrypoint attest-erasedata-queue", result.stderr)
        self.assertIn("--attest-clean-legacy-queue", result.stderr)
        self.assertIn("root:root 0400", result.stderr)

    def test_hard_linked_rc_is_refused_and_a_single_link_passes(self):
        # The rc admission of rc-check and paths: owner, mode and one link.
        with tempfile.TemporaryDirectory() as scratch:
            rc = Path(scratch) / "rtorrent.rc"
            rc.write_text("session.path.set = /data/.session\n")
            rc.chmod(0o644)
            os.link(rc, Path(scratch) / "elsewhere")
            call = "attest($argv[3], 'configuration', $uid, 'file');"
            result = self.probe(scratch, call, str(rc))
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn(f"Cannot attest configuration: {rc} has unsafe ownership or mode (rule=file", result.stderr)
            self.assertIn("links=2", result.stderr)
            (Path(scratch) / "elsewhere").unlink()
            result = self.probe(scratch, call, str(rc))
            self.assertEqual((result.returncode, result.stderr), (0, ""))


class StartupRcAdmissionOrderTests(unittest.TestCase):
    def test_rc_is_admitted_before_update_config_rewrites_it(self):
        # update-config replaces the rc as root, so the admission that paths
        # performs must already have refused an unsafe file, unconditionally.
        source = STARTUP.read_text()
        self.assertTrue(("/usr/bin/php85", '"$helper"', "rc-check") in top_level_lists(source),
                        "startup must run rc-check as an unconditional top-level command")
        order = [words for words in commands(source)
                 if words[-1:] in (["rc-check"], ["paths"]) or "/usr/local/bin/update-config" in words]
        self.assertEqual([words[-1] if words[-1] != "/usr/local/bin/update-config" else "update-config" for words in order],
                         ["rc-check", "update-config", "paths"])
        self.assertIn("'rc-check'", HELPER.read_text())


@requires_host_php("startup image-tree removal", extensions=("tokenizer", "posix"))
class StartupImageTreeTests(unittest.TestCase):
    def probe(self, scratch, call, *args):
        probe = Path(scratch) / "probe.php"
        probe.write_text(PROBE + call + "\n")
        return subprocess.run(["php", str(probe), str(HELPER), str(Path(scratch) / "functions.php"), *args],
                              text=True, capture_output=True)

    def tree(self, scratch):
        """An image tree with nested data and links that removal must not follow."""
        outside = Path(scratch) / "outside"
        outside.mkdir()
        (outside / "kept").write_text("kept\n")
        target = Path(scratch) / "target"
        (target / "dir/sub").mkdir(parents=True)
        (target / "dir/sub/file").write_text("image\n")
        (target / "link").symlink_to(outside)
        (target / "dangling").symlink_to(Path(scratch) / "missing")
        return target, outside

    def test_remove_tree_removes_entries_bottom_up_without_following_links(self):
        with tempfile.TemporaryDirectory() as scratch:
            target, outside = self.tree(scratch)
            result = self.probe(scratch, "var_export(removeTree($argv[3]));", str(target))
            self.assertEqual((result.returncode, result.stdout), (0, "true"), result.stderr)
            self.assertFalse(os.path.lexists(target))
            self.assertEqual((outside / "kept").read_text(), "kept\n")

    @unittest.skipIf(os.geteuid() == 0, "root removes entries of a read-only directory")
    def test_remove_tree_continues_past_and_reports_a_remaining_entry(self):
        with tempfile.TemporaryDirectory() as scratch:
            target, outside = self.tree(scratch)
            (target / "dir/sub").chmod(0o555)
            try:
                result = self.probe(scratch, "var_export(removeTree($argv[3]));", str(target))
            finally:
                (target / "dir/sub").chmod(0o755)
            self.assertEqual((result.returncode, result.stdout), (0, "false"), result.stderr)
            self.assertTrue((target / "dir/sub/file").is_file())
            self.assertFalse(os.path.lexists(target / "link"))
            self.assertFalse(os.path.lexists(target / "dangling"))
            self.assertEqual((outside / "kept").read_text(), "kept\n")

    def test_link_to_replaces_an_image_tree_once(self):
        with tempfile.TemporaryDirectory() as scratch:
            target, outside = self.tree(scratch)
            call = "linkTo($argv[3], $argv[4]); linkTo($argv[3], $argv[4]); echo readlink($argv[4]);"
            result = self.probe(scratch, call, str(outside), str(target))
            self.assertEqual((result.returncode, result.stdout), (0, str(outside)), result.stderr)
            self.assertEqual((outside / "kept").read_text(), "kept\n")

    @unittest.skipIf(os.geteuid() == 0, "root removes entries of a read-only directory")
    def test_link_to_refuses_when_the_image_tree_remains(self):
        with tempfile.TemporaryDirectory() as scratch:
            target, outside = self.tree(scratch)
            (target / "dir/sub").chmod(0o555)
            try:
                result = self.probe(scratch, "linkTo($argv[3], $argv[4]);", str(outside), str(target))
            finally:
                (target / "dir/sub").chmod(0o755)
            self.assertEqual(result.returncode, 78, result.stderr)
            self.assertIn(f"Cannot attest configuration: cannot link {target}", result.stderr)
            self.assertTrue((target / "dir/sub/file").is_file())


if __name__ == "__main__":
    unittest.main()
