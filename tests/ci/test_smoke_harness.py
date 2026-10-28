"""Host-only behavior of the shared Python smoke helpers (tests/smoke/smoke_harness.py)."""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests/smoke"))
import smoke_harness  # noqa: E402 - import the smoke helper after path setup
from test_smoke_lib import REVIEWED_NATIVE_CASES, REVIEWED_NATIVE_SKIP  # noqa: E402 - the shell gate's cases

FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['FAKE_DOCKER_TRACE'], 'a') as trace:
    trace.write(json.dumps(args) + '\n')
if args[0] == 'inspect':
    reply = os.environ['FAKE_DOCKER_INSPECT']
    if not reply:
        sys.exit(1)
    print(reply)
# An image label; an unset FAKE_IMAGE_VERSION is an unreadable image.
if args[:2] == ['image', 'inspect']:
    if 'FAKE_IMAGE_VERSION' not in os.environ:
        sys.exit(1)
    print(os.environ['FAKE_IMAGE_VERSION'])
# A watchdog scenario: rTorrent runs as PID 4242 until the fixture is stopped.
scenario = os.environ.get('FAKE_WATCHDOG', '')
stopped = pathlib.Path(os.environ['FAKE_DOCKER_TRACE']).with_name('stopped')
if args[:2] == ['exec', 'c'] and args[2:4] == ['kill', '-STOP']:
    stopped.write_text('yes')
elif args[:2] == ['exec', 'c'] and args[2:] == ['s6-svstat', '-o', 'pid', '/etc/s6.d/rtorrent']:
    print('5151' if scenario == 'replaced' and stopped.exists() else '4242')
elif args == ['logs', 'c'] and scenario != 'silent':
    print('service-watchdog: recovery=continued pid=4242', file=sys.stderr)
elif args[:1] == ['exec'] and args[-1:] == ['system.pid']:
    print('4242')
elif args == ['exec', 'c', 'rutorrent-healthcheck']:
    sys.exit(0)
"""

# A stateful fake daemon: "run" creates a labelled container and, with
# FAKE_START_FAILS, prints its ID and exits 127 as the real CLI does when the
# entrypoint cannot start. Every call is traced.
FAKE_DAEMON = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
trace = pathlib.Path(os.environ['FAKE_DOCKER_TRACE'])
with trace.open('a') as handle:
    handle.write(json.dumps(args) + '\n')
path = trace.with_name('state.json')
state = json.loads(path.read_text()) if path.exists() else {'containers': {}, 'networks': {}}
def save():
    path.write_text(json.dumps(state))
def labels():
    return dict(args[index + 1].split('=', 1) for index, arg in enumerate(args[:-1]) if arg == '--label')
def find(target):
    return next((c for c in state['containers'].values() if target in (c['Id'], c['Name'])), None)
if args[:2] == ['image', 'inspect']:
    print('0.16.27' if 'rtorrent.version' in ' '.join(args) else 'sha256:' + 'f' * 64)
elif args[:2] == ['network', 'create']:
    state['networks'][args[-1]] = {'Id': 'n' * 64, 'Labels': labels()}
    save(); print('n' * 64)
elif args[:2] == ['network', 'inspect']:
    print(json.dumps([state['networks'][args[2]]]))
elif args[:2] == ['network', 'rm']:
    if state['containers']:
        sys.exit('network has active endpoints')
    state['networks'] = {}
    save()
elif args[0] == 'run':
    identity = '%064x' % (len(state['containers']) + 1)
    name = args[args.index('--name') + 1] if '--name' in args else identity[:12]
    state['containers'][identity] = {'Id': identity, 'Name': name, 'Config': {'Labels': labels()},
                                     'State': {'Running': False}, 'Mounts': [{'Type': 'volume', 'Name': 'anonymous'}]}
    save(); print(identity)
    if os.environ.get('FAKE_START_FAILS'):
        sys.exit('docker: Error response from daemon: failed to create task for container')
    state['containers'][identity]['State']['Running'] = True
    save()
elif args[:2] == ['ps', '-aq']:
    key, _, value = args[args.index('--filter') + 1].removeprefix('label=').partition('=')
    for container in state['containers'].values():
        if container['Config']['Labels'].get(key) == value:
            print(container['Id'])
elif args[0] == 'inspect':
    container = find(args[1])
    if container is None:
        sys.exit('Error: No such object')
    print(json.dumps([container]))
elif args[0] == 'stop':
    find(args[-1])['State']['Running'] = False
    save()
elif args[:3] == ['rm', '-f', '-v']:
    del state['containers'][find(args[3])['Id']]
    save()
else:
    sys.exit('unexpected docker call: ' + ' '.join(args))
"""


class StartFailureCleanupTests(unittest.TestCase):
    """docker run -d can create a container and still fail to start it."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="smoke-start-")
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        docker = self.work / "docker"
        docker.write_text(FAKE_DAEMON)
        docker.chmod(0o755)
        self.trace = self.work / "trace"
        self.trace.write_text("")
        self.environment = dict(os.environ, PATH=f"{self.work}{os.pathsep}{os.environ['PATH']}",
                                FAKE_DOCKER_TRACE=str(self.trace), FAKE_START_FAILS="1")

    def state(self):
        path = self.work / "state.json"
        return json.loads(path.read_text()) if path.exists() else {"containers": {}, "networks": {}}

    def test_failed_start_removes_the_created_container_before_raising(self):
        with mock.patch.dict(os.environ, self.environment):
            with self.assertRaisesRegex(RuntimeError, "status=1"):
                smoke_harness.start_container("owner-1", "--name", "case", "image")
        self.assertEqual(self.state()["containers"], {})
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        self.assertEqual(calls[0][:5], ["run", "--pull=never", "--label", f"{smoke_harness.OWNER_LABEL}=owner-1", "-d"])
        self.assertIn(["rm", "-f", "-v", "%064x" % 1], calls)

    def test_run_cleanup_removes_only_this_runs_containers(self):
        environment = dict(self.environment, FAKE_START_FAILS="")
        with mock.patch.dict(os.environ, environment):
            mine = smoke_harness.start_container("owner-1", "--name", "mine", "image")
            other = smoke_harness.start_container("owner-2", "--name", "other", "image")
            removed = smoke_harness.remove_run_containers("owner-1")
        self.assertEqual(removed, {mine: ["anonymous"]})
        self.assertEqual(list(self.state()["containers"]), [other])
        self.assertEqual(smoke_harness.run_argv("o", "x")[:5], ["docker", "run", "--pull=never", "--label",
                                                                 f"{smoke_harness.OWNER_LABEL}=o"])

    def test_harnesses_leave_nothing_behind_when_the_start_fails(self):
        for harness, arguments in (("http-runtime.py", ["--scope", "watchdog"]), ("service-runtime.py", [])):
            with self.subTest(harness=harness):
                (self.work / "state.json").unlink(missing_ok=True)
                result = subprocess.run([sys.executable, str(ROOT / "tests/smoke" / harness), "fake-image", *arguments],
                                        env=self.environment, capture_output=True, text=True, timeout=60)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("failed to create task", result.stderr)
                state = self.state()
                self.assertEqual(state["containers"], {}, result.stderr)
                self.assertEqual(state["networks"], {}, result.stderr)


class SmokeHarnessTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="smoke-harness-")
        self.addCleanup(temp.cleanup)
        self.work = Path(temp.name)
        docker = self.work / "docker"
        docker.write_text(FAKE_DOCKER)
        docker.chmod(0o755)
        self.trace = self.work / "trace"
        self.trace.write_text("")
        self.environment = {"PATH": f"{self.work}{os.pathsep}{os.environ['PATH']}",
                            "FAKE_DOCKER_TRACE": str(self.trace)}

    def remove(self, inspect, owned_id="owned-id"):
        environment = dict(self.environment, FAKE_DOCKER_INSPECT=inspect)
        with mock.patch.dict(os.environ, environment):
            result = smoke_harness.remove_owned_container("name", owned_id, "owner-1")
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return result, [call for call in calls if call[0] != "inspect"]

    @staticmethod
    def container(identity, owner, running):
        return json.dumps([{"Id": identity, "Config": {"Labels": {smoke_harness.OWNER_LABEL: owner}},
                            "State": {"Running": running},
                            "Mounts": [{"Type": "volume", "Name": "anonymous-1"}, {"Type": "bind"}]}])

    def test_only_the_exact_owned_container_is_stopped_and_removed(self):
        result, calls = self.remove(self.container("owned-id", "owner-1", True))
        self.assertEqual(result, ["anonymous-1"])
        self.assertEqual(calls, [["stop", "--timeout", "60", "owned-id"], ["rm", "-f", "-v", "owned-id"]])

    def test_foreign_missing_or_unrecorded_containers_are_left_alone(self):
        for inspect, owned_id in ((self.container("owned-id", "owner-2", True), "owned-id"),
                                  (self.container("other-id", "owner-1", True), "owned-id"),
                                  ("", "owned-id"),
                                  (self.container("owned-id", "owner-1", True), "")):
            with self.subTest(inspect=inspect[:40], owned_id=owned_id):
                self.trace.write_text("")
                self.assertEqual(self.remove(inspect, owned_id), (None, []))

    def test_owner_tokens_use_the_one_label_scheme(self):
        self.assertEqual(smoke_harness.OWNER_LABEL, "io.ivanshift.smoke.owner")
        first, second = smoke_harness.owner_token("http-runtime"), smoke_harness.owner_token("http-runtime")
        self.assertRegex(first, r"\Ahttp-runtime-[0-9a-f-]{36}\Z")
        self.assertNotEqual(first, second)

    def reviewed_native(self, label, expected):
        """Call require_reviewed_native; return (exit code or None, stdout, docker calls)."""
        self.trace.write_text("")
        environment = dict(self.environment)
        if label is not None:
            environment["FAKE_IMAGE_VERSION"] = label
        with mock.patch.dict(os.environ, environment):
            os.environ.pop("SMOKE_EXPECTED_RTORRENT_VERSION", None)
            if expected is not None:
                os.environ["SMOKE_EXPECTED_RTORRENT_VERSION"] = expected
            exit_code = None
            with contextlib.redirect_stdout(io.StringIO()) as output:
                try:
                    smoke_harness.require_reviewed_native("fixture-image", "fixture gate")
                except SystemExit as stop:
                    exit_code = stop.code
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return exit_code, output.getvalue(), calls

    def test_reviewed_native_gate_runs_the_shell_gates_cases(self):
        inspect = ["image", "inspect", "fixture-image", "--format",
                   '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}']
        for label, expected, runs in REVIEWED_NATIVE_CASES:
            with self.subTest(label=label, expected=expected):
                exit_code, output, calls = self.reviewed_native(label, expected)
                self.assertEqual((exit_code, output), (None, "") if runs else (0, REVIEWED_NATIVE_SKIP))
                self.assertEqual(calls, [inspect])
        with self.assertRaisesRegex(RuntimeError, "status=1"):
            self.reviewed_native(None, None)

    def watchdog(self, scenario):
        environment = dict(self.environment, FAKE_WATCHDOG=scenario, FAKE_DOCKER_INSPECT="")
        with mock.patch.dict(os.environ, environment):
            result = smoke_harness.watchdog_resumes_stopped_daemon("c", seconds=0.6)
        calls = [json.loads(line) for line in self.trace.read_text().splitlines()]
        return result, [call[2:] for call in calls if call[:3] == ["exec", "c", "kill"]]

    def test_watchdog_scenario_requires_the_same_resumed_process(self):
        # Both the unauthenticated HTTP harness and the service harness use it.
        for scenario, expected in (("resumed", True), ("silent", False), ("replaced", False)):
            with self.subTest(scenario=scenario):
                self.trace.write_text("")
                (self.work / "stopped").unlink(missing_ok=True)
                result, signals = self.watchdog(scenario)
                self.assertIs(bool(result), expected)
                # The daemon is stopped once and always continued afterwards.
                self.assertEqual(signals, [["kill", "-STOP", "4242"], ["kill", "-CONT", "4242"]])

    def test_stopped_daemon_scenario_has_one_implementation(self):
        # http-runtime.py (HTTP_AUTH=false) and service-runtime.py (default
        # authentication) call the shared scenario instead of copying it.
        users = []
        for path in sorted((ROOT / "tests/smoke").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            if path.name != "smoke_harness.py":
                self.assertFalse("'-STOP'" in text, f"{path.name} copies the SIGSTOP scenario")
                if "watchdog_resumes_stopped_daemon(" in text:
                    users.append(path.name)
        self.assertEqual(users, ["http-runtime.py", "service-runtime.py"])

    def test_poll_treats_transient_failures_as_not_yet(self):
        answers = iter([RuntimeError("docker exec failed"), ValueError("partial JSON"), False, True])

        def predicate():
            answer = next(answers)
            if isinstance(answer, Exception):
                raise answer
            return answer

        with mock.patch("time.sleep"):
            self.assertTrue(smoke_harness.poll(predicate, seconds=60))
            with self.assertRaisesRegex(RuntimeError, "deadline"):
                smoke_harness.wait(lambda: False, seconds=0)
            with self.assertRaises(KeyError):
                smoke_harness.poll(lambda: {}["classification"], seconds=60)


if __name__ == "__main__":
    unittest.main()
