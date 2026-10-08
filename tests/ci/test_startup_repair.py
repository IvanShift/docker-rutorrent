from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "rootfs/usr/local/lib/rutorrent/startup.php"


@unittest.skipUnless(shutil.which("php"), "PHP is required for startup admission")
class StartupRepairDiagnosticsTests(unittest.TestCase):
    def refuse(self, rule, mode):
        # Exercise shipped admission functions, without running root startup.
        source = HELPER.read_text().split("$uid = (int)getenv('UID');", 1)[0]
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "unsafe"
            if rule == "parent":
                path.mkdir()
            else:
                path.touch()
            path.chmod(mode)
            script = Path(scratch) / "probe.php"
            script.write_text(source + "\n$uid = posix_geteuid(); $gid = 991; attest($argv[1], 'fixture', $uid, $argv[2]);\n")
            return subprocess.run(["php", str(script), str(path), rule], text=True, capture_output=True)

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


if __name__ == "__main__":
    unittest.main()
