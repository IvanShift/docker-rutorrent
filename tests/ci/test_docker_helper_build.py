"""Compile the separate Docker helper ABI, including compiler and source refusals."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
HELPER = ROOT / "scripts/build-docker-helpers.sh"


class DockerHelperBuild(unittest.TestCase):
    def build(self, source, output):
        return subprocess.run(["sh", str(HELPER), str(source), str(output)],
                              text=True, capture_output=True, timeout=30)

    def fixture(self, root):
        source = root / "source"
        source.mkdir()
        for name, code in (("ownership-walk.c", 7), ("service-runtime.c", 9), ("native-actor-paths.c", 11)):
            (source / name).write_text(f'#if __STDC_VERSION__ < 201112L\n#error C11 required\n#endif\nint main(void) {{ return {code}; }}\n')
        return source

    def test_compiled_programs_have_the_expected_independent_install_locations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            result = self.build(self.fixture(root), root / "output")
            self.assertEqual(result.returncode, 0, result.stderr)
            for name, code in (("bin/rutorrent-own-tree", 7), ("libexec/service-runtime", 9), ("libexec/native-actor-paths", 11)):
                program = root / "output/usr/local" / name
                self.assertEqual(program.stat().st_mode & 0o777, 0o755)
                self.assertEqual(subprocess.run([str(program)]).returncode, code)
            manifest = root / "output/usr/local/share/rutorrent-docker-helpers"
            self.assertEqual(manifest.read_text().splitlines(), ["version=1", "rutorrent-own-tree", "service-runtime", "native-actor-paths"])

    def test_required_missing_or_symlinked_source_is_refused(self):
        for required_name in ("ownership-walk.c", "service-runtime.c", "native-actor-paths.c"):
            for symlink in (False, True):
                with self.subTest(required_name=required_name, symlink=symlink), tempfile.TemporaryDirectory() as temp:
                    root = Path(temp)
                    source = self.fixture(root)
                    required = source / required_name
                    required.unlink()
                    if symlink:
                        required.symlink_to(source / "ownership-walk.c")
                    result = self.build(source, root / "output")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("Docker helper source unavailable: " + required_name, result.stderr)
                    self.assertFalse((root / "output/usr/local/share/rutorrent-docker-helpers").exists())

    def test_compiler_warnings_cannot_be_published_as_a_successful_helper_set(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self.fixture(root)
            (source / "service-runtime.c").write_text('int main(void) { int unused; return 0; }\n')
            result = self.build(source, root / "output")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse((root / "output/usr/local/share/rutorrent-docker-helpers").exists())


if __name__ == "__main__":
    unittest.main()
