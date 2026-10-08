"""A CI runner without host PHP must fail the PHP-backed tests, not skip them.

The PHP-backed classes are found in the test sources, not listed here: every
class that runs host php must use requires_host_php, so a new one cannot
silently skip in CI.
"""

import ast
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from host_php import ci_requires_host_php

CI_DIR = Path(__file__).resolve().parent
# A floor for the discovery itself; the discovered set may only grow.
KNOWN = {("test_rc_shutdown", "ShutdownSessionMigrationTests"), ("test_startup_repair", "StartupRepairDiagnosticsTests"),
         ("test_smoke_clients", "ServiceRpcClientTests"), ("test_smoke_clients", "TorrentFixtureTests")}


RUNNERS = {"run", "Popen", "call", "check_call", "check_output", "system", "popen"}


def runs_host_php(node):
    """Whether NODE holds an argv, a command string or a PATH lookup of host php."""
    for child in ast.walk(node):
        if isinstance(child, (ast.List, ast.Tuple)) and child.elts:
            first = child.elts[0]
            if isinstance(first, ast.Constant) and first.value == "php":
                return True
        if isinstance(child, ast.Call) and child.args and isinstance(child.args[0], ast.Constant):
            name = getattr(child.func, "attr", getattr(child.func, "id", ""))
            argument = child.args[0].value
            if name == "which" and argument == "php":
                return True
            if name in RUNNERS and isinstance(argument, str) and argument.split()[:1] == ["php"]:
                return True
    return False


def decorator_names(cls):
    names = []
    for decorator in cls.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        names.append(getattr(target, "id", getattr(target, "attr", "")))
    return names


def php_backed():
    """{(module, class)} of test classes that run host php, and module-level uses outside a class."""
    classes, stray = set(), []
    for path in sorted(CI_DIR.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                if runs_host_php(node):
                    classes.add((path.stem, node.name))
            elif runs_host_php(node):
                stray.append(f"{path.name}:{node.lineno}")
    return classes, stray


class HostPhpPolicyTests(unittest.TestCase):
    def run_without_php(self, modules, **variables):
        with tempfile.TemporaryDirectory(prefix="no-php-") as empty:
            # An empty PATH directory hides php; the interpreter path is absolute.
            return subprocess.run([sys.executable, "-m", "unittest", *modules],
                                  cwd=CI_DIR, capture_output=True, text=True, timeout=60,
                                  env={"PATH": empty, **variables})

    def test_every_class_that_runs_php_uses_the_policy(self):
        classes, stray = php_backed()
        self.assertLessEqual(KNOWN, classes)
        self.assertEqual(stray, [], "run host php inside a requires_host_php test class")
        for module, name in sorted(classes):
            tree = ast.parse((CI_DIR / f"{module}.py").read_text(encoding="utf-8"))
            cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
            with self.subTest(test=f"{module}.{name}"):
                self.assertIn("requires_host_php", decorator_names(cls))
                self.assertFalse({"skip", "skipIf", "skipUnless"} & set(decorator_names(cls)))

    def test_ci_without_php_fails_every_php_backed_class(self):
        classes, _ = php_backed()
        modules = sorted({module for module, _ in classes})
        for variables in ({"CI": "true"}, {"GITHUB_ACTIONS": "true"}):
            with self.subTest(variables=variables):
                result = self.run_without_php(modules, **variables)
                self.assertNotEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr.count("AssertionError: CI requires host php for "), len(classes),
                                 result.stderr)
                self.assertNotIn("skipped", result.stderr)

    def test_local_run_without_php_still_skips(self):
        classes, _ = php_backed()
        result = self.run_without_php(sorted({module for module, _ in classes}))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("OK (skipped=", result.stderr)

    def test_missing_extension_fails_in_ci_and_skips_locally(self):
        with tempfile.TemporaryDirectory(prefix="php-extensions-") as temporary:
            work = Path(temporary)
            php = work / "php"
            php.write_text("#!/bin/sh\nprintf '[PHP Modules]\\nCore\\nposix\\n'\n")
            php.chmod(0o755)
            (work / "test_probe.py").write_text(textwrap.dedent(f"""\
                import sys, unittest
                sys.path.insert(0, {str(CI_DIR)!r})
                from host_php import requires_host_php

                @requires_host_php("the probe", extensions=("POSIX", "SimpleXML"))
                class ProbeTests(unittest.TestCase):
                    def test_nothing(self):
                        pass
                """))
            for variables, fails, message in (({"CI": "true"}, True, "the runner has php without the extension(s) SimpleXML"),
                                              ({}, False, "skipped 'PHP is required for the probe: php without")):
                with self.subTest(variables=variables):
                    result = subprocess.run([sys.executable, "-m", "unittest", "-v", "test_probe"], cwd=work,
                                            capture_output=True, text=True, timeout=60,
                                            env={"PATH": f"{work}{os.pathsep}/usr/bin:/bin", **variables})
                    # A class that fails in setUpClass exits 5 on Python 3.13 and 1 on 3.14 (measured).
                    self.assertEqual(result.returncode != 0, fails, result.stderr)
                    self.assertIn(message, result.stderr)

    def test_only_true_ci_markers_require_php(self):
        self.assertTrue(ci_requires_host_php({"GITHUB_ACTIONS": "true"}))
        self.assertTrue(ci_requires_host_php({"CI": "TRUE"}))
        self.assertFalse(ci_requires_host_php({"CI": "false"}))
        self.assertFalse(ci_requires_host_php({}))


if __name__ == "__main__":
    unittest.main()
