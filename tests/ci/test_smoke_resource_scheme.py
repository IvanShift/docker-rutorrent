"""One owner-label scheme and one cleanup path for every smoke container.

lib.sh starts shell-harness containers only through smoke_run and smoke_create,
which label them with this run's token under io.ivanshift.smoke.owner;
smoke_harness.py gives the Python harnesses the same launcher (run_argv and
start_container). Cleanup removes a container only when that label proves
ownership. Shell harnesses are lexed (shell_scan.py) and Python harnesses
parsed (ast), so a variable, a line continuation, a global docker option, a
wrapper, a substitution or a comment neither hides nor satisfies a rule.
"""

import ast
import re
import unittest
from pathlib import Path

from shell_scan import ShellSyntaxError, command_start, commands, function_commands
from test_shell_scan import run_by_sh

ROOT = Path(__file__).resolve().parents[2]
SMOKE = ROOT / "tests/smoke"
# Host entry points of the migration suite start containers the same way.
UPDATE_CONFIG = ROOT / "tests/update-config"
SCHEME = "io.ivanshift.smoke.owner"
# Label keys are dotted; scenario names such as "foreign-owner" are not.
OWNER_KEY = re.compile(r"\b[a-z0-9_-]+(?:\.[a-z0-9_-]+)+[.-]owner\b")
# docker global options that take a separate value.
VALUE_OPTIONS = {"--context", "-c", "--host", "-H", "--config", "--log-level", "-l",
                 "--tlscacert", "--tlscert", "--tlskey"}
REMOVAL_HELPERS = {"smoke_remove_run_containers", "smoke_remove_container", "smoke_remove_owned"}
# The one direct start outside lib.sh, by exact statement: a foreground --rm
# fixture cleanup that creates no container outliving the command.
EXCEPTIONS = {
    "tests/smoke/startup-regressions.sh": [
        ["docker", "run", "--rm", "--pull=never", "--network", "none", "--entrypoint", "/bin/sh",
         "--mount", "type=bind,src=$scratch,dst=/fixture", "$image", "-ec", "rm -rf /fixture/*"]],
}
# Where the shared Python helpers may start or remove containers, as (function, rule).
HELPER_SITES = {
    "smoke_harness.py": {("docker", "dynamic"), ("run_argv", "start"), ("start_container", "dynamic"),
                         ("remove_owned_container", "remove")},
}


def harness_files():
    return sorted(path for root in (SMOKE, UPDATE_CONFIG) for path in root.rglob("*")
                  if path.is_file() and path.suffix in (".sh", ".py") and "__pycache__" not in path.parts)


def relative(path):
    return path.relative_to(ROOT).as_posix()


def classify(argv):
    """The rule a docker argument list touches: start, remove, dynamic or None.

    Computed arguments are None (Python) or start with '$' (shell).
    """
    position = 0
    while position < len(argv) and argv[position] is not None and argv[position].startswith("-"):
        option = argv[position]
        position += 1
        if "=" not in option and option in VALUE_OPTIONS:
            position += 1
    argv = argv[position:]
    if not argv:
        return None
    command = argv[0]
    if command == "container" and len(argv) > 1:
        command = argv[1]
    elif command == "system" and argv[1:2] == ["prune"]:
        return "remove"
    if command is None or command.startswith("$"):
        return "dynamic"
    if command in ("run", "create"):
        return "start"
    if command in ("rm", "prune"):
        return "remove"
    return None


def shell_findings(source):
    """(rule, words) for every container start, removal, indirection or label in shell source."""
    findings = []
    for words in commands(source):
        for index, word in enumerate(words):
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=(?:\S*/)?docker", word) or re.search(r"\$\{[^}]*docker", word):
                findings.append(("indirect", words))
            if word == "docker" or word.endswith("/docker"):
                rule = classify(words[index + 1:])
                if rule is not None:
                    findings.append((rule, words[index:]))
            if word == "--label" or word.startswith("--label="):
                label = word.partition("=")[2] if word.startswith("--label=") else (words[index + 1:index + 2] or [""])[0]
                if label.partition("=")[0] not in (SCHEME, "$SMOKE_OWNER_LABEL"):
                    findings.append(("label", words))
    return findings


def allowed_shell(path, rule, words):
    if words in EXCEPTIONS.get(relative(path), []):
        return True
    if path.name != "lib.sh":
        return False
    if rule == "start":
        # The launchers: the owner label and --pull=never in the same argv.
        labels = [words[index + 1] for index, word in enumerate(words[:-1]) if word == "--label"]
        return labels == ["$SMOKE_OWNER_LABEL=$smoke_owner"] and "--pull=never" in words
    # Removal only of the ID whose owner label was just checked.
    return rule == "remove" and words[-1] == "$owned_id"


def python_findings(path):
    """(function, rule, line) for every container start, removal, indirection or label."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    findings = []

    def function(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    def text(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    def is_docker(node):
        return (isinstance(node, ast.Name) and node.id == "docker") or (isinstance(node, ast.Attribute) and node.attr == "docker")

    def owner_label(node):
        first = node.values[0] if isinstance(node, ast.JoinedStr) and node.values else None
        return isinstance(first, ast.FormattedValue) and (
            (isinstance(first.value, ast.Name) and first.value.id == "OWNER_LABEL")
            or (isinstance(first.value, ast.Attribute) and first.value.attr == "OWNER_LABEL"))

    def argv(nodes, where):
        # A starred or computed argument is None: dynamic where it decides the subcommand.
        rule = classify([text(node) for node in nodes])
        if rule is not None:
            findings.append((function(where), rule, where.lineno))

    for node in ast.walk(tree):
        parent = parents.get(node)
        if isinstance(node, ast.ImportFrom) and any(alias.asname for alias in node.names):
            findings.append((function(node), "indirect", node.lineno))
        elif isinstance(node, ast.Call) and is_docker(node.func):
            argv(node.args, node)
        elif isinstance(node, (ast.List, ast.Tuple)) and node.elts and text(node.elts[0]) == "docker":
            argv(node.elts[1:], node)
        elif isinstance(node, ast.Constant) and node.value == "docker":
            if not (isinstance(parent, (ast.List, ast.Tuple)) and parent.elts[0] is node):
                findings.append((function(node), "indirect", node.lineno))
        elif is_docker(node) and not (isinstance(parent, ast.Call) and parent.func is node):
            findings.append((function(node), "indirect", node.lineno))
        elif isinstance(node, ast.keyword) and node.arg == "shell":
            findings.append((function(node), "shell", parent.lineno))
        elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "os"
              and node.attr in ("system", "popen")):
            findings.append((function(node), "shell", node.lineno))
        if isinstance(node, (ast.List, ast.Tuple, ast.Call)):
            items = node.elts if isinstance(node, (ast.List, ast.Tuple)) else node.args
            for index, item in enumerate(items[:-1]):
                if text(item) == "--label" and not owner_label(items[index + 1]):
                    findings.append((function(node), "label", node.lineno))
    return findings


class ShellFindingTests(unittest.TestCase):
    """shell_findings() must report what sh would start or remove.

    The lexer's own cases are in test_shell_scan.py.
    """

    def test_bypass_spellings_are_classified(self):
        for source in ('d=docker; $d rm -f "$c"\n', 'docker --context default rm -f "$c"\n',
                       'docker ps -aq | xargs -r docker rm -f\n', 'if docker run -d x; then :; fi\n',
                       'timeout 5 docker run -d x\n', 'docker container rm x\n', 'f() { docker "$@"; }\n',
                       'smoke_run --label owner=me x\n'):
            with self.subTest(source=source):
                self.assertTrue(shell_findings(source))

    def test_starts_hidden_in_shell_grammar_are_found_or_refused(self):
        # sh runs each of these (docker is a recording stub); the guard must
        # report the start or refuse the source.
        for source in ("x=$(case a in a)docker run -d hidden;; esac)\n",
                       "timeout --signal KILL 5 sh -c 'docker run -d hidden'\n",
                       "sh <<'EOF'\ndocker run -d hidden\nEOF\n",
                       "sh 0\\\n<<'EOF'\ndocker run -d hidden\nEOF\n",
                       "echo 'docker run -d hidden' | sh\n",
                       "x=hidden; sh -c \"docker run -d $x\"\n",
                       "x='docker run -d hidden'; eval \"$x\"\n",
                       "touch 'docker run -d hidden'; eval d*\n",
                       "touch 'docker run -d hidden'; sh -c d*\n"):
            with self.subTest(source=source):
                self.assertEqual(run_by_sh(source), ["docker run -d hidden"])
                try:
                    findings = shell_findings(source)
                except ShellSyntaxError:
                    continue
                self.assertIn(("start", ["docker", "run", "-d", "hidden"]), findings)


class SmokeResourceSchemeTests(unittest.TestCase):
    def test_migration_host_entry_points_are_covered(self):
        names = {relative(path) for path in harness_files()}
        for entry in ("tests/update-config/single-file-mounts.sh", "tests/update-config/signals.sh"):
            self.assertIn(entry, names)

    def test_every_harness_is_lexed_or_parsed(self):
        for path in harness_files():
            with self.subTest(path=relative(path)):
                if path.suffix == ".sh":
                    list(commands(path.read_text(encoding="utf-8")))
                else:
                    ast.parse(path.read_text(encoding="utf-8"))

    def test_every_owner_label_uses_the_one_scheme(self):
        keys = {}
        for path in harness_files():
            for key in OWNER_KEY.findall(path.read_text(encoding="utf-8")):
                keys.setdefault(key, []).append(relative(path))
        self.assertIn(SCHEME, keys)
        self.assertEqual({key: files for key, files in keys.items() if key != SCHEME}, {})
        for path in harness_files():
            with self.subTest(path=relative(path)):
                if path.suffix == ".sh":
                    self.assertEqual([words for rule, words in shell_findings(path.read_text(encoding="utf-8"))
                                      if rule == "label"], [])
                else:
                    self.assertEqual([line for _, rule, line in python_findings(path) if rule == "label"], [])

    def test_only_the_shared_helpers_start_or_remove_containers(self):
        for path in harness_files():
            with self.subTest(path=relative(path)):
                if path.suffix == ".sh":
                    refused = [(rule, " ".join(words)) for rule, words in shell_findings(path.read_text(encoding="utf-8"))
                               if rule != "label" and not allowed_shell(path, rule, words)]
                    self.assertEqual(refused, [])
                else:
                    findings = [finding for finding in python_findings(path) if finding[1] != "label"]
                    self.assertEqual({(where, rule) for where, rule, _ in findings},
                                     HELPER_SITES.get(path.name, set()), findings)

    def test_shared_helpers_label_and_check_what_they_start_and_remove(self):
        lib = SMOKE / "lib.sh"
        rules = sorted(rule for rule, words in shell_findings(lib.read_text(encoding="utf-8"))
                       if allowed_shell(lib, rule, words))
        self.assertEqual(rules, ["remove", "remove", "start", "start"])
        harness = (SMOKE / "smoke_harness.py").read_text(encoding="utf-8")
        for line in ("return ['docker', 'run', '--pull=never', '--label', f'{OWNER_LABEL}={owner}', *args]",
                     "docker('rm', '-f', '-v', owned_id)", "if info['Id'] != owned_id or info['Config']['Labels'].get(OWNER_LABEL) != owner:"):
            self.assertTrue(line in harness, f"smoke_harness.py must keep: {line}")

    def test_detached_containers_have_owner_checked_cleanup(self):
        # A detached start needs a cleanup function that calls an owner-checked
        # removal helper, installed for EXIT; a comment naming one is not enough.
        for path in harness_files():
            if path.suffix != ".sh" or path.name == "lib.sh":
                continue
            source = path.read_text(encoding="utf-8")
            detached = False
            for words in commands(source):
                start = command_start(words)
                name, arguments = (words[start:start + 1] or [""])[0], words[start + 1:]
                if name == "smoke_create" or (name in ("smoke_run", "smoke_run_with_libexec") and any(
                        word == "--detach" or re.fullmatch(r"-[A-Za-z]*d[A-Za-z]*", word) for word in arguments)):
                    detached = True
            if not detached:
                continue
            with self.subTest(path=relative(path)):
                removals = [words for words in function_commands(source, "cleanup")
                            if words[command_start(words):][:1] and words[command_start(words)] in REMOVAL_HELPERS]
                self.assertTrue(removals, "cleanup() must call an owner-checked removal helper")
                installed = [words for words in commands(source)
                             if words == ["smoke_install_traps"] or (words[:2] == ["trap", "cleanup"] and "EXIT" in words)]
                self.assertTrue(installed, "cleanup() must be installed for EXIT")


if __name__ == "__main__":
    unittest.main()
