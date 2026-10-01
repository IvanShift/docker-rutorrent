"""Keep documented build/runtime defaults tied to the live Dockerfile contract."""

import re
import shlex
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from prepare_ci_inputs import (  # noqa: E402 - repository helper path setup
    dockerfile_defaults,
)


def table_rows(text, title):
    section = text.split(title, 1)[1].split("\n##", 1)[0]
    result = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        columns = [part.strip() for part in line.split("|")[1:-1]]
        names = re.findall(r"`([A-Z][A-Z0-9_]*)`", columns[0])
        default = columns[-1]
        if default == "_(empty)_":
            value = ""
        elif default.startswith("`") and default.endswith("`"):
            value = default[1:-1]
        else:
            continue
        for name in names:
            result[name] = value
    return result


def env_defaults(text):
    defaults = {}
    joined = re.sub(r"\\\n\s*", " ", text)
    for match in re.finditer(r"(?m)^ENV\s+(.+)$", joined):
        for word in shlex.split(match[1], comments=True):
            name, separator, value = word.partition("=")
            if separator:
                defaults[name] = value
    return defaults


class DocumentedDefaultsTests(unittest.TestCase):
    def test_readme_build_defaults_cover_and_equal_global_dockerfile_args(self):
        dockerfile = (REPO_ROOT / "Dockerfile").read_text()
        documented = table_rows(
            (REPO_ROOT / "README.md").read_text(), "### Build arguments"
        )
        current = dockerfile_defaults(dockerfile)
        global_args = dockerfile.split("\nFROM ", 1)[0]
        for name in re.findall(r"(?m)^ARG ([A-Z][A-Z0-9_]*)$", global_args):
            if name not in {"BUILDPLATFORM", "TARGETPLATFORM"}:
                current[name] = ""
        self.assertEqual(
            set(documented), set(current), "Document every global ARG default"
        )
        # Expand the documented expressions under the same ARG order as Docker does.
        ordered = "\n".join(f'ARG {name}="{documented[name]}"' for name in current)
        self.assertEqual(dockerfile_defaults(ordered), current)

    def test_readme_runtime_defaults_equal_dockerfile_env_and_startup_optout(self):
        readme = (REPO_ROOT / "README.md").read_text()
        documented = table_rows(readme, "### Environment variables")
        documented.update(
            table_rows(readme, "### Additional variables when FileBot enabled")
        )
        current = env_defaults(
            re.split(r"(?im)^FROM\s", (REPO_ROOT / "Dockerfile").read_text())[-1]
        )
        # CC/CXX/DIST_PATH belong to build stages, not the operator runtime interface.
        runtime = {
            name: value
            for name, value in current.items()
            if name not in {"CC", "CXX", "DIST_PATH"}
        }
        for name, value in runtime.items():
            self.assertIn(name, documented)
            self.assertEqual(documented[name], value, name)
        self.assertEqual(documented["ALLOW_UNAUTHENTICATED_HTTP"], "false")
        startup = (REPO_ROOT / "rootfs/usr/local/bin/startup").read_text()
        self.assertIn("ALLOW_UNAUTHENTICATED_HTTP:-false", startup)

    def test_readme_contains_no_stale_literal_source_commit(self):
        readme = (REPO_ROOT / "README.md").read_text()
        current = dockerfile_defaults((REPO_ROOT / "Dockerfile").read_text())
        valid = {
            value for value in current.values() if re.fullmatch(r"[0-9a-f]{40}", value)
        }
        examples = {
            "0123456789abcdef0123456789abcdef01234567",
            "89abcdef0123456789abcdef0123456789abcdef",
        }
        for sha in re.findall(r"(?<![0-9a-f])[0-9a-f]{40}(?![0-9a-f])", readme):
            self.assertIn(sha, valid | examples, f"Stale source SHA in README: {sha}")


if __name__ == "__main__":
    unittest.main()
