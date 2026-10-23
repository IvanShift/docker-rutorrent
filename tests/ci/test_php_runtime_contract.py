"""Guard PHP drift with real tree checks and isolated package/path mutations."""

import tempfile
import unittest
from pathlib import Path

from dockerfile_parser import run_scripts, stages
from php_runtime_contract import php_runtime_drift, runtime_php_version
from shell_scan import commands, simple_command

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = """FROM alpine AS builder
RUN apk add --no-cache build-base
FROM alpine AS runtime
RUN apk upgrade --no-cache \\
 && apk add --no-cache \\
 # Runtime PHP packages, not a commented historical package.
 php85 \\
 php85-fpm \\
 php85-pcntl
"""


class PhpRuntimeContractTests(unittest.TestCase):
    def test_cli_fpm_and_modules_require_the_critical_security_release(self):
        scripts = run_scripts(stages((REPO_ROOT / "Dockerfile").read_text())["runtime"])
        packages = set()
        for script in scripts:
            for words in commands(script):
                command = simple_command(words)
                if command.name == "apk" and "add" in command.arguments:
                    arguments = command.arguments[command.arguments.index("add") + 1:]
                    packages.update(word for word in arguments if word.startswith("php85"))
        required = {"php85", "php85-fpm", "php85-bcmath", "php85-ctype", "php85-curl",
                    "php85-dom", "php85-fileinfo", "php85-mbstring", "php85-openssl",
                    "php85-pcntl", "php85-phar", "php85-posix", "php85-session",
                    "php85-simplexml", "php85-tokenizer", "php85-xml", "php85-zip"}
        self.assertTrue({name + ">=8.5.12-r0" for name in required} <= packages,
                        "CLI, FPM and every installed extension need the PHP security floor")
        self.assertTrue(all(package.endswith(">=8.5.12-r0") for package in packages),
                        "Every PHP package in apk add must carry the quoted security floor")

    def tree(self, temp, dockerfile=DOCKERFILE):
        root = Path(temp)
        (root / "Dockerfile").write_text(dockerfile)
        return root

    def write(self, root, path, content):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return target

    def test_live_image_runtime_contract(self):
        expected, errors = php_runtime_drift(REPO_ROOT)
        self.assertEqual(errors, [], "\n".join(errors))
        self.assertTrue(expected.isdigit())

    def test_primary_apk_detection_ignores_comments_and_non_add_commands(self):
        text = "# RUN apk add php84\n" + DOCKERFILE.replace(
            "RUN apk upgrade --no-cache", "RUN echo php84 && apk upgrade --no-cache"
        ).replace("php85 \\", '"php85=8.5.4-r0" \\')
        self.assertEqual(runtime_php_version(text), "85")

    def test_missing_or_conflicting_primary_runtime_packages_fail_closed(self):
        mutations = {
            "no-primary": DOCKERFILE.replace(" php85 \\\n", ""),
            "conflict": DOCKERFILE.replace(" php85 \\\n", " php85 php84 \\\n"),
            "only-builder": DOCKERFILE.replace("AS runtime", "AS other"),
            "duplicate-runtime": DOCKERFILE
            + "FROM alpine AS runtime\nRUN apk add php85\n",
        }
        for name, text in mutations.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                runtime_php_version(text)

    def test_stale_php_module_package_is_reported(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-php-module-") as temp:
            root = self.tree(temp, DOCKERFILE.replace("php85-pcntl", "php84-pcntl"))
            expected, errors = php_runtime_drift(root)
            self.assertEqual(expected, "85")
            self.assertTrue(
                any("Dockerfile:" in error and "php84" in error for error in errors)
            )

    def test_stale_paths_commands_and_pid_references_are_reported(self):
        mutations = {
            "rootfs/etc/php84/conf.d/www.ini": "memory_limit=256M\n",
            "rootfs/etc/s6.d/php84/run": "exec /usr/sbin/php-fpm85\n",
            "rootfs/etc/php85/php-fpm.d/www.conf": "pid=/run/php/php84-fpm.pid\n",
            "rootfs/etc/nginx/fastcgi-runtime.conf": (
                "fastcgi_pass unix:/run/php/php84-fpm.sock;\n"
            ),
            "rootfs/usr/local/bin/startup": "php84 /helper.php\n",
            "rootfs/usr/local/lib/rutorrent/startup.php": "<?php $cli = 'php84';\n",
            "scripts/runtime-check.sh": "apk add php84-tokenizer\n",
            "tests/smoke/default.sh": "php84 -r 'echo 1;'\n",
            "tests/smoke/legacy-098.sh": "/usr/sbin/php-fpm84 -t\n",
            "tests/update-config/run.sh": "php_bin=php84\n",
            "tests/shell/plugin-schedules.sh": "sed 's|/usr/bin/php84|fixture|g'\n",
        }
        for path, content in mutations.items():
            with (
                self.subTest(path=path),
                tempfile.TemporaryDirectory(prefix="rem4-ci-php-drift-") as temp,
            ):
                root = self.tree(temp)
                self.write(root, path, content)
                expected, errors = php_runtime_drift(root)
                self.assertEqual(expected, "85")
                self.assertTrue(
                    any(path in error and "84" in error for error in errors), errors
                )

    def test_empty_versioned_directory_and_symlink_target_are_checked(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-php-path-") as temp:
            root = self.tree(temp)
            (root / "rootfs/etc/php84").mkdir(parents=True)
            link = root / "rootfs/php-current"
            link.symlink_to("/usr/bin/php84")
            _, errors = php_runtime_drift(root)
            self.assertTrue(any("rootfs/etc/php84 [path]" in error for error in errors))
            self.assertTrue(
                any("rootfs/php-current [symlink]" in error for error in errors)
            )

    def test_historic_inputs_are_excluded_but_legacy_runtime_stays_current(self):
        with tempfile.TemporaryDirectory(prefix="rem4-ci-php-history-") as temp:
            root = self.tree(temp)
            self.write(root, "tests/update-config/php-cases.sh", "old=/usr/bin/php74\n")
            self.write(
                root,
                "tests/update-config/fixtures/syntax-traps.expected.rc",
                "operator=/usr/bin/php84-wrapper\n",
            )
            self.write(
                root,
                "tests/update-config/fixtures/legacy-stock.expected.rc",
                "execute=/usr/bin/php85\n",
            )
            self.write(root, "tests/smoke/legacy-098.sh", "php85 /legacy.php\n")
            self.write(root, "tests/smoke/default.sh", "php85 /native.php\n")
            expected, errors = php_runtime_drift(root)
            self.assertEqual(expected, "85")
            self.assertEqual(errors, [])
            self.write(
                root,
                "tests/update-config/fixtures/legacy-stock.expected.rc",
                "execute=/usr/bin/php84\n",
            )
            _, errors = php_runtime_drift(root)
            self.assertTrue(
                any("legacy-stock.expected.rc" in error for error in errors)
            )


if __name__ == "__main__":
    unittest.main()
