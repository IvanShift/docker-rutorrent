"""Writable checkout modes must not cross the immutable source build boundary."""

import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

HELPER = Path(__file__).resolve().parents[2] / "scripts/normalize-immutable-tree.sh"


class ImmutableSourceModesTests(unittest.TestCase):
    def test_checkout_write_bits_removed_without_changing_execution_or_link_targets(
        self,
    ):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-source-modes-") as temp:
            root = Path(temp)
            trees = [root / "app", root / "conf-dist"]
            external_directory = root / "external"
            external_directory.mkdir()
            external_directory.chmod(0o775)
            external_file = external_directory / "external.php"
            external_file.write_text("external must remain unchanged\n")
            external_file.chmod(0o664)
            controlled = {}
            for tree in trees:
                nested = tree / "nested"
                nested.mkdir(parents=True)
                tree.chmod(0o775)
                nested.chmod(0o775)
                for name, mode in {
                    "config.php": 0o664,
                    "tool.sh": 0o775,
                    "partial.sh": 0o743,
                }.items():
                    path = nested / name
                    path.write_text("#!/bin/sh\nexit 0\n")
                    path.chmod(mode)
                    controlled[path] = mode
                (tree / "external-file").symlink_to(external_file)
                (tree / "external-directory").symlink_to(
                    external_directory, target_is_directory=True
                )
            subprocess.run(
                ["sh", str(HELPER), *(str(tree) for tree in trees)],
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
            for tree in trees:
                self.assertEqual(stat.S_IMODE(tree.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE((tree / "nested").stat().st_mode), 0o755)
                self.assertTrue((tree / "external-file").is_symlink())
                self.assertTrue((tree / "external-directory").is_symlink())
            for path, before in controlled.items():
                after = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(after, before & ~0o022, path.name)
                self.assertEqual(after & 0o111, before & 0o111, path.name)
            self.assertEqual(stat.S_IMODE(external_directory.stat().st_mode), 0o775)
            self.assertEqual(stat.S_IMODE(external_file.stat().st_mode), 0o664)
            self.assertEqual(
                external_file.read_text(), "external must remain unchanged\n"
            )
