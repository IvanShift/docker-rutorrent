"""Keep image PHP package, executable and filesystem literals on one version."""

import os
import re
import shlex
from pathlib import Path

SCAN_ROOTS = ("rootfs", "scripts", "tests/smoke", "tests/shell", "tests/update-config")
HISTORICAL_FIXTURES = {
    "tests/update-config/php-cases.sh": "Old migration cases.",
    "tests/update-config/fixtures/all-aliases.input.rc": "Old aliases.",
    "tests/update-config/fixtures/php-config.input.php": "Old unrelated string.",
    "tests/update-config/fixtures/syntax-traps.input.rc": "Old samples/wrappers.",
    "tests/update-config/fixtures/syntax-traps.expected.rc": "Preserved samples.",
}
VERSIONED_PHP = re.compile(
    r"(?<![A-Za-z0-9_])php(?:-(?:fpm|cgi|dbg|config)|ize|dbg)?"
    r"(?P<version>[0-9]{2,})(?![A-Za-z0-9])",
    re.I,
)
PRIMARY_PACKAGE = re.compile(r"php(?P<version>[0-9]{2,})(?:[<>=~].+)?$")


def docker_instructions(text):
    pending = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        pending.append(stripped[:-1].rstrip() if continued else stripped)
        if not continued:
            yield " ".join(pending)
            pending = []
    if pending:
        raise ValueError("Unfinished Dockerfile instruction")


def runtime_php_version(text):
    """Read the bare phpNN package installed by apk add in the runtime stage."""
    runtime = False
    runtime_stages = 0
    versions = set()
    for instruction in docker_instructions(text):
        if re.match(r"FROM\s", instruction, re.I):
            runtime = re.search(r"\sAS\s+runtime\s*$", instruction, re.I) is not None
            runtime_stages += runtime
            continue
        if not runtime or not re.match(r"RUN\s", instruction, re.I):
            continue
        lexer = shlex.shlex(
            instruction[3:].strip(), posix=True, punctuation_chars=";&|"
        )
        lexer.whitespace_split = True
        words = list(lexer)
        for index, word in enumerate(words):
            if word != "apk":
                continue
            command = []
            for argument in words[index + 1 :]:
                if argument in {"&&", "||", ";", "&", "|"}:
                    break
                command.append(argument)
            if "add" not in command:
                continue
            for package in command[command.index("add") + 1 :]:
                match = PRIMARY_PACKAGE.fullmatch(package)
                if match:
                    versions.add(match["version"])
    if runtime_stages != 1 or len(versions) != 1:
        raise ValueError(
            "Require one runtime stage and one primary phpNN APK version; "
            f"runtime stages={runtime_stages}, primary versions={sorted(versions)}"
        )
    return versions.pop()


def php_runtime_drift(repository):
    """Return the inferred version and actionable mismatched literals."""
    repository = Path(repository)
    dockerfile = (repository / "Dockerfile").read_text(encoding="utf-8")
    expected = runtime_php_version(dockerfile)
    errors = []

    def inspect(where, content):
        for line_number, line in enumerate(content.splitlines(), 1):
            for match in VERSIONED_PHP.finditer(line):
                if match["version"] != expected:
                    errors.append(
                        f"{where}:{line_number}: {match[0]} disagrees "
                        f"with runtime APK php{expected}"
                    )

    inspect("Dockerfile", dockerfile)
    for relative_root in SCAN_ROOTS:
        root = repository / relative_root
        if not root.exists():
            continue
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(repository).as_posix()
            if relative in HISTORICAL_FIXTURES:
                continue
            inspect(relative + " [path]", relative)
            if path.is_symlink():
                inspect(relative + " [symlink]", os.readlink(path))
            elif path.is_file():
                inspect(relative, path.read_text(encoding="utf-8", errors="replace"))
    return expected, sorted(set(errors))
