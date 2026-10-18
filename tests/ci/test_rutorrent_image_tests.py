"""Only the ruTorrent test files that image smoke runs are packaged in the image."""

import os
import re
import shutil
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path

from dockerfile_parser import stages as dockerfile_stages

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prune-rutorrent-tests.sh"
LIST = ROOT / "scripts/rutorrent-image-tests.list"
# Packaged-app paths as smoke spells them: absolute, or below a fork root variable.
APP_TEST = re.compile(r"(?:/rutorrent/app|\$\{?fork\}?|\$1|/candidate-fork)/(tests/[A-Za-z0-9_./-]+)")
# PATH wrappers that interrupt a prune. find lists the tree sorted, so the unlisted
# fixtures (tests/js, tests/php) are pruned before the listed tests/plugins files; rm
# performs PRUNE_ACTION once, on the first test file it removes, and then removes it.
INTERRUPTING_TOOLS = {
    "find": """\
if [ "$*" = 'tests ! -type d' ]; then
    tree=$("$PRUNE_FIND" "$@") || exit
    [ -z "$tree" ] || printf '%s\\n' "$tree" | LC_ALL=C sort
else
    exec "$PRUNE_FIND" "$@"
fi
""",
    "rm": """\
case " $* " in
*" $PRUNE_APP/tests/"*)
    if mkdir "$PRUNE_STATE/acted" 2>/dev/null; then
        case $PRUNE_ACTION in
        signal) kill -s "$PRUNE_SIGNAL" "$PPID" ;;
        drop-keep-list) "$PRUNE_RM" -f -- "$TMPDIR"/* ;;
        lose) "$PRUNE_RM" -f -- "$PRUNE_APP/$PRUNE_LOSE" ;;
        plant) : > "$PRUNE_APP/tests/planted.php" ;;
        esac
    fi ;;
esac
exec "$PRUNE_RM" "$@"
""",
}
# The first read of the keep-list fails with an I/O error after one entry.
PARTIAL_READ_GREP = """\
if [ "$1" = -v ]; then
    "$PRUNE_GREP" "$@" | head -n 1
    echo "grep: $PRUNE_LIST: Input/output error" >&2
    exit 2
fi
exec "$PRUNE_GREP" "$@"
"""


def keep_list():
    return [line for line in LIST.read_text().splitlines() if line and not line.startswith("#")]


def default_signals():
    # A non-interactive shell cannot trap a signal that was ignored on entry (nohup).
    for number in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(number, signal.SIG_DFL)


class ImageTestKeepList(unittest.TestCase):
    def test_keep_list_is_exactly_what_image_smoke_runs(self):
        referenced = set()
        for path in (ROOT / "tests/smoke").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                referenced.update(APP_TEST.findall(path.read_text(errors="replace")))
        self.assertEqual(set(keep_list()), referenced)
        self.assertEqual(len(keep_list()), len(set(keep_list())))

    def test_runtime_receives_the_application_only_from_the_pruned_export(self):
        stages = dockerfile_stages((ROOT / "Dockerfile").read_text())
        export = " ".join(stages["rutorrent-app"].instructions)
        runtime = stages["runtime"].instructions
        self.assertIn("source=scripts/prune-rutorrent-tests.sh,target=/prune-rutorrent-tests,readonly", export)
        self.assertIn("source=scripts/rutorrent-image-tests.list,target=/rutorrent-image-tests.list,readonly", export)
        self.assertIn("&& sh /prune-rutorrent-tests /export/rutorrent/app /rutorrent-image-tests.list", export)
        self.assertIn("COPY --from=rutorrent-app /export/ /", runtime)
        self.assertFalse([instruction for instruction in runtime if "rutorrent-source" in instruction])


class PruneScript(unittest.TestCase):
    def tree(self, temp, strict=True, keep=None, extra=("tests/php/CacheTest.php", "tests/js/a/b.test.js")):
        root = Path(temp) / "app"
        (root / "tools").mkdir(parents=True)
        if strict:
            (root / "tools/native-helpers.list").write_text("version\t1\n")
        for relative in (keep_list() if keep is None else keep) + list(extra):
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative + "\n")
        return root

    def prune(self, root):
        return subprocess.run(["sh", str(SCRIPT), str(root), str(LIST)],
                              capture_output=True, text=True, timeout=30, check=False)

    def remaining(self, root):
        return sorted(str(path.relative_to(root)) for path in (root / "tests").rglob("*") if not path.is_dir())

    def missing(self, root):
        return [path for path in keep_list() if not (root / path).is_file()]

    def interrupted(self, temp, root, action, grep=None, **settings):
        """Prune with INTERRUPTING_TOOLS; returns the result, whether rm acted and the scratch left."""
        tools, state, scratch = (Path(temp) / name for name in ("bin", "state", "tmp"))
        for directory in (tools, state, scratch):
            directory.mkdir()
        for name, body in dict(INTERRUPTING_TOOLS, **({"grep": grep} if grep else {})).items():
            (tools / name).write_text("#!/bin/sh\n" + body)
            (tools / name).chmod(0o755)
        environment = dict(os.environ, PATH=f"{tools}{os.pathsep}{os.environ['PATH']}", TMPDIR=str(scratch),
                           PRUNE_APP=str(root), PRUNE_STATE=str(state), PRUNE_ACTION=action,
                           PRUNE_LIST=str(LIST), **settings,
                           **{f"PRUNE_{name.upper()}": shutil.which(name) for name in ("find", "grep", "rm")})
        result = subprocess.run(["sh", str(SCRIPT), str(root), str(LIST)], env=environment,
                                capture_output=True, text=True, timeout=30, check=False,
                                preexec_fn=default_signals)
        return result, (state / "acted").is_dir(), sorted(path.name for path in scratch.iterdir())

    def test_strict_source_keeps_only_listed_files_and_removes_emptied_directories(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp)
            result = self.prune(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.remaining(root), sorted(keep_list()))
            self.assertFalse((root / "tests/js").exists())
            self.assertFalse((root / "tests/php").exists())
            self.assertEqual((root / keep_list()[0]).read_text(), keep_list()[0] + "\n")

    def test_strict_source_missing_a_listed_test_is_refused_by_name(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            missing = keep_list()[1]
            root = self.tree(temp, keep=[path for path in keep_list() if path != missing])
            result = self.prune(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"image smoke test missing from ruTorrent source: {missing}", result.stderr)
            self.assertTrue((root / "tests/php/CacheTest.php").exists())

    def test_listed_symlink_is_refused(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            linked = keep_list()[0]
            root = self.tree(temp, keep=[path for path in keep_list() if path != linked])
            (root / linked).parent.mkdir(parents=True, exist_ok=True)
            (root / linked).symlink_to(root / "tests/php/CacheTest.php")
            result = self.prune(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn(f"image test is not a regular file: {linked}", result.stderr)

    def test_lenient_source_keeps_what_it_has_and_no_tests_directory_is_accepted(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp, strict=False, keep=keep_list()[:1])
            result = self.prune(root)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.remaining(root), keep_list()[:1])
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp, strict=False, keep=[], extra=())
            self.assertFalse((root / "tests").exists())
            result = self.prune(root)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_path_with_a_newline_is_refused_before_any_deletion(self):
        # Split at its newline, the file or the directory name lists a file outside tests/.
        for strict in (True, False):
            for name, outside in (("tests/php/x\nindex.html", "index.html"),
                                  ("tests/js/x\nphp/settings.php", "php/settings.php")):
                with self.subTest(strict=strict, name=name), \
                        tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
                    root = self.tree(temp, strict=strict)
                    for path in (name, outside):
                        (root / path).parent.mkdir(parents=True, exist_ok=True)
                        (root / path).write_text("<?php\n")
                    before = self.remaining(root)
                    result = self.prune(root)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("ruTorrent test path contains a newline", result.stderr)
                    self.assertEqual(self.remaining(root), before)
                    self.assertTrue((root / outside).is_file())

    def test_residue_left_after_pruning_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp)
            result, acted, _ = self.interrupted(temp, root, "plant")
            self.assertTrue(acted)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unexpected ruTorrent test residue: tests/planted.php", result.stderr)

    def test_signal_while_pruning_ends_the_run_with_its_status_and_keeps_the_listed_tests(self):
        for name, status in (("HUP", 129), ("INT", 130), ("TERM", 143)):
            with self.subTest(signal=name), tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
                root = self.tree(temp)
                result, acted, scratch = self.interrupted(temp, root, "signal", PRUNE_SIGNAL=name)
                self.assertTrue(acted)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(self.missing(root), [])
                self.assertEqual(scratch, [])

    def test_keep_list_read_error_while_pruning_is_fatal_not_an_unlisted_test(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp)
            result, acted, _ = self.interrupted(temp, root, "drop-keep-list")
            self.assertTrue(acted)
            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read image test keep-list", result.stderr)
            self.assertEqual(self.missing(root), [])

    def test_partial_read_of_the_keep_list_is_fatal_before_any_deletion(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp)
            before = self.remaining(root)
            result, _, _ = self.interrupted(temp, root, "none", grep=PARTIAL_READ_GREP)
            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot read image test keep-list", result.stderr)
            self.assertEqual(self.remaining(root), before)

    def test_listed_test_lost_while_pruning_is_refused_by_name(self):
        lost = keep_list()[-1]
        for strict in (True, False):
            with self.subTest(strict=strict), tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
                root = self.tree(temp, strict=strict)
                result, acted, _ = self.interrupted(temp, root, "lose", PRUNE_LOSE=lost)
                self.assertTrue(acted)
                self.assertEqual(result.returncode, 1)
                self.assertIn(f"image smoke test lost while pruning: {lost}", result.stderr)


if __name__ == "__main__":
    unittest.main()
