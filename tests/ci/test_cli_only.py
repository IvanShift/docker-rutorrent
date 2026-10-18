"""Exact HTTP doors for the shipped CLI actors, discovered from real PHP tokens."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
GENERATOR = ROOT / "rootfs/etc/nginx/generate-cli-only.php"


@requires_host_php("CLI worker HTTP policy", extensions=("tokenizer",))
class CliOnlyPolicy(unittest.TestCase):
    def test_move_actor_and_upstream_entry_are_denied_without_denying_http_actions(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "php").mkdir()
            plugins = root / "plugins/autotools"
            plugins.mkdir(parents=True)
            for name in ("move_worker.php", "upstream_move.php", "token.php", "recover.php"):
                (plugins / name).write_text("<?php if (PHP_SAPI !== 'cli') exit; $hash = $argv[1];")
            (plugins / "action.php").write_text("<?php // $argv is only documentation\n$text = '$argv';")
            output = root / "cli-only.conf"
            result = subprocess.run(["php", str(GENERATOR), str(root), str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = {f"location = /plugins/autotools/{name} {{ deny all; }}"
                        for name in ("move_worker.php", "upstream_move.php", "token.php", "recover.php")}
            self.assertEqual(set(output.read_text().splitlines()[1:]), expected)

    def test_generated_policy_is_sorted_exact_and_skips_links_and_other_extensions(self):
        # The image has no static copy: the build stops when generation fails.
        self.assertFalse((ROOT / "rootfs/etc/nginx/cli-only.conf").exists())
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "php").mkdir()
            (root / "php/initplugins.php").write_text("<?php $count = count($argv);")
            plugins = root / "plugins/autotools"
            plugins.mkdir(parents=True)
            (plugins / "move_worker.php").write_text("<?php $hash = $argv[1];")
            (plugins / "check.php").write_text("<?php /* $argv */ echo <<<'TEXT'\n$argv\nTEXT;\n")
            (plugins / "move_tx.php").write_text("<?php $argument = $_REQUEST['argv'] ?? '';")
            (plugins / "worker.inc").write_text("<?php $hash = $argv[1];")
            (plugins / "linked.php").symlink_to(plugins / "move_worker.php")
            outputs = [root / "first.conf", root / "second.conf"]
            for output in outputs:
                result = subprocess.run(["php", str(GENERATOR), str(root), str(output)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            lines = outputs[0].read_text().splitlines()
            self.assertTrue(lines[0].startswith("# "))
            self.assertEqual(lines[1:], ["location = /php/initplugins.php { deny all; }",
                                         "location = /plugins/autotools/move_worker.php { deny all; }"])
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())

    def test_generation_refuses_incomplete_source_and_unsafe_worker_paths(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            (root / "php").mkdir()
            output = root / "cli-only.conf"
            result = subprocess.run(["php", str(GENERATOR), str(root), str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Missing ruTorrent directory: plugins", result.stderr)
            self.assertFalse(output.exists())
            (root / "plugins").mkdir()
            (root / "plugins/bad name.php").write_text("<?php $argv;")
            result = subprocess.run(["php", str(GENERATOR), str(root), str(output)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            # Where an uncaught exception is printed depends on the host php.ini.
            self.assertIn("Unsafe CLI worker path: /plugins/bad name.php", result.stdout + result.stderr)
            self.assertFalse(output.exists())
            result = subprocess.run(["php", str(GENERATOR), str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 64)

    def test_readme_apply_existing_and_reset_commands_name_image_tools(self):
        readme = (ROOT / "README.md").read_text()
        for tool, suffixes in (("retrackers-apply-existing.php", ("", " --apply --limit=50")),
                               ("autotools-move-reset.php", ("", " <HASH>", " <HASH> --apply"))):
            for suffix in suffixes:
                self.assertIn(f"docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/{tool}{suffix}\n", readme)


if __name__ == "__main__":
    unittest.main()
