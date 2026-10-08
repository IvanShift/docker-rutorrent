"""Repair guidance named by the shipped startup admission refusals."""

from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "rootfs/usr/local/lib/rutorrent/startup.php"

# Load only the helper's top-level function declarations, found by the PHP
# tokenizer rather than by a literal source line, so its root-only body never
# runs here however the operational code is rearranged.
PROBE = r"""<?php
declare(strict_types=1);
[, $helper, $path, $rule, $functions] = $argv;
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
foreach (['attest', 'repairHint', 'refuse'] as $required) {
    if (!function_exists($required)) {
        fwrite(STDERR, "startup.php no longer declares $required() at top level\n");
        exit(2);
    }
}
$uid = posix_geteuid();
$gid = 991;
attest($path, 'fixture', $uid, $rule);
"""


@requires_host_php("startup admission", extensions=("tokenizer", "posix"))
class StartupRepairDiagnosticsTests(unittest.TestCase):
    def refuse(self, rule, mode):
        with tempfile.TemporaryDirectory() as scratch:
            path = Path(scratch) / "unsafe"
            if rule == "parent":
                path.mkdir()
            else:
                path.touch()
            path.chmod(mode)
            probe = Path(scratch) / "probe.php"
            probe.write_text(PROBE)
            return subprocess.run(
                ["php", str(probe), str(HELPER), str(path), rule, str(Path(scratch) / "functions.php")],
                text=True, capture_output=True)

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
