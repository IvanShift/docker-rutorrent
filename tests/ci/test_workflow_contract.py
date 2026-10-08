"""Freeze the CI wiring that GitHub actually executes for the tested image bytes.

The workflows are parsed (workflow_yaml.py) and their run scripts lexed
(shell_scan.py), not searched as text. A gate command must be a whole
unconditional command at the top level of its step, so a commented, skipped,
masked or failure-tolerant gate fails these contracts instead of matching a
substring.
"""

import ast
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from shell_scan import ShellSyntaxError, commands, tokens, top_level_lists
from workflow_yaml import load

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
RESERVED = {"if", "then", "else", "elif", "do", "while", "until", "{"}
VERIFY_WORDS = shlex.split(VERIFY_REVISION)


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


def masking(script):
    """(reason, words) for each command that could let a failing or skipped command pass."""
    found = []
    for words in commands(script or ""):
        if words[0] == "!":
            # Outside an if or loop condition, ! turns a failure into success.
            found.append(("an inverted status", words))
        index = 0
        while index < len(words) and (words[index] in RESERVED or words[index] == "!"
                                      or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[index])):
            index += 1
        name, arguments = (words[index:index + 1] or [""])[0], words[index + 1:]
        if name in (":", "true", "false", "/bin/true", "/usr/bin/true"):
            found.append((f"the constant command {name!r}", words))
        elif name == "exit" and arguments in ([], ["0"]):
            found.append(("an early successful exit", words))
        elif name in ("return", "exec", "trap"):
            found.append((f"the shell builtin {name!r}", words))
        elif name == "set" and (any(re.fullmatch(r"\+[A-Za-z]*e[A-Za-z]*", word) for word in arguments)
                                or ("errexit" in arguments and "+o" in arguments)):
            found.append(("errexit switched off", words))
    return found


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


class ShellAnalysisTests(unittest.TestCase):
    """The helpers above must see what bash would execute."""

    def test_masking_constructs_are_recognised(self):
        for script in ("cmd || true", "cmd ||true", "cmd || :", ": cmd", "true cmd", "exit", "exit 0", "x=1 exit 0",
                       "cmd; exit", "set +e", "set -u +e", "set +o errexit", "exec true", "trap 'exit 0' EXIT",
                       "return 0", "false && cmd", "cmd || /bin/true", "! cmd", "if true; then cmd; fi",
                       'x="$(cmd || true)"'):
            with self.subTest(script=script):
                self.assertTrue(masking(script + "\n"))
        for script in ("exit 1", "echo 'exit 0'", "set -euo pipefail", "test -n \"$x\" || exit 1",
                       "case $a in x) ;; *) echo no >&2; exit 1;; esac", "echo done", "[ ! -x f ] || fail x",
                       "if ! cmd; then exit 1; fi", "until ready; do sleep 1; done"):
            with self.subTest(script=script):
                self.assertEqual(masking(script + "\n"), [])

    def test_only_unconditional_top_level_lists_are_executed(self):
        script = ("a \\\n  \"b\"  # comment\n# c\nif false; then\n  d\nfi\nf() {\n  e\n}\n"
                  "for x in y; do\n  g\ndone\ncase $x in z) h ;; esac\necho 'i # j'\nk &\n(m)\nn; o\n")
        self.assertEqual(top_level_lists(script), [("a", '"b"'), ("echo", "'i # j'"), ("n",), ("o",)])
        with self.assertRaises(ShellSyntaxError):
            top_level_lists('echo "\ncmd\n')

    def test_matrix_expansion_follows_include_rules(self):
        matrix = {"os": ["a", "b"], "include": [{"os": "a", "runner": "r1"}, {"os": "c", "runner": "r3"}],
                  "exclude": [{"os": "b"}]}
        self.assertEqual(matrix_combinations(matrix), [{"os": "a", "runner": "r1"}, {"os": "c", "runner": "r3"}])


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


class WorkflowContractTests(unittest.TestCase):
    def setUp(self):
        self.document = workflow()
        self.jobs = self.document["jobs"]

    def step(self, job, name):
        matches = [step for step in self.jobs[job]["steps"] if step.get("name") == name]
        self.assertEqual(len(matches), 1, f"{job}: exactly one step named {name!r}")
        return matches[0]

    def test_no_job_or_step_tolerates_failure_or_changes_the_shell(self):
        # GitHub runs every step with bash -e unless a shell or default is set;
        # these keys and constructs would let a failing or skipped gate pass.
        for path in workflow_files():
            document = load(path.read_text(encoding="utf-8"))
            self.assertNotIn("defaults", document, path.name)
            self.assertFalse(SHELL_ENVIRONMENT & set(document.get("env") or {}), path.name)
            for job_name, job in document["jobs"].items():
                self.assertNotIn("continue-on-error", job, f"{path.name}/{job_name}")
                self.assertNotIn("defaults", job, f"{path.name}/{job_name}")
                self.assertFalse(SHELL_ENVIRONMENT & set(job.get("env") or {}), f"{path.name}/{job_name}")
                for step in job.get("steps") or []:
                    where = f"{path.name}/{job_name}/{step.get('name') or step.get('uses')}"
                    self.assertNotIn("continue-on-error", step, where)
                    self.assertNotIn("shell", step, where)
                    self.assertFalse(SHELL_ENVIRONMENT & set(step.get("env") or {}), where)
                    script = step.get("run") or ""
                    self.assertEqual(masking(script), [], where)
                    # These files change the environment of every later step.
                    self.assertNotRegex(script, r"GITHUB_(?:ENV|PATH)\b", where)
                    if any(token[:2] == ("op", "|") for token in tokens(script)):
                        self.assertEqual(next(commands(script)), ["set", "-euo", "pipefail"],
                                         f"{where}: a pipeline needs pipefail")

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
        self.assertFalse([words for step in publish for words in commands(step.get("run") or "")
                          if words[:2] == ["docker", "build"] or words[:3] == ["docker", "buildx", "build"]])
        names = [step.get("name") for step in publish]
        verify = names.index("Verify all four retained candidates before registry writes")
        self.assertLess(verify, names.index("Login to Docker Hub"))
        self.assertIn(["python3", "scripts/ci_image_artifact.py", "verify", "$directory/image.tar", "$directory/metadata.json",
                       "$flavor", "$arch", "$REVISION"], list(commands(publish[verify]["run"])))

    def test_revision_guard_precedes_every_registry_write(self):
        # Staging uploads and release tags both wait for the current-master check.
        for name, write in (("Upload candidate manifests without changing their digests", "skopeo"),
                            ("Promote the tested platform digests to release tags", "docker")):
            with self.subTest(step=name):
                step = self.step("publish", name)
                executed = list(commands(step["run"]))
                self.assertEqual(executed[0], ["set", "-euo", "pipefail"])
                self.assertIn(gate(VERIFY_REVISION), top_level_lists(step["run"]))
                writes = [index for index, words in enumerate(executed) if words[:1] == [write]]
                self.assertTrue(writes, f"{name}: no {write} command")
                self.assertLess(executed.index(VERIFY_WORDS), writes[0])
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
        # Only publication declares a job group: one commit at a time, never
        # across commits, never cancelled. Another job group could displace.
        for path in workflow_files():
            declared = [name for name, job in load(path.read_text(encoding="utf-8"))["jobs"].items() if "concurrency" in job]
            self.assertEqual(declared, ["publish"] if path.name == "build.yml" else [], path.name)
        publication = self.jobs["publish"]["concurrency"]

        def publish_group(event, sha=newer):
            return interpolate(publication["group"], context(event, sha=sha)).casefold()

        self.assertEqual(publish_group("push"), publish_group("schedule"))
        self.assertNotEqual(publish_group("push", older), publish_group("push"))
        self.assertNotIn(publish_group("push"), {run_group(event) for event in ("push", "schedule")})
        self.assertEqual(publication["cancel-in-progress"], "false")

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


if __name__ == "__main__":
    unittest.main()
