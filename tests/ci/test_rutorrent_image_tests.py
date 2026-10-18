"""Only the ruTorrent test files that image smoke runs are packaged in the image."""

import re
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prune-rutorrent-tests.sh"
LIST = ROOT / "scripts/rutorrent-image-tests.list"
# Packaged-app paths as smoke spells them: absolute, or below a fork root variable.
APP_TEST = re.compile(r"(?:/rutorrent/app|\$\{?fork\}?|\$1|/candidate-fork)/(tests/[A-Za-z0-9_./-]+)")


def keep_list():
    return [line for line in LIST.read_text().splitlines() if line and not line.startswith("#")]


class ImageTestKeepList(unittest.TestCase):
    def test_keep_list_is_exactly_what_image_smoke_runs(self):
        referenced = set()
        for path in (ROOT / "tests/smoke").rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                referenced.update(APP_TEST.findall(path.read_text(errors="replace")))
        self.assertEqual(set(keep_list()), referenced)
        self.assertEqual(len(keep_list()), len(set(keep_list())))

    def test_runtime_receives_the_application_only_from_the_pruned_export(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        stages = re.split(r"(?m)^FROM\s", dockerfile)
        export = next(stage for stage in stages if stage.splitlines()[0].endswith(" AS rutorrent-app"))
        runtime = next(stage for stage in stages if stage.splitlines()[0].endswith(" AS runtime"))
        self.assertIn("source=scripts/prune-rutorrent-tests.sh,target=/prune-rutorrent-tests,readonly", export)
        self.assertIn("source=scripts/rutorrent-image-tests.list,target=/rutorrent-image-tests.list,readonly", export)
        self.assertIn("&& sh /prune-rutorrent-tests /export/rutorrent/app /rutorrent-image-tests.list", export)
        self.assertIn("COPY --from=rutorrent-app /export/ /", runtime)
        self.assertNotIn("rutorrent-source", runtime)


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

    def test_unremovable_residue_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="docker-prune-") as temp:
            root = self.tree(temp)
            (root / "tests/php/line\nbreak.php").write_text("<?php\n")
            result = self.prune(root)
            self.assertEqual(result.returncode, 1)
            self.assertIn("unexpected ruTorrent test residue", result.stderr)


if __name__ == "__main__":
    unittest.main()
