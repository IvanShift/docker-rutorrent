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

    def test_fallback_policy_tracks_move_entry_replacement(self):
        config = (ROOT / "rootfs/etc/nginx/cli-only.conf").read_text()
        for name in ("move_worker.php", "upstream_move.php", "token.php", "recover.php"):
            self.assertIn(f"location = /plugins/autotools/{name} {{ deny all; }}", config)
        for name in ("check.php", "move_tx.php"):
            self.assertNotIn(f"/plugins/autotools/{name}", config)

    def test_readme_apply_existing_and_reset_commands_name_image_tools(self):
        readme = (ROOT / "README.md").read_text()
        for tool, suffixes in (("retrackers-apply-existing.php", ("", " --apply --limit=50")),
                               ("autotools-move-reset.php", ("", " <HASH>", " <HASH> --apply"))):
            for suffix in suffixes:
                self.assertIn(f"docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/{tool}{suffix}\n", readme)


if __name__ == "__main__":
    unittest.main()
