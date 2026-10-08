"""Host PHP policy for CI unit tests that execute shipped PHP.

Locally a missing ``php``, or a missing extension that a test needs, skips
these tests. In CI (GitHub Actions sets both ``CI`` and ``GITHUB_ACTIONS``) it
fails them instead: a runner image without PHP or without the extension must
not turn the migration and startup-admission checks into silent skips.
test_host_php.py finds every test class that runs host php and requires this
decorator on it.
"""

import functools
import os
import shutil
import subprocess
import unittest

CI_VARIABLES = ("CI", "GITHUB_ACTIONS")


def ci_requires_host_php(environ=None):
    environ = os.environ if environ is None else environ
    return any(environ.get(name, "").lower() == "true" for name in CI_VARIABLES)


@functools.lru_cache(maxsize=None)
def loaded_php_extensions():
    """Lowercase names that `php -m` lists, or None when php does not answer."""
    try:
        result = subprocess.run(["php", "-m"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode:
        return None
    return frozenset(line.strip().lower() for line in result.stdout.splitlines() if line.strip())


def requires_host_php(purpose, extensions=()):
    """Class decorator: run with host PHP, skip locally without it, fail in CI.

    EXTENSIONS names the PHP extensions the class needs, as `php -m` spells them.
    """

    def decorate(cls):
        if not shutil.which("php"):
            problem = "no php on PATH"
        else:
            loaded = loaded_php_extensions()
            missing = [name for name in extensions if loaded is None or name.lower() not in loaded]
            if not missing:
                return cls
            problem = "php without the extension(s) " + ", ".join(missing)
        if not ci_requires_host_php():
            return unittest.skip(f"PHP is required for {purpose}: {problem}")(cls)

        def refuse(klass):
            raise AssertionError(f"CI requires host php for {purpose}; the runner has {problem}")

        cls.setUpClass = classmethod(refuse)
        return cls

    return decorate
