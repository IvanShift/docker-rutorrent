"""Freeze the CI wiring that GitHub actually executes for the tested image bytes.

The workflows are parsed (workflow_yaml.py) and their run scripts lexed
(shell_scan.py), not searched as text. A gate command must be a whole
unconditional command at the top level of its step, so a commented, skipped,
masked or failure-tolerant gate fails these contracts instead of matching a
substring.
"""

import ast
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shell_scan import commands, simple_command, tokens, top_level_lists
from workflow_yaml import WorkflowYamlError, load

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from verify_publish_revision import verify_revision  # noqa: E402

WORKFLOWS = ROOT / ".github/workflows"
PINNED_RUNNERS = {"ubuntu-24.04", "ubuntu-24.04-arm"}
VERIFY_REVISION = 'python3 scripts/verify_publish_revision.py "$REVISION" --repo "$REPOSITORY"'
UPDATE_CONFIG = ('docker run --pull=never --rm --entrypoint /bin/sh --mount '
                 '"type=bind,src=${PWD}/tests/update-config,dst=/tests/update-config,readonly" ')
# (job, step, required if:, commands that must run as whole top-level lines)
GATES = (
    ("prepare", "Run CI unit tests", None, ["python3 -m unittest discover -s tests/ci -p 'test_*.py'"]),
    ("prepare", "Require successful checks for the pinned ruTorrent commit", None,
     ['python3 scripts/verify_rutorrent_checks.py "$RUTORRENT_COMMIT" --repo "$RUTORRENT_REPO_SLUG"']),
    ("validate", "Require native architecture and check shell entry points", None, ["tests/shell/run.sh"]),
    ("validate", "Import and record the exact candidate for testing", None, [
        'python3 scripts/ci_image_artifact.py record "$archive" "$RUNNER_TEMP/candidate/metadata.json" '
        '"$FLAVOR" "$ARCH" "$REVISION"',
        'docker image save "$CANDIDATE_IMAGE" | python3 scripts/ci_image_artifact.py verify-import "$archive" '
        '"$RUNNER_TEMP/candidate/metadata.json" "$FLAVOR" "$ARCH" "$REVISION" --image "$CANDIDATE_IMAGE"']),
    ("validate", "Run update-config checks on every candidate", None, [
        UPDATE_CONFIG + '"$CANDIDATE_IMAGE" /tests/update-config/run.sh',
        'tests/update-config/single-file-mounts.sh "$CANDIDATE_IMAGE"']),
    ("validate", "Run migration entry-point signal regressions", "matrix.flavor == 'default' && matrix.arch == 'amd64'",
     ['tests/update-config/signals.sh "$CANDIDATE_IMAGE"']),
    ("validate", "Run full default startup and native smoke tests", "matrix.flavor == 'default'",
     ['tests/smoke/default.sh "$CANDIDATE_IMAGE"']),
    ("validate", "Run startup, FPM rename and retracker metadata regressions", "matrix.flavor == 'default'", [
        'tests/smoke/startup-regressions.sh "$CANDIDATE_IMAGE"', 'tests/smoke/stop-untied.sh "$CANDIDATE_IMAGE"',
        'tests/smoke/log-rotation.sh "$CANDIDATE_IMAGE"', 'tests/smoke/erasedata-fpm-rename.sh "$CANDIDATE_IMAGE"',
        'tests/smoke/retrackers-metadata.sh "$CANDIDATE_IMAGE" native']),
    # The browser-free scope still checks every X-Frame-Options value; the full
    # browser scope needs Chrome and stays a local acceptance gate.
    ("validate", "Run offline repair, runtime contract, frame-header and unauthenticated watchdog regressions",
     "matrix.flavor == 'default'", [
         'tests/smoke/upgrade-permissions.sh "$CANDIDATE_IMAGE"', 'tests/smoke/runtime-contracts.sh "$CANDIDATE_IMAGE"',
         'python3 tests/smoke/http-runtime.py "$CANDIDATE_IMAGE" --scope watchdog']),
    ("validate", "Run FileBot startup and native smoke tests", "matrix.flavor == 'filebot'", [
        'tests/smoke/filebot.sh "$CANDIDATE_IMAGE"', 'tests/smoke/pidfd.sh "$CANDIDATE_IMAGE" "linux/$ARCH"',
        'tests/smoke/erasedata-rename.sh "$CANDIDATE_IMAGE" "linux/$ARCH"']),
    # The legacy image shares the FPM pools; runtime-contracts pins them there too.
    ("legacy", "Run legacy migration and real daemon smoke tests", None, [
        UPDATE_CONFIG + "rutorrent-ci:legacy-098 /tests/update-config/legacy-098.sh",
        "tests/smoke/legacy-098.sh rutorrent-ci:legacy-098", "tests/smoke/stop-untied.sh rutorrent-ci:legacy-098",
        "tests/smoke/retrackers-metadata.sh rutorrent-ci:legacy-098 legacy",
        "tests/smoke/runtime-contracts.sh rutorrent-ci:legacy-098"]),
)
# Variables that replace commands or shell options for every later command.
SHELL_ENVIRONMENT = {"PATH", "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "PYTHONPATH", "PYTHONSTARTUP", "LD_PRELOAD"}
CONSTANTS = {":", "true", "false", "/bin/true", "/usr/bin/true"}
# Reserved words after which '!' is the condition of an if or a loop.
CONDITIONS = {"if", "elif", "while", "until"}
VERIFY_WORDS = shlex.split(VERIFY_REVISION)
# Publish steps run with stub programs; these record registry writes.
REGISTRY_WRITERS = ("skopeo", "docker")
DIGEST = "sha256:" + "d" * 64
STEP_VALUES = {"REVISION": "a" * 40, "REPOSITORY": "owner/repository", "GITHUB_TOKEN": "token",
               "RTORRENT_LABEL": "v0.16.25", "TAG_DATE": "20261018", "RUN_ID": "1", "RUN_ATTEMPT": "1"}


def workflow(name="build.yml"):
    return load((WORKFLOWS / name).read_text(encoding="utf-8"))


def workflow_files():
    files = sorted(path for path in WORKFLOWS.iterdir() if path.suffix in (".yml", ".yaml"))
    assert files, "no workflow files found"
    return files


def gate(command):
    """The one unconditional list that a gate command string is."""
    lists = top_level_lists(command)
    assert len(lists) == 1, command
    return lists[0]


def switches_off_failure(name, arguments):
    """Whether set or shopt ARGUMENTS turn off errexit or pipefail, or turn on noexec."""
    if name == "shopt":
        letters = "".join(word[1:] for word in arguments if word.startswith("-"))
        names = {word for word in arguments if not word.startswith("-")}
        return "o" in letters and bool(("u" in letters and names & {"errexit", "pipefail"})
                                       or ("s" in letters and "noexec" in names))
    index = 0
    while index < len(arguments):
        word = arguments[index]
        if word in ("-", "--") or len(word) < 2 or word[0] not in "-+":
            return False
        index += 1
        for letter in word[1:]:
            if letter == "o":
                option = arguments[index] if index < len(arguments) else None
                index += 1
                if (word[0], option) in (("+", "errexit"), ("+", "pipefail"), ("-", "noexec")):
                    return True
            elif (word[0], letter) in (("+", "e"), ("-", "n")):
                return True
    return False


def masking(script):
    """(reason, words) for each command that could let a failing or skipped command pass.

    simple_command() finds the command behind reserved words, assignments
    and wrappers such as command, builtin or env, as it does for the scanner.
    """
    found = []
    for words in commands(script or ""):
        command = simple_command(words)
        reserved = ("",) + command.reserved
        if any(word == "!" and before not in CONDITIONS for before, word in zip(reserved, reserved[1:])):
            # Outside an if or loop condition, ! turns a failure into success.
            found.append(("an inverted status", words))
        name, arguments = command.name or "", command.arguments
        if "exec" in command.wrappers:
            found.append(("the shell builtin 'exec'", words))
        elif name in CONSTANTS:
            found.append((f"the constant command {name!r}", words))
        elif name == "exit" and arguments in ([], ["0"]):
            found.append(("an early successful exit", words))
        elif name in ("return", "trap"):
            found.append((f"the shell builtin {name!r}", words))
        elif name in ("set", "shopt") and switches_off_failure(name, arguments):
            found.append(("errexit or pipefail switched off", words))
    return found


def workflow_violations(document, name):
    """(where, problem) for each key or construct that lets a failing or skipped gate pass.

    GitHub runs every step with bash -e unless a shell or default is set.
    """
    found = []
    scopes = [(name, document)]
    for job_name, job in document["jobs"].items():
        scopes.append((f"{name}/{job_name}", job))
        scopes += [(f"{name}/{job_name}/{step.get('name') or step.get('uses')}", step) for step in job.get("steps") or []]
    for where, scope in scopes:
        for key in ("defaults", "continue-on-error", "shell"):
            if key in scope:
                found.append((where, key))
        changed = SHELL_ENVIRONMENT & set(scope.get("env") or {})
        if changed:
            found.append((where, f"environment {sorted(changed)}"))
        script = scope.get("run") or ""
        found += [(where, f"{reason}: {' '.join(words)}") for reason, words in masking(script)]
        # These files change the environment of every later step.
        if re.search(r"GITHUB_(?:ENV|PATH)\b", script):
            found.append((where, "GITHUB_ENV or GITHUB_PATH"))
        if any(token[:2] == ("op", "|") for token in tokens(script)) and next(commands(script)) != ["set", "-euo", "pipefail"]:
            found.append((where, "a pipeline without pipefail"))
    return found


def run_step(script, verifier_status, workspace, environment):
    """(exit status, calls) of a publish step script run as GitHub runs it (bash -e).

    Stub programs come first in PATH: the revision verifier exits with
    verifier_status, skopeo and docker only record their arguments, and
    every other python3 call is the real interpreter.
    """
    stubs, trace = workspace / "bin", workspace / "trace"
    stubs.mkdir(exist_ok=True)
    trace.write_text("")
    programs = {
        "python3": ('if [ "$1" = scripts/verify_publish_revision.py ]; then\n'
                    '  printf "verify %s\\n" "$*" >> "$TRACE"\n  exit "$VERIFIER_STATUS"\nfi\n'
                    f'exec {shlex.quote(sys.executable)} "$@"\n'),
        "skopeo": ('printf "skopeo %s\\n" "$*" >> "$TRACE"\n'
                   'while [ "$#" -gt 0 ]; do\n  if [ "$1" = --digestfile ]; then printf "%s" "$DIGEST" > "$2"; fi\n'
                   '  shift\ndone\n'),
        "docker": 'printf "docker %s\\n" "$*" >> "$TRACE"\n',
    }
    for program, body in programs.items():
        (stubs / program).write_text("#!/bin/sh\n" + body)
        (stubs / program).chmod(0o755)
    for flavor in ("default", "filebot"):
        for arch in ("amd64", "arm64"):
            candidate = workspace / "candidates" / f"candidate-{flavor}-{arch}"
            candidate.mkdir(parents=True, exist_ok=True)
            (candidate / "metadata.json").write_text('{"manifest_digest": "%s"}' % DIGEST)
            (candidate / "pushed-digest").write_text(DIGEST)
    (workspace / "step.sh").write_text(script)
    variables = {"PATH": f"{stubs}{os.pathsep}{os.environ.get('PATH', os.defpath)}", "HOME": str(workspace),
                 "RUNNER_TEMP": str(workspace), "TRACE": str(trace), "VERIFIER_STATUS": str(verifier_status),
                 "DIGEST": DIGEST, "DOCKER_HOST": "unix:///nonexistent/docker.sock", **environment}
    result = subprocess.run(["bash", "--noprofile", "--norc", "-e", str(workspace / "step.sh")], cwd=workspace,
                            env=variables, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    return result.returncode, trace.read_text().splitlines()


def publication_commands(script):
    """Normalized publish commands whose operation we can classify.

    The shared parser unwraps execution prefixes but retains query-only
    commands. Docker/buildx global options could move the operation past
    the position checked below; refuse that unused syntax instead of
    guessing option arities with a separate Docker CLI parser.
    """
    for words in commands(script or ""):
        command = simple_command(words)
        if command.name == "docker":
            arguments = command.arguments
            if arguments[:1] == ["buildx"]:
                arguments = arguments[1:]
            if arguments and arguments[0].startswith("-"):
                raise AssertionError(f"Docker global options are not supported in publication: {words!r}")
        yield command


def registry_write_steps(steps):
    """Every publish step invoking a registry tool, with its normalized commands."""
    for step in steps:
        executed = list(publication_commands(step.get("run")))
        if any(command.name in REGISTRY_WRITERS for command in executed):
            yield step, executed


def matrix_combinations(matrix):
    """Expand a strategy matrix with GitHub's include and exclude rules."""
    keys = [key for key in matrix if key not in ("include", "exclude")]
    combinations = [{}] if keys else []
    for key in keys:
        if not isinstance(matrix[key], list):
            raise AssertionError(f"matrix.{key} must be a literal list")
        combinations = [dict(combination, **{key: value}) for combination in combinations for value in matrix[key]]
    for excluded in matrix.get("exclude") or []:
        combinations = [combination for combination in combinations
                        if not all(combination.get(key) == value for key, value in excluded.items())]
    added = []
    for included in matrix.get("include") or []:
        matched = False
        for combination in combinations:
            if all(combination[key] == value for key, value in included.items() if key in keys):
                combination.update({key: value for key, value in included.items() if key not in keys})
                matched = True
        if not matched:
            added.append(dict(included))
    return combinations + added


TOKEN = re.compile(r"\s*(?:('[^']*')|(==|!=|&&|\|\||[!(),])|([A-Za-z_][A-Za-z0-9_.-]*))")


def evaluate(expression, context):
    """Evaluate the GitHub expression subset the workflow uses.

    Literals, context properties, ==, != (GitHub ignores case when it compares
    strings), !, && and || (returning an operand), parentheses, format() and
    success() (context["success"], default true).
    """
    tokens = []
    position = 0
    while position < len(expression.rstrip()):
        match = TOKEN.match(expression, position)
        if match is None:
            raise ValueError(f"Unsupported expression syntax: {expression[position:]!r}")
        tokens.append(match[1] or match[2] or match[3])
        position = match.end()

    def take(expected=None):
        token = tokens.pop(0)
        if expected is not None and token != expected:
            raise ValueError(f"Expected {expected!r}, got {token!r}")
        return token

    def primary():
        token = take()
        if token == "(":
            value = either()
            take(")")
            return value
        if token == "!":
            return not primary()
        if token.startswith("'"):
            return token[1:-1]
        if token == "success":
            take("(")
            take(")")
            return context.get("success", True)
        if token == "format":
            take("(")
            arguments = [either()]
            while tokens[0] == ",":
                take(",")
                arguments.append(either())
            take(")")
            template, *values = arguments
            for index, value in enumerate(values):
                template = template.replace("{%d}" % index, str(value))
            return template
        value = context
        for part in token.split("."):
            value = value[part]
        return value

    def same(left, right):
        if isinstance(left, str) and isinstance(right, str):
            return left.casefold() == right.casefold()
        return left == right

    def comparison():
        left = primary()
        while tokens and tokens[0] in ("==", "!="):
            operator = take()
            right = primary()
            left = same(left, right) if operator == "==" else not same(left, right)
        return left

    def both():
        left = comparison()
        while tokens and tokens[0] == "&&":
            take()
            right = comparison()
            left = right if left else left
        return left

    def either():
        left = both()
        while tokens and tokens[0] == "||":
            take()
            right = both()
            left = left if left else right
        return left

    value = either()
    if tokens:
        raise ValueError(f"Unparsed expression tokens: {tokens}")
    return value


def interpolate(template, context):
    return re.sub(r"\$\{\{(.*?)\}\}", lambda match: str(evaluate(match[1], context)), template)


def context(event, ref="refs/heads/master", sha="b" * 40, **extra):
    return {"github": {"workflow": "build", "event_name": event, "ref": ref, "sha": sha}, **extra}


def publication(sha, state):
    """The publish job's registry protocol, pausing after each guarded phase."""
    def check():
        verify_revision(sha, {"ref": "refs/heads/master", "object": {"type": "commit", "sha": state["master"]}})

    check()  # before the staging uploads
    state["staging"] = sha
    yield
    check()  # just before the release tags move
    yield
    state["latest"] = state["filebot"] = sha


class PublishGroups:
    """GitHub's documented job concurrency, applied to the publish job's own keys.

    A job whose group is held waits as pending. queue: single (the default)
    keeps one pending job per group and cancels it for the next arrival;
    queue: max keeps up to 100 pending jobs in the order they started waiting.
    """

    def __init__(self, concurrency, state):
        self.concurrency, self.state = concurrency, state
        self.running, self.pending, self.outcome = {}, {}, {}

    def arrive(self, name, event, sha, pause_after=None):
        group = interpolate(self.concurrency["group"], context(event, sha=sha)).casefold()
        job = (name, publication(sha, self.state))
        if group not in self.running:
            self.running[group] = job
            self._run(group, pause_after)
        elif self.concurrency.get("queue", "single") == "max":
            assert len(self.pending.setdefault(group, [])) < 100, "a full queue cancels the arrival"
            self.pending[group].append(job)
        else:
            for displaced, _ in self.pending.get(group, []):
                self.outcome[displaced] = "cancelled"
            self.pending[group] = [job]

    def resume(self, name):
        self._run(next(group for group, (running, _) in self.running.items() if running == name))

    def _run(self, group, pause_after=None):
        name, steps = self.running[group]
        try:
            for phase, _ in enumerate(steps, 1):
                if phase == pause_after:
                    return
            self.outcome[name] = "published"
        except ValueError:
            self.outcome[name] = "refused"
        del self.running[group]
        if self.pending.get(group):
            self.running[group] = self.pending[group].pop(0)
            self._run(group)


def interleaved_publications(concurrency):
    """Run A passes both master checks and stalls before its tag writes; master
    moves to B, and a re-run of an older commit and a schedule run arrive."""
    older, first, second = "0" * 40, "a" * 40, "b" * 40
    state = {"master": first}
    groups = PublishGroups(concurrency, state)
    groups.arrive("A", "push", first, pause_after=2)
    state["master"] = second
    groups.arrive("B", "push", second)
    groups.arrive("re-run", "push", older)
    groups.arrive("schedule", "schedule", second)
    groups.resume("A")
    return groups, state


class ShellAnalysisTests(unittest.TestCase):
    """The helpers above must see what bash would execute."""

    def test_masking_constructs_are_recognised(self):
        for script in ("cmd || true", "cmd ||true", "cmd || :", ": cmd", "true cmd", "exit", "exit 0", "x=1 exit 0",
                       "cmd; exit", "set +e", "set -u +e", "set +o errexit", "exec true", "trap 'exit 0' EXIT",
                       "return 0", "false && cmd", "cmd || /bin/true", "! cmd", "if true; then cmd; fi",
                       'x="$(cmd || true)"',
                       # The same constructs behind the wrappers simple_command() knows.
                       "command set +e", "builtin set +e", "command -p set +e", "x=1 command set +e",
                       "eval 'set +e'", "command exit", "builtin exit 0", "command exec cmd", "nohup true",
                       "command trap 'exit 0' EXIT", "if x; then set +e; fi", "if x; then ! cmd; fi",
                       "set +o pipefail", "set -n", "set -o noexec", "shopt -u -o errexit", "shopt -uo pipefail"):
            with self.subTest(script=script):
                self.assertTrue(masking(script + "\n"))
        for script in ("exit 1", "echo 'exit 0'", "set -euo pipefail", "test -n \"$x\" || exit 1",
                       "case $a in x) ;; *) echo no >&2; exit 1;; esac", "echo done", "[ ! -x f ] || fail x",
                       "if ! cmd; then exit 1; fi", "until ready; do sleep 1; done",
                       "set -- +e", "set -o errexit", "command -v set", "echo set +e", "shopt -s -o errexit",
                       "while ! ready; do sleep 1; done"):
            with self.subTest(script=script):
                self.assertEqual(masking(script + "\n"), [])

    def test_masking_agrees_with_bash_on_errexit_switches(self):
        # After each spelling a failing verifier runs in a pipeline. The step
        # passes in bash exactly when masking() reports the spelling.
        probe = ('set -euo pipefail\n{}\n'
                 'python3 scripts/verify_publish_revision.py "$REVISION" --repo "$REPOSITORY" | cat\nskopeo copy a b\n')
        cases = [(spelling, True) for spelling in (
            "set +e", "set -u +e", "set +o errexit", "set +o pipefail", "set -n", "set -o noexec", "command set +e",
            "command -p set +e", "builtin set +e", "command builtin set +e", "x=1 command set +e", "eval 'set +e'",
            "eval set +o pipefail", "if true; then set +e; fi", "shopt -u -o errexit", "shopt -uo pipefail",
            "exec true", "trap 'exit 0' EXIT")]
        cases += [(spelling, False) for spelling in (
            "set -e", "set -o errexit", "set -- +e", "command -v set", "echo set +e", "shopt -s -o errexit",
            "set -x")]
        with tempfile.TemporaryDirectory(prefix="masking-") as temporary:
            for spelling, masks in cases:
                script = probe.format(spelling)
                with self.subTest(spelling=spelling):
                    status, _ = run_step(script, 1, Path(temporary), STEP_VALUES)
                    self.assertEqual(status == 0, masks, "the case no longer models bash")
                    self.assertEqual(bool(masking(script)), masks)

    def test_matrix_expansion_follows_include_rules(self):
        matrix = {"os": ["a", "b"], "include": [{"os": "a", "runner": "r1"}, {"os": "c", "runner": "r3"}],
                  "exclude": [{"os": "b"}]}
        self.assertEqual(matrix_combinations(matrix), [{"os": "a", "runner": "r1"}, {"os": "c", "runner": "r3"}])


class WorkflowYamlTests(unittest.TestCase):
    """Expected values are what YAML (PyYAML 6.0.3) reads; the subset refuses what it does not decode."""

    def test_flow_collections_parse_or_are_refused(self):
        self.assertEqual(load("a: []\nb: [ \"master\" ]\nc: [x, 'y', [1, 2], {k: v, m: [n]}, ~]\n"),
                         {"a": [], "b": ["master"], "c": ["x", "y", ["1", "2"], {"k": "v", "m": ["n"]}, None]})
        # A trailing comma means [x] to YAML; empty entries are not YAML at all.
        for text in ("a: [x,]\n", "a: [x, , y]\n", "a: [, x]\n", "a: {k: v,}\n", "a: [&x]\n"):
            with self.subTest(text=text), self.assertRaises(WorkflowYamlError):
                load(text)

    def test_double_quoted_escapes_decode_or_are_refused(self):
        self.assertEqual(load('a: "q\\"b\\\\s\\/n\\nt\\t"\nb: ["x\\", y"]\n'),
                         {"a": 'q"b\\s/n\nt\t', "b": ['x", y']})
        # YAML reads "P\u0041TH" as PATH; keeping the escape as text would
        # hide the key from the environment contract, so it is refused.
        for escape in ("\\u0041", "\\x41", "\\U00000041", "\\N", "\\_", "\\L", "\\P", "\\0", "\\a", "\\b",
                       "\\e", "\\f", "\\r", "\\v", "\\ ", "\\'"):
            for text in (f'a: "P{escape}TH"\n', f'"P{escape}TH": x\n', f'a: ["P{escape}TH"]\n',
                         f'a: {{"P{escape}TH": x}}\n', f'env:\n  "BASH{escape}ENV": x\n'):
                with self.subTest(text=text), self.assertRaises(WorkflowYamlError):
                    load(text)

    def test_keys_are_compared_after_decoding(self):
        self.assertEqual(load('env:\n  "PATH": a\n  \'BASH_ENV\': b\n'), {"env": {"PATH": "a", "BASH_ENV": "b"}})
        for text in ('env:\n  PATH: a\n  "PATH": b\n', "env:\n  BASH_ENV: a\n  'BASH_ENV': b\n",
                     'env: {PATH: a, "PATH": b}\n', 'env:\n  "P\\/TH": a\n  "P/TH": b\n',
                     "env:\n  'it''s': a\n  \"it's\": b\n"):
            with self.subTest(text=text), self.assertRaises(WorkflowYamlError):
                load(text)

    def test_quotes_open_only_where_a_scalar_starts(self):
        # Inside a plain scalar a quote is a character, so ' #' starts a comment.
        self.assertEqual(load("run: echo ' # gate'\n"), {"run": "echo '"})
        self.assertEqual(load("a: [it's, x]\nb: \"x # y\" # c\nc: '#'\n"), {"a": ["it's", "x"], "b": "x # y", "c": "#"})
        self.assertEqual(load("- 'a, b'\n- key: \"v: w\"\n"), ["a, b", {"key": "v: w"}])
        for text in ('a: "x" y\n', "a: 'x' y\n", 'a: "x\n', "a: 'it's'\n", "a: echo a: b\n", "a: b:\n",
                     '"a" x: b\n', "[a, b]: c\n"):
            with self.subTest(text=text), self.assertRaises(WorkflowYamlError):
                load(text)


class ExpressionReferenceTests(unittest.TestCase):
    """Reference cases for the evaluator, so it follows GitHub's semantics."""

    def test_reference_cases(self):
        cases = (
            ("'pull_request' == 'PULL_REQUEST'", {}, True),
            ("'a' != 'A'", {}, False),
            ("github.event_name == 'Push'", context("push"), True),
            ("'' || 'fallback'", {}, "fallback"),
            ("'value' && 'second'", {}, "second"),
            ("'' && 'second'", {}, ""),
            ("!('x' == 'y')", {}, True),
            ("format('-{0}-{1}', 'a', github.sha)", context("push", sha="c" * 40), "-a-" + "c" * 40),
            ("success() && github.event_name == 'schedule'", context("schedule", success=False), False),
        )
        for expression, values, expected in cases:
            with self.subTest(expression=expression):
                self.assertEqual(evaluate(expression, values), expected)


class PublishGroupsReferenceTests(unittest.TestCase):
    """The publication model must expose both ways a newer master is lost."""

    def test_reference_cases(self):
        per_commit = {"group": "publish-rutorrent-${{ github.sha }}", "cancel-in-progress": "false"}
        shared = {"group": "publish-rutorrent", "cancel-in-progress": "false"}
        for name, concurrency, outcome, tags in (
                # Groups per commit: B publishes beside A, then A moves the tags back.
                ("per-commit", per_commit, {"B": "published", "re-run": "refused", "schedule": "published",
                                            "A": "published"}, "a" * 40),
                # One pending slot: the re-run displaces B, then is itself displaced.
                ("queue single", shared, {"B": "cancelled", "re-run": "cancelled", "A": "published",
                                          "schedule": "published"}, "b" * 40),
                ("queue max", dict(shared, queue="max"), {"A": "published", "B": "published", "re-run": "refused",
                                                          "schedule": "published"}, "b" * 40)):
            with self.subTest(name):
                groups, state = interleaved_publications(concurrency)
                self.assertEqual(groups.outcome, outcome)
                self.assertEqual((state["latest"], state["filebot"]), (tags, tags))
        # Without a later arrival a displaced push of master is lost for good.
        state = {"master": "a" * 40}
        groups = PublishGroups(shared, state)
        groups.arrive("A", "push", "a" * 40, pause_after=2)
        state["master"] = "b" * 40
        groups.arrive("B", "push", "b" * 40)
        groups.arrive("re-run", "push", "0" * 40)
        groups.resume("A")
        self.assertEqual(groups.outcome, {"B": "cancelled", "A": "published", "re-run": "refused"})
        self.assertEqual(state["latest"], "a" * 40)


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.document = workflow()
        self.jobs = self.document["jobs"]

    def test_no_job_or_step_tolerates_failure_or_changes_the_shell(self):
        # GitHub runs every step with bash -e unless a shell or default is set;
        # these keys and constructs would let a failing or skipped gate pass.
        for path in workflow_files():
            with self.subTest(workflow=path.name):
                self.assertEqual(workflow_violations(load(path.read_text(encoding="utf-8")), path.name), [])

    def test_contract_refuses_edits_that_let_a_gate_pass(self):
        # In-memory edits of this workflow: each must be a violation or be
        # refused by the YAML subset.
        text = (WORKFLOWS / "build.yml").read_text(encoding="utf-8")
        self.assertIn("\nenv:\n", text)
        for name, key in (("quoted PATH", '"PATH"'), ("quoted BASH_ENV", "'BASH_ENV'"), ("plain ENV", "ENV")):
            with self.subTest(name):
                mutated = load(text.replace("\nenv:\n", f"\nenv:\n  {key}: /tmp/stubs\n", 1))
                self.assertTrue(workflow_violations(mutated, "build.yml"))
        for name, key in (("escaped PATH", '"P\\u0041TH"'), ("escaped BASH_ENV", '"BASH\\x5fENV"'),
                          ("duplicate IMAGE_NAME", '"IMAGE_NAME"')):
            with self.subTest(name), self.assertRaises(WorkflowYamlError):
                load(text.replace("\nenv:\n", f"\nenv:\n  {key}: /tmp/stubs\n", 1))
        for wrapper in ("command set +e", "builtin set +e", "env set +e", "command -p set +o pipefail"):
            with self.subTest(wrapper):
                document = load(text)
                for step in document["jobs"]["publish"]["steps"]:
                    if "run" in step and step["run"].startswith("set -euo pipefail\n"):
                        step["run"] = step["run"].replace("set -euo pipefail\n", f"set -euo pipefail\n{wrapper}\n", 1)
                self.assertTrue(workflow_violations(document, "build.yml"))

    def test_a_failing_revision_check_stops_every_registry_write(self):
        # Every publish step that writes to the registry runs as GitHub runs
        # it, with stub programs. A failing verifier must stop it before any
        # write; a passing one must reach the writes, so the stubs are not
        # what stops them. A wrapped "set +e" would reach a write, and the
        # contract (masking) refuses it.
        environment = {"IMAGE_NAME": self.document["env"]["IMAGE_NAME"]}
        writers = list(registry_write_steps(self.jobs["publish"]["steps"]))
        self.assertEqual({command.name for _, executed in writers for command in executed
                          if command.name in REGISTRY_WRITERS}, set(REGISTRY_WRITERS))
        with tempfile.TemporaryDirectory(prefix="publication-") as temporary:
            for step, _ in writers:
                values = dict(environment, **{key: STEP_VALUES.get(key, "test") for key in step.get("env") or {}})
                for verifier, mutation in ((1, None), (0, None), (1, "command set +e"), (1, "builtin set +e")):
                    script = step["run"]
                    if mutation:
                        script = script.replace("set -euo pipefail\n", f"set -euo pipefail\n{mutation}\n", 1)
                    with self.subTest(step=step.get("name"), verifier=verifier, mutation=mutation):
                        status, calls = run_step(script, verifier, Path(temporary), values)
                        writes = [call for call in calls if call.split(" ", 1)[0] in REGISTRY_WRITERS]
                        self.assertEqual(calls[:1], ["verify scripts/verify_publish_revision.py {} --repo {}".format(
                            values["REVISION"], values["REPOSITORY"])])
                        if verifier == 0:
                            self.assertEqual(status, 0)
                            self.assertTrue(writes)
                        elif mutation is None:
                            self.assertNotEqual(status, 0)
                            self.assertEqual(writes, [])
                        else:
                            self.assertTrue(writes, "the harness must show the wrapped switch")
                            self.assertTrue(masking(script))

    def test_gate_steps_execute_every_required_command(self):
        gates = {(job, name): (condition, commands) for job, name, condition, commands in GATES}
        for job in ("prepare", "validate", "legacy"):
            self.assertNotIn("if", self.jobs[job], job)
        for job_name, job in self.jobs.items():
            for step in job["steps"]:
                with self.subTest(job=job_name, step=step.get("name")):
                    condition, required = gates.pop((job_name, step.get("name")), (None, []))
                    self.assertEqual(step.get("if"), condition)
                    executed = top_level_lists(step.get("run") or "")
                    for command in required:
                        self.assertIn(gate(command), executed)
        self.assertEqual(gates, {}, "every gate step must exist")

    def test_every_candidate_combination_is_built_and_tested(self):
        validate = self.jobs["validate"]
        self.assertEqual(validate["env"]["CANDIDATE_IMAGE"],
                         "ivanshift/rutorrent:validation-${{ matrix.flavor }}-${{ matrix.arch }}")
        combinations = matrix_combinations(validate["strategy"]["matrix"])
        self.assertEqual(sorted((item["flavor"], item["arch"], item["runner"]) for item in combinations),
                         [("default", "amd64", "ubuntu-24.04"), ("default", "arm64", "ubuntu-24.04-arm"),
                          ("filebot", "amd64", "ubuntu-24.04"), ("filebot", "arm64", "ubuntu-24.04-arm")])
        build = [step for step in validate["steps"] if str(step.get("uses", "")).startswith("docker/build-push-action@")]
        self.assertEqual(build[0]["with"]["tags"], "${{ env.CANDIDATE_IMAGE }}")
        for job_name, job in self.jobs.items():
            for step in job["steps"]:
                self.assertNotIn("CANDIDATE_IMAGE", step.get("env") or {}, f"{job_name}/{step.get('name')}")

    def test_default_candidates_pin_frame_headers_repair_and_watchdog(self):
        # The gate step runs the browser-free watchdog scope (GATES); that scope
        # must still check every X-Frame-Options value. The module-level frame
        # checks precede the first scope exit, which precedes the browser cases.
        harness = ast.parse((ROOT / "tests/smoke/http-runtime.py").read_text(encoding="utf-8"))

        def position(predicate, what):
            found = [index for index, node in enumerate(harness.body) if predicate(node)]
            self.assertTrue(found, f"http-runtime.py: {what}")
            return found[0]

        def scope_test(scope):
            return lambda node: (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                                 and ast.unparse(node.test) == f"options.scope == '{scope}'")

        frame = position(lambda node: any(isinstance(child, ast.Constant) and isinstance(child.value, str)
                                          and child.value.startswith("application DENY and frame-ancestors preserved")
                                          for child in ast.walk(node)), "application frame-header check")
        self.assertLess(frame, position(scope_test("nginx"), "nginx scope exit"))
        self.assertLess(position(scope_test("nginx"), "nginx scope exit"), position(scope_test("all"), "browser scope"))

    def test_publication_requires_native_and_legacy_validation(self):
        publish = self.jobs["publish"]
        self.assertEqual(publish["needs"], ["prepare", "validate", "legacy"])
        for event, ref, success, expected in (("schedule", "refs/heads/master", True, True),
                                              ("push", "refs/heads/master", True, True),
                                              ("push", "refs/heads/master", False, False),
                                              ("push", "refs/heads/other", True, False),
                                              ("pull_request", "refs/pull/7/merge", True, False),
                                              ("workflow_dispatch", "refs/heads/master", True, False)):
            with self.subTest(event=event, ref=ref, success=success):
                self.assertIs(bool(evaluate(publish["if"], context(event, ref, success=success))), expected)

    def test_build_inputs_preserve_source_pins_and_refresh_runtime_packages(self):
        for job in ("validate", "legacy"):
            build = [step for step in self.jobs[job]["steps"] if str(step.get("uses", "")).startswith("docker/build-push-action@")]
            self.assertEqual(len(build), 1, job)
            self.assertEqual(build[0]["with"]["no-cache-filters"], "runtime")
            self.assertNotRegex(build[0]["with"]["build-args"], r"(?m)^\s*RUTORRENT_(REF|COMMIT_SHA)=")

    def test_exact_tested_archive_is_uploaded_without_a_publish_rebuild(self):
        validate, publish = self.jobs["validate"]["steps"], self.jobs["publish"]["steps"]
        build = next(step for step in validate if str(step.get("uses", "")).startswith("docker/build-push-action@"))
        self.assertTrue(build["with"]["outputs"].startswith("type=oci,dest="))
        self.assertTrue(any(str(step.get("uses", "")).startswith("actions/upload-artifact@") for step in validate))
        self.assertFalse([step for step in validate if "{{.Id}}" in (step.get("run") or "")])
        self.assertFalse([step for step in publish if str(step.get("uses", "")).startswith("docker/build-push-action@")])
        for step in publish:
            for command in publication_commands(step.get("run")):
                self.assertFalse(command.name == "docker" and (command.arguments[:1] == ["build"]
                                 or command.arguments[:2] == ["buildx", "build"]), command.words)
        names = [step.get("name") for step in publish]
        verify = names.index("Verify all four retained candidates before registry writes")
        self.assertLess(verify, names.index("Login to Docker Hub"))
        self.assertIn(["python3", "scripts/ci_image_artifact.py", "verify", "$directory/image.tar", "$directory/metadata.json",
                       "$flavor", "$arch", "$REVISION"], list(commands(publish[verify]["run"])))

    def test_revision_guard_precedes_every_registry_write(self):
        # Staging uploads and release tags both wait for the current-master check.
        writers = list(registry_write_steps(self.jobs["publish"]["steps"]))
        self.assertTrue(writers, "publication must contain registry writes")
        for step, executed in writers:
            with self.subTest(step=step.get("name")):
                self.assertEqual(executed[0].words, ["set", "-euo", "pipefail"])
                self.assertIn(gate(VERIFY_REVISION), top_level_lists(step["run"]))
                writes = [index for index, command in enumerate(executed) if command.name in REGISTRY_WRITERS]
                self.assertLess([command.words for command in executed].index(VERIFY_WORDS), writes[0])
                self.assertEqual(step["env"]["REVISION"], "${{ github.sha }}")
                self.assertEqual(step["env"]["REPOSITORY"], "${{ github.repository }}")
                self.assertEqual(step["env"]["GITHUB_TOKEN"], "${{ github.token }}")

    def test_pending_push_is_never_displaced_by_another_run(self):
        # GitHub keeps one pending run per group and cancels it when another
        # arrives; group names compare without case. Only a pull request may
        # supersede its own older run.
        group = self.document["concurrency"]["group"]
        cancel = self.document["concurrency"]["cancel-in-progress"]
        older, newer, master = "a" * 40, "b" * 40, "refs/heads/master"

        def run_group(event, ref=master, sha=newer):
            return interpolate(group, context(event, ref, sha)).casefold()

        push = run_group("push")
        for event, ref, sha in (("schedule", master, newer), ("workflow_dispatch", master, newer),
                                ("push", master, older), ("pull_request", "refs/pull/7/merge", newer)):
            with self.subTest(event=event, sha=sha[:1]):
                self.assertNotEqual(run_group(event, ref, sha), push)
        pull = "refs/pull/7/merge"
        self.assertEqual(run_group("pull_request", pull, older), run_group("pull_request", pull, newer))
        self.assertNotEqual(run_group("pull_request", pull), run_group("pull_request", "refs/pull/8/merge"))
        for event in ("push", "schedule", "workflow_dispatch", "pull_request"):
            with self.subTest(cancel=event):
                self.assertEqual(interpolate(cancel, context(event)), str(event == "pull_request"))
        # Only publication declares a job group: one group for every commit and
        # event, so publishers run one at a time and their master checks run
        # inside it. queue: max keeps every pending publisher instead of the
        # default single one; it rules out cancel-in-progress, and publication
        # never cancels midway through tags. Another job group could displace.
        for path in workflow_files():
            declared = [name for name, job in load(path.read_text(encoding="utf-8"))["jobs"].items() if "concurrency" in job]
            self.assertEqual(declared, ["publish"] if path.name == "build.yml" else [], path.name)
        publication = self.jobs["publish"]["concurrency"]

        def publish_group(event, sha=newer):
            return interpolate(publication["group"], context(event, sha=sha)).casefold()

        self.assertEqual({publish_group(event, sha) for event in ("push", "schedule") for sha in (older, newer)},
                         {publish_group("push")})
        self.assertNotIn(publish_group("push"), {run_group(event) for event in ("push", "schedule")})
        self.assertEqual(publication, {"group": publication["group"], "queue": "max", "cancel-in-progress": "false"})

    def test_publishers_never_roll_back_or_displace_a_newer_master(self):
        groups, state = interleaved_publications(self.jobs["publish"]["concurrency"])
        self.assertEqual(groups.outcome, {"A": "published", "B": "published", "re-run": "refused",
                                          "schedule": "published"})
        master = state["master"]
        self.assertEqual((state["staging"], state["latest"], state["filebot"]), (master, master, master))
        # A publisher of a superseded commit that only starts now writes nothing.
        groups.arrive("late", "push", "a" * 40)
        self.assertEqual(groups.outcome["late"], "refused")
        self.assertEqual((state["staging"], state["latest"], state["filebot"]), (master, master, master))

    def test_every_job_in_every_workflow_runs_on_a_pinned_runner(self):
        # ubuntu-latest moves to a new release on GitHub's schedule; the image
        # tests and their toolchain are verified on 24.04.
        for path in workflow_files():
            for job_name, job in load(path.read_text(encoding="utf-8"))["jobs"].items():
                with self.subTest(workflow=path.name, job=job_name):
                    runner = job["runs-on"]
                    self.assertIsInstance(runner, str, "runs-on must be one label")
                    reference = re.fullmatch(r"\$\{\{\s*matrix\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}", runner)
                    if reference is None:
                        self.assertNotIn("${{", runner)
                        resolved = [runner]
                    else:
                        resolved = [item.get(reference[1]) for item in matrix_combinations(job["strategy"]["matrix"])]
                    self.assertTrue(resolved)
                    for label in resolved:
                        self.assertIn(label, PINNED_RUNNERS)

    def test_shell_suite_runs_with_errexit_and_fails_on_any_failing_check(self):
        runner_text = (ROOT / "tests/shell/run.sh").read_text(encoding="utf-8")
        self.assertEqual(next(commands(runner_text)), ["set", "-eu"])
        self.assertEqual(masking(runner_text), [])
        checks = sorted(path.name for path in (ROOT / "tests/shell").iterdir()
                        if path.suffix in (".sh", ".py") and path.name != "run.sh")
        self.assertTrue(checks)
        # Every check is replaced by a stub that records its run. run.sh must
        # run them all, and must exit non-zero when any one of them fails.
        with tempfile.TemporaryDirectory(prefix="shell-suite-") as temporary:
            tree = Path(temporary) / "tree"
            (tree / "tests/shell").mkdir(parents=True)
            (tree / "rootfs").mkdir()
            runner = tree / "tests/shell/run.sh"
            runner.write_text(runner_text, encoding="utf-8")
            runner.chmod(0o755)
            trace = Path(temporary) / "trace"
            for failing in [None] + checks:
                trace.write_text("")
                for name in checks:
                    stub = tree / "tests/shell" / name
                    status = 1 if name == failing else 0
                    if name.endswith(".py"):
                        stub.write_text(f"import sys\nwith open({str(trace)!r}, 'a') as trace:\n"
                                        f"    trace.write({name!r} + '\\n')\nsys.exit({status})\n")
                    else:
                        stub.write_text(f"#!/bin/sh\nprintf '%s\\n' {name} >> {shlex.quote(str(trace))}\nexit {status}\n")
                    stub.chmod(0o755)
                result = subprocess.run(["sh", str(runner)], capture_output=True, text=True, timeout=60)
                ran = trace.read_text().splitlines()
                with self.subTest(failing=failing):
                    if failing is None:
                        self.assertEqual(result.returncode, 0, result.stderr)
                        self.assertEqual(sorted(ran), checks)
                    else:
                        self.assertNotEqual(result.returncode, 0, f"run.sh passed although {failing} failed")
                        self.assertIn(failing, ran)

    def test_geolite_identity_is_retained_in_final_image(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        for field in ("version", "sha256"):
            self.assertIn(f'io.github.ivanshift.geolite.{field}="${{GEOIP2_DB_{field.upper()}}}"', dockerfile)


class PublicationRevisionTests(unittest.TestCase):
    def test_only_current_master_can_promote_tags(self):
        current = "a" * 40
        response = {"ref": "refs/heads/master", "object": {"type": "commit", "sha": current}}
        verify_revision(current, response)
        with self.assertRaisesRegex(ValueError, "no longer master"):
            verify_revision("b" * 40, response)

    def test_unproven_branch_or_revision_is_refused(self):
        for response in ({}, {"ref": "refs/heads/other"}, {"ref": "refs/heads/master", "object": {"type": "tag", "sha": "a" * 40}}):
            with self.subTest(response=response), self.assertRaises(ValueError):
                verify_revision("a" * 40, response)


class PublicationDiscoveryTests(unittest.TestCase):
    """Mutate the real workflow and run its publication contracts, not a second model."""

    checks = ("test_a_failing_revision_check_stops_every_registry_write",
              "test_revision_guard_precedes_every_registry_write")

    def with_extra_step(self, script, guarded=False):
        document = workflow()
        steps = document["jobs"]["publish"]["steps"]
        login = next(index for index, step in enumerate(steps) if step.get("name") == "Login to Docker Hub")
        # Deliberately unnamed: all writers need a guard, regardless of step names.
        extra = {"run": script, "env": {"REVISION": "${{ github.sha }}",
                 "REPOSITORY": "${{ github.repository }}", "GITHUB_TOKEN": "${{ github.token }}"}}
        if guarded:
            extra["run"] = "set -euo pipefail\n" + VERIFY_REVISION + "\n" + script
        steps.insert(login + 1, extra)
        return document

    def check(self, document, method):
        case = WorkflowContractTests(method)
        case.document, case.jobs = document, document["jobs"]
        # Invoke the actual assertion method. With no unittest run outcome,
        # subTest propagates its assertion to this mutation test.
        getattr(case, method)()

    def test_additional_wrapped_writer_requires_its_own_guard(self):
        for script in ("command docker buildx imagetools create --tag example.invalid/probe:latest " + DIGEST,
                       "WRITER_TEST=1 env MARKER=x skopeo copy oci-archive:image.tar docker://example.invalid/probe"):
            document = self.with_extra_step(script)
            for method in self.checks:
                with self.subTest(script=script, method=method), self.assertRaises(AssertionError):
                    self.check(document, method)

    def test_guarded_additional_writer_passes_without_a_known_name(self):
        for script in ("command docker buildx imagetools create --tag example.invalid/probe:latest " + DIGEST,
                       "env MARKER=x skopeo copy oci-archive:image.tar docker://example.invalid/probe"):
            document = self.with_extra_step(script, guarded=True)
            for method in self.checks:
                with self.subTest(script=script, method=method):
                    self.check(document, method)

    def test_wrapped_rebuild_in_an_additional_step_is_refused(self):
        for script in ("command docker build .", "env MARKER=x docker buildx build ."):
            with self.subTest(script=script), self.assertRaises(AssertionError):
                self.check(self.with_extra_step(script), "test_exact_tested_archive_is_uploaded_without_a_publish_rebuild")

    def test_docker_global_options_are_refused_in_publication(self):
        # Prefix options can move build past the position checked by the
        # no-rebuild contract. No current publish command needs these forms.
        for script in ("docker --debug build .", "command docker --debug build .",
                       "env MARKER=x docker buildx --builder default build ."):
            document = self.with_extra_step(script, guarded=True)
            for method in self.checks + ("test_exact_tested_archive_is_uploaded_without_a_publish_rebuild",):
                with self.subTest(script=script, method=method), self.assertRaisesRegex(AssertionError, "Docker global options"):
                    self.check(document, method)

    def test_command_queries_are_not_registry_writes(self):
        script = "command -v docker\nbuiltin command -V skopeo\n"
        with tempfile.TemporaryDirectory(prefix="publication-query-") as temporary:
            status, calls = run_step(script, 1, Path(temporary), STEP_VALUES)
            self.assertEqual(status, 0)
            self.assertEqual(calls, [])
        document = self.with_extra_step(script)
        for method in self.checks:
            with self.subTest(method=method):
                self.check(document, method)


if __name__ == "__main__":
    unittest.main()
