import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / "scripts/build-rutorrent-helpers.sh"
PAIRS = {
    "rutorrent-task-kill-pidfd": "plugins/_task/kill-verified.c",
    "rutorrent-task-supervise": "plugins/_task/supervise.c",
    "rutorrent-erasedata-rename-noreplace": "plugins/erasedata/rename-noreplace.c",
}


class Contract(unittest.TestCase):
    def source_tree(self, temp):
        root = Path(temp) / "source"
        (root / "php").mkdir(parents=True)
        (root / "plugins").mkdir()
        (root / "tools").mkdir()
        return root

    def write_manifest(self, root, entries):
        (root / "tools/native-helpers.list").write_text(
            "version\t1\n"
            "flags\t-std=c11 -O2 -Wall -Wextra -Werror -DREM4_DECLARED_FLAG=1\n"
            + entries
        )

    def run_build(self, root, output):
        return subprocess.run(
            ["sh", str(HELPER), str(root), str(output)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_moved_references_and_missing_helper(self):
        with tempfile.TemporaryDirectory(prefix="rem4-build-helpers-") as temp:
            root = Path(temp) / "source"
            (root / "php").mkdir(parents=True)
            (root / "plugins").mkdir()
            out = Path(temp) / "out"
            for name, relative in PAIRS.items():
                p = root / relative
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text("int main(void) { return 0; }\n")
            (root / "php" / "new-location.php").write_text(
                "<?php\n"
                + "\n".join(
                    "$helper = '/usr/local/bin/" + name + "';" for name in PAIRS
                )
            )
            result = self.run_build(root, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (out / "usr/local/share/rutorrent-native-helpers")
                .read_text()
                .splitlines()[0],
                "contract=native",
            )
            for name in PAIRS:
                self.assertTrue((out / "usr/local/bin" / name).exists())
            self.assertFalse(
                (out / "usr/local/share/rutorrent-native-capabilities").exists()
            )
            (root / "php" / "new-location.php").write_text(
                "<?php $helper = '/usr/local/bin/rutorrent-future-helper';"
            )
            result = self.run_build(root, Path(temp) / "missing")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required native helper unavailable", result.stderr)

    def test_explicit_helper_free_source(self):
        with tempfile.TemporaryDirectory(prefix="rem4-build-old-") as temp:
            root = Path(temp) / "source"
            (root / "php").mkdir(parents=True)
            (root / "plugins").mkdir()
            result = self.run_build(root, Path(temp) / "out")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (
                    Path(temp) / "out/usr/local/share/rutorrent-native-helpers"
                ).read_text(),
                "contract=legacy\n",
            )

    def test_missing_source_tree_rejected(self):
        with tempfile.TemporaryDirectory(prefix="rem4-build-missing-") as temp:
            result = self.run_build(Path(temp) / "source", Path(temp) / "out")
            self.assertNotEqual(result.returncode, 0)

    def test_manifest_compiles_declared_fourth_helper_with_declared_flags(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-manifest-") as temp:
            root = self.source_tree(temp)
            declared = PAIRS | {"rutorrent-future-helper": "tools/future-helper.c"}
            for relative in declared.values():
                source = root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(
                    "#ifndef REM4_DECLARED_FLAG\n#error declared flag missing\n#endif\n"
                    "int main(void) { return REM4_DECLARED_FLAG == 1 ? 0 : 1; }\n"
                )
            self.write_manifest(
                root,
                "".join(f"{name}\t{relative}\n" for name, relative in declared.items()),
            )
            (root / "php/future.php").write_text(
                "<?php $helper = '/usr/local/bin/rutorrent-future-helper';\n"
            )
            out = Path(temp) / "out"
            result = self.run_build(root, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            installed = out / "usr/local/bin/rutorrent-future-helper"
            self.assertEqual(
                subprocess.run([str(installed)], timeout=5, check=False).returncode, 0
            )
            self.assertEqual(
                (out / "usr/local/share/rutorrent-native-helpers")
                .read_text()
                .splitlines(),
                ["contract=native", *sorted(declared)],
            )
            self.assertEqual(
                (out / "usr/local/share/rutorrent-native-capabilities").read_text(),
                "rename-probe=1\n",
            )

    def test_final_manifest_entry_without_newline_is_compiled_and_recorded(self):
        with tempfile.TemporaryDirectory(prefix="docker-helper-eof-") as temp:
            root = self.source_tree(temp)
            for name in ("alpha", "beta"):
                (root / f"tools/{name}.c").write_text("int main(void) { return 0; }\n")
            self.write_manifest(root, "rutorrent-alpha-helper\ttools/alpha.c\nrutorrent-beta-helper\ttools/beta.c")
            out = Path(temp) / "out"
            result = self.run_build(root, out)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((out / "usr/local/bin/rutorrent-beta-helper").is_file())
            self.assertIn("rutorrent-beta-helper", (out / "usr/local/share/rutorrent-native-helpers").read_text().splitlines())

    def test_stale_output_binary_cannot_supply_missing_current_source(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-stale-helper-") as temp:
            root = self.source_tree(temp)
            missing = "rutorrent-task-kill-pidfd"
            for name, relative in PAIRS.items():
                if name == missing:
                    continue
                source = root / relative
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("int main(void) { return 0; }\n")
            (root / "php/consumer.php").write_text(
                f"<?php $helper = '/usr/local/bin/{missing}';\n"
            )
            out = Path(temp) / "out"
            stale = out / "usr/local/bin" / missing
            stale.parent.mkdir(parents=True)
            stale.write_text("#!/bin/sh\nexit 0\n")
            stale.chmod(0o755)
            result = self.run_build(root, out)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("required native helper unavailable", result.stderr)

    def test_manifest_requires_source_even_without_php_reference(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-manifest-missing-") as temp:
            root = self.source_tree(temp)
            self.write_manifest(root, "rutorrent-future-helper\ttools/missing.c\n")
            result = self.run_build(root, Path(temp) / "out")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("declared native helper source unavailable", result.stderr)

    def test_manifest_rejects_malformed_or_duplicate_declarations(self):
        entries = "rutorrent-future-helper\ttools/future.c\n"
        invalid = {
            "duplicate-helper": entries,
            "duplicate-version": "version\t1\n",
            "duplicate-flags": "flags\t-std=c11\n",
            "unknown-directive": "unexpected\tvalue\n",
            "extra-column": "rutorrent-other-helper\ttools/other.c\textra\n",
            "missing-value": "rutorrent-other-helper\t\n",
            "parent-path": "rutorrent-other-helper\t../outside.c\n",
            "absolute-path": "rutorrent-other-helper\t/tmp/outside.c\n",
        }
        for name, malformed in invalid.items():
            with (
                self.subTest(name=name),
                tempfile.TemporaryDirectory(prefix="rem4-ci-invalid-manifest-") as temp,
            ):
                root = self.source_tree(temp)
                (root / "tools/future.c").write_text("int main(void) { return 0; }\n")
                self.write_manifest(root, entries + malformed)
                out = Path(temp) / "out"
                result = self.run_build(root, out)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertFalse(
                    (out / "usr/local/bin/rutorrent-future-helper").exists()
                )


if __name__ == "__main__":
    unittest.main()
