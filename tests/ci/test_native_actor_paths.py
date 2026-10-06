"""Exercise the real fixed-path helper in a private fixture namespace."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(os.environ.get("SUPPLYCHAIN_TEST_ROOT", Path(__file__).resolve().parents[2]))
SOURCE = ROOT / "rootfs/usr/local/libexec/native-actor-paths.c"
NORMALIZER = ROOT / "scripts/normalize-immutable-tree.sh"
DIRECTORIES = ("usr", "usr/bin", "rutorrent", "rutorrent/app", "rutorrent/app/plugins",
               "rutorrent/app/plugins/retrackers", "usr/local", "usr/local/share")
PHP = "usr/bin/php85"
UPDATE = "rutorrent/app/plugins/retrackers/update.php"
DISPATCH = "rutorrent/app/plugins/retrackers/dispatch.php"
MARKER = "usr/local/share/rutorrent-native-actor-paths-required"


class NativeActorPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = tempfile.TemporaryDirectory(prefix="native-actor-compiler-")
        cls.program = Path(cls.compiler.name) / "native-actor-paths"
        subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-DNATIVE_ACTOR_TESTING", str(SOURCE), "-o", str(cls.program)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.compiler.cleanup()

    def fixture(self, root):
        namespace = root / "namespace"
        namespace.mkdir(mode=0o755)
        for name in DIRECTORIES:
            (namespace / name).mkdir(mode=0o755, parents=True, exist_ok=True)
        for name, mode in ((PHP, 0o755), (UPDATE, 0o644), (DISPATCH, 0o644)):
            (namespace / name).write_text("fixture\n")
            (namespace / name).chmod(mode)
        return namespace

    def invoke(self, namespace, *arguments, program=None):
        return subprocess.run([str(program or self.program), *arguments],
            env=dict(os.environ, NATIVE_ACTOR_TEST_ROOT=str(namespace)),
            text=True, capture_output=True, timeout=3)

    def snapshot(self, namespace):
        result = {}
        for p in [namespace, *namespace.rglob("*")]:
            info = p.lstat()
            result[str(p.relative_to(namespace))] = (info.st_dev, info.st_ino,
                info.st_uid, info.st_gid, info.st_mode, info.st_nlink,
                p.read_bytes() if p.is_file() and not p.is_symlink() else None)
        return result

    def test_historical_writable_usr_is_red_then_fixed_literal_normalization_is_green(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); namespace = self.fixture(root)
            (namespace / "usr").chmod(0o775)
            # The existing app-only normalization leaves the image's /usr untouched.
            subprocess.run(["sh", str(NORMALIZER), str(namespace / "rutorrent/app")], check=True)
            refusal = self.invoke(namespace, "check", "--require-all", "--bootstrap-root")
            self.assertEqual(refusal.returncode, 78, refusal.stdout + refusal.stderr)
            self.assertIn("refused /usr: directory grants", refusal.stderr)
            result = self.invoke(namespace, "normalize", "--require-all")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((namespace / "usr").stat().st_mode & 0o7777, 0o755)
            self.assertEqual(self.invoke(namespace, "check", "--require-all", "--bootstrap-root").returncode, 0)

    def test_only_fixed_inodes_are_normalized_and_marker_is_durable_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); namespace = self.fixture(root)
            unrelated = namespace / "rutorrent/app/ordinary.php"
            unrelated.write_text("unrelated\n"); unrelated.chmod(0o777)
            outside = root / "outside"; outside.write_text("outside\n"); outside.chmod(0o777)
            (namespace / "rutorrent/app/external").symlink_to(outside)
            for name in DIRECTORIES[:6]: (namespace / name).chmod(0o775)
            for name in (PHP, UPDATE, DISPATCH): (namespace / name).chmod(0o777)
            result = self.invoke(namespace, "normalize", "--require-all")
            self.assertEqual(result.returncode, 0, result.stderr)
            for name in DIRECTORIES[:6]: self.assertEqual((namespace / name).stat().st_mode & 0o7777, 0o755)
            for name, mode in ((PHP, 0o755), (UPDATE, 0o644), (DISPATCH, 0o644), (MARKER, 0o644)):
                self.assertEqual((namespace / name).stat().st_mode & 0o7777, mode)
            self.assertEqual((namespace / MARKER).read_text(), "version=1\n")
            self.assertEqual(outside.stat().st_mode & 0o7777, 0o777)
            self.assertEqual(unrelated.stat().st_mode & 0o7777, 0o777)
            before = self.snapshot(namespace)
            self.assertEqual(self.invoke(namespace, "normalize", "--require-all").returncode, 0)
            self.assertEqual(before, self.snapshot(namespace))

    def test_read_only_check_preserves_every_inode_owner_mode_and_byte(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); before = self.snapshot(namespace)
            result = self.invoke(namespace, "check", "--require-all", "--bootstrap-root")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(before, self.snapshot(namespace))

    def test_old_published_app_without_dispatch_has_no_new_required_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); (namespace / DISPATCH).unlink()
            self.assertEqual(self.invoke(namespace, "normalize").returncode, 0)
            self.assertFalse((namespace / MARKER).exists())
            self.assertEqual(self.invoke(namespace, "check").returncode, 0)
            self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 78)

    def test_absent_old_actor_pair_is_optional_but_final_require_all_refuses(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp))
            for name in (UPDATE, DISPATCH): (namespace / name).unlink()
            self.assertEqual(self.invoke(namespace, "normalize").returncode, 0)
            self.assertFalse((namespace / MARKER).exists())
            self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 78)

    def test_dispatch_without_update_refuses_before_first_mutation(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); (namespace / UPDATE).unlink()
            (namespace / "usr").chmod(0o775); before = self.snapshot(namespace)
            self.assertEqual(self.invoke(namespace, "normalize").returncode, 78)
            self.assertEqual(before, self.snapshot(namespace))

    def test_each_fixed_leaf_and_ancestor_symlink_is_refused_without_touching_target(self):
        for name in (PHP, UPDATE, DISPATCH, "usr", "usr/bin", "rutorrent", "rutorrent/app",
                     "rutorrent/app/plugins", "rutorrent/app/plugins/retrackers", "usr/local", "usr/local/share"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); namespace = self.fixture(root)
                target = namespace / name; target.rename(root / "held")
                target.symlink_to(root / "held", target_is_directory=(root / "held").is_dir())
                (namespace / "rutorrent/app/ordinary.php").write_text("unchanged\n") if (namespace / "rutorrent/app").is_dir() else None
                before = self.snapshot(namespace); held = self.snapshot(root / "held")
                result = self.invoke(namespace, "normalize", "--require-all")
                self.assertEqual(result.returncode, 78, result.stdout + result.stderr)
                self.assertEqual(before, self.snapshot(namespace))
                self.assertEqual(held, self.snapshot(root / "held"))

    def test_hardlinked_fixed_leaf_is_refused_without_any_mutation(self):
        for name in (PHP, UPDATE, DISPATCH):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); namespace = self.fixture(root)
                os.link(namespace / name, root / "alias")
                before = self.snapshot(namespace)
                self.assertEqual(self.invoke(namespace, "normalize", "--require-all").returncode, 78)
                self.assertEqual(before, self.snapshot(namespace))

    def test_fifo_leaf_refuses_without_blocking(self):
        for name in (PHP, UPDATE, DISPATCH):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                namespace = self.fixture(Path(temp)); (namespace / name).unlink(); os.mkfifo(namespace / name)
                self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 78)

    def test_setid_php_is_refused_even_in_build_normalize_mode(self):
        for mode in (0o4755, 0o2755):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                namespace = self.fixture(Path(temp)); (namespace / PHP).chmod(mode)
                self.assertEqual(self.invoke(namespace, "normalize", "--require-all").returncode, 78)

    def test_php_without_execute_permission_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); (namespace / PHP).chmod(0o644)
            self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 78)

    def test_current_identity_executable_php_0750_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); (namespace / PHP).chmod(0o750)
            self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 0)

    def test_unsafe_actor_write_access_is_refused_in_read_only_mode(self):
        for name in (UPDATE, DISPATCH):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                namespace = self.fixture(Path(temp)); (namespace / name).chmod(0o664)
                before = self.snapshot(namespace)
                self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 78)
                self.assertEqual(before, self.snapshot(namespace))

    def test_sticky_ancestor_retains_native_admission_semantics(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp)); (namespace / "rutorrent").chmod(0o1777)
            self.assertEqual(self.invoke(namespace, "check", "--require-all").returncode, 0)

    def test_invalid_existing_marker_refuses_before_first_mutation(self):
        for kind in ("wrong-body", "wrong-mode", "symlink", "hardlink", "fifo"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                root = Path(temp); namespace = self.fixture(root); marker = namespace / MARKER
                marker.write_text("version=1\n"); marker.chmod(0o644)
                if kind == "wrong-body": marker.write_text("version=2\n")
                if kind == "wrong-mode": marker.chmod(0o666)
                if kind == "symlink": marker.unlink(); marker.symlink_to(namespace / UPDATE)
                if kind == "hardlink": os.link(marker, root / "alias")
                if kind == "fifo": marker.unlink(); os.mkfifo(marker)
                (namespace / "usr").chmod(0o775); before = self.snapshot(namespace)
                self.assertEqual(self.invoke(namespace, "normalize", "--require-all").returncode, 78)
                self.assertEqual(before, self.snapshot(namespace))

    def test_unknown_paths_and_normalize_bootstrap_option_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            namespace = self.fixture(Path(temp))
            for arguments in (("normalize", "--bootstrap-root"), ("check", "/other.php"), ("other",), ("check", "--root", "/tmp")):
                self.assertEqual(self.invoke(namespace, *arguments).returncode, 64)

    def test_unsupported_effective_access_inspection_refuses_visibly(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); namespace = self.fixture(root)
            source = root / "unsupported.c"; program = root / "unsupported"
            before = "syscall(SYS_faccessat2, fd, \"\", X_OK, AT_EMPTY_PATH | AT_EACCESS)"
            text = SOURCE.read_text(); self.assertEqual(text.count(before), 1)
            source.write_text(text.replace(before, "(errno = ENOSYS, -1)"))
            subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-DNATIVE_ACTOR_TESTING", str(source), "-o", str(program)], check=True)
            result = self.invoke(namespace, "check", "--require-all", program=program)
            self.assertEqual(result.returncode, 78)
            self.assertIn("access inspection is unsupported", result.stderr)

    def test_production_source_has_no_runtime_configurable_root(self):
        text = SOURCE.read_text()
        production = text[:text.index("#ifdef NATIVE_ACTOR_TESTING")] + text[text.index("#endif", text.index("#ifdef NATIVE_ACTOR_TESTING")) + len("#endif"):]
        self.assertNotIn("getenv(", production)
        self.assertIn('const char *root = "/";', production)


if __name__ == "__main__":
    unittest.main()
