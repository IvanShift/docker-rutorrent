"""Host checks of the FileBot queue: hook records and the supervised worker.

A copy of filebot-queue.php runs with its spool, job runners and delays pointed
at a temporary directory; stub runners stand in for FileBot and GNU find. The
hook wrappers and the completion runner run with their image paths rewritten.
"""

import ctypes
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from host_php import requires_host_php

ROOT = Path(__file__).resolve().parents[2]
QUEUE_PHP = ROOT / "rootfs/usr/local/libexec/filebot-queue.php"
JOB_NAME = re.compile(rb"job-[0-9]{16}-[0-9a-f]{12}")
# Records arrive as argv: any byte except NUL, including newlines, shell syntax,
# invalid UTF-8, a leading dash and empty fields.
AWKWARD = [
    (b"/data/downloads/Show S01 [1080p]\n'q' \"d\" $(touch x) `y` \\", b"Show\nS01 \xff\xfe; rm -rf /"),
    (b"", b""),
    (b"-rf", b"--def"),
]

STUB_POSTDL = r"""#!/bin/sh
# Log the exact arguments, then act on the title.
printf '%s\0%s\0' "$1" "$2" >> "$STUB_DIR/postdl.log"
test -e /proc/self/fd/3 && echo lock >> "$STUB_DIR/fd3.log"
umask > "$STUB_DIR/umask"
case "$2" in
  fail) exit 3 ;;
  slow)
    sleep 60 & echo "$!" > "$STUB_DIR/child.pid"
    echo "$$" > "$STUB_DIR/leader.pid"
    wait ;;
  brief)
    sleep 3 & echo "$!" > "$STUB_DIR/child.pid"
    echo "$$" > "$STUB_DIR/leader.pid"
    wait ;;
  released)
    echo "$$" > "$STUB_DIR/leader.pid"
    while [ ! -f "$STUB_DIR/release" ]; do sleep 0.02; done
    echo done > "$STUB_DIR/child.done" ;;
  successor-slow)
    echo "$$" > "$STUB_DIR/next.pid"
    sleep 60 ;;
  controlled)
    echo "$$" > "$STUB_DIR/leader.pid"
    exec python3 "$STUB_DIR/controlled.py" ;;
  stubborn)
    trap '' TERM
    sleep 60 & echo "$!" > "$STUB_DIR/child.pid"
    echo "$$" > "$STUB_DIR/leader.pid"
    while :; do sleep 1; done ;;
  detach)
    # Succeed at once, leaving a background child that finishes later.
    (sleep 2; echo done > "$STUB_DIR/child.done") & echo "$!" > "$STUB_DIR/child.pid" ;;
  detach-hung)
    sleep 60 & echo "$!" > "$STUB_DIR/child.pid" ;;
  handoff)
    echo "$$" > "$STUB_DIR/leader.pid"
    python3 "$STUB_DIR/handoff.py" & ;;
  zombie)
    echo "$$" > "$STUB_DIR/leader.pid"
    exec python3 "$STUB_DIR/zombie.py" ;;
  thread)
    echo "$$" > "$STUB_DIR/leader.pid"
    python3 "$STUB_DIR/thread.py" & echo "$!" > "$STUB_DIR/child.pid" ;;
  next)
    # Whether the previous job's child still runs beside this job.
    pid=$(cat "$STUB_DIR/child.pid" 2>/dev/null)
    state=$(cut -d' ' -f3 "/proc/${pid:-0}/stat" 2>/dev/null)
    if [ -n "$state" ] && [ "$state" != Z ]; then echo overlap; else echo clean; fi >> "$STUB_DIR/next.log" ;;
esac
"""
STUB_CLEANUP = r"""#!/bin/sh
echo "$$" > "$STUB_DIR/cleanup.pid"
echo run >> "$STUB_DIR/cleanup.log"
sleep "${STUB_CLEANUP_SECONDS:-0}"
"""


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie (not yet reaped by init in a test sandbox) no longer runs.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1][0] != "Z"
    except (FileNotFoundError, IndexError):
        return False


@requires_host_php("FileBot queue checks", extensions=("posix", "pcntl"))
class FileBotQueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rem1018-u15-queue-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.queue = self.dir / "queue"
        self.queue.mkdir(mode=0o700)
        self.queue.chmod(0o700)
        for name, text in (("postdl", STUB_POSTDL), ("cleanup", STUB_CLEANUP)):
            (self.dir / name).write_text(text)
            (self.dir / name).chmod(0o755)
        self.script = self.dir / "filebot-queue.php"
        self.write_script()
        self.workers = []
        self.fixture_pids = {}
        # Reap jobs orphaned by the deliberate worker/leader crash cases. The
        # container has its own subreaper; a unit-test runner otherwise does not.
        libc = ctypes.CDLL(None)
        previous = ctypes.c_int()
        self.assertEqual(libc.prctl(37, ctypes.byref(previous), 0, 0, 0), 0)
        self.assertEqual(libc.prctl(36, 1, 0, 0, 0), 0)
        self.addCleanup(libc.prctl, 36, previous.value, 0, 0, 0)
        self.addCleanup(self.stop_workers)

    def write_script(self, **constants):
        values = {"QUEUE": f"'{self.queue}'", "POSTDL": f"'{self.dir}/postdl'",
                  "CLEANUP": f"'{self.dir}/cleanup'", "ERROR_LOG": f"'{self.dir}/error.log'",
                  "CLEANUP_DELAY": "1", "KILL_GRACE": "1", "POLL": "0.2", **constants}
        text = QUEUE_PHP.read_text()
        for name, value in values.items():
            text, count = re.subn(rf"(?m)^const {name} = .*;$", f"const {name} = {value};", text)
            self.assertEqual(count, 1, name)
        self.script.write_text(text)

    def php_boundary(self, functions):
        """Instrument low-level calls only in the temporary script under test."""
        source = self.script.read_text()
        declaration = "declare(strict_types=1);"
        self.assertIn(declaration, source)
        self.script.write_text(source.replace(declaration, declaration + "\nnamespace QueueTest;\n" + functions, 1))

    def deny_proc_stat_reads(self):
        """Model inaccessible process identity until the fixture restores it."""
        self.php_boundary(r'''
function file_get_contents($path, $include = false, $context = null, $offset = 0, $length = null) {
    if (getenv('HIDE_STAT') === 'yes' && !file_exists(getenv('STUB_DIR') . '/stat-readable')
        && preg_match('~^/proc/[0-9]+/stat$~', $path)) return false;
    return $length === null ? \file_get_contents($path, $include, $context, $offset)
        : \file_get_contents($path, $include, $context, $offset, $length);
}
''')

    def env(self, **extra):
        return {"PATH": os.environ["PATH"], "STUB_DIR": str(self.dir), **extra}

    def enqueue(self, *arguments):
        return subprocess.run(["php", "-n", str(self.script), *arguments], capture_output=True,
                              env=self.env(), timeout=30)

    def records(self, directory=None):
        return sorted(path.name for path in (directory or self.queue).iterdir()
                      if JOB_NAME.fullmatch(path.name.encode()))

    def start_worker(self, **extra):
        log = open(self.dir / f"worker-{len(self.workers)}.log", "wb")
        self.addCleanup(log.close)
        process = subprocess.Popen(["php", str(self.script), "worker"], stdout=log, stderr=subprocess.STDOUT,
                                   env=self.env(**extra), umask=0o022)
        self.workers.append((process, log.name))
        return process

    def worker_log(self, index=0):
        return Path(self.workers[index][1]).read_text(errors="replace")

    def stop_workers(self):
        self.remember_pids()
        for process, _ in self.workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for pid, (start, group) in self.fixture_pids.items():
            try:
                current = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                # A Z main thread can still have live sibling threads. Its
                # recorded start and PGID remain valid cleanup identities.
                if int(current[19]) == start and int(current[2]) == group:
                    os.killpg(group, signal.SIGKILL)
            except (ProcessLookupError, FileNotFoundError):
                pass
        end = time.monotonic() + 3
        while time.monotonic() < end:
            try:
                if os.waitpid(-1, os.WNOHANG)[0] == 0:
                    time.sleep(0.01)
            except ChildProcessError:
                break
        failed = self.queue / "failed"
        if failed.is_dir():
            failed.chmod(0o700)

    def remember_pids(self):
        """Record the identity of processes published by this fixture only."""
        for name in ("leader.pid", "child.pid", "cleanup.pid", "next.pid"):
            try:
                pid = int((self.dir / name).read_text())
                fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                if int(fields[2]) != os.getpgrp():
                    self.fixture_pids.setdefault(pid, (int(fields[19]), int(fields[2])))
            except (FileNotFoundError, ValueError):
                pass

    def crash(self, process):
        """SIGKILL the worker alone, as an OOM kill would; its job keeps running."""
        process.kill()
        process.wait(timeout=10)

    def pid(self, name):
        self.remember_pids()
        return int((self.dir / name).read_text())

    def next_log(self):
        path = self.dir / "next.log"
        return path.read_text().split() if path.exists() else []

    def wait_for(self, condition, seconds=10, message="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                self.remember_pids()
                return
            time.sleep(0.05)
        self.fail(f"timed out waiting for {message}; worker log:\n" + "".join(
            Path(log).read_text(errors="replace") for _, log in self.workers))

    def terminate(self, process):
        process.send_signal(signal.SIGTERM)
        self.assertEqual(process.wait(timeout=15), 0)

    def postdl_calls(self):
        path = self.dir / "postdl.log"
        fields = path.read_bytes().split(b"\0")[:-1] if path.exists() else []
        return [tuple(fields[index:index + 2]) for index in range(0, len(fields), 2)]

    def test_completion_records_keep_arbitrary_bytes_in_creation_order(self):
        for directory, title in AWKWARD:
            result = self.enqueue("completion", directory, title)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout + result.stderr, b"")
        names = self.records()
        self.assertEqual(len(names), len(AWKWARD))
        self.assertEqual(sorted(os.listdir(self.queue)), names, "no temporary or extra entry")
        for name, (directory, title) in zip(names, AWKWARD):
            self.assertEqual((self.queue / name).read_bytes(), directory + b"\0" + title + b"\0")
            self.assertEqual((self.queue / name).stat().st_mode & 0o777, 0o600)
        self.assertFalse((self.dir / "x").exists(), "record content must never reach a shell")

    def test_cleanup_requests_coalesce_into_one_flag(self):
        for _ in range(3):
            self.assertEqual(self.enqueue("cleanup").returncode, 0)
        self.assertEqual(os.listdir(self.queue), ["cleanup"])

    def test_refused_request_is_logged_and_fails(self):
        self.queue.rmdir()
        for arguments in (("completion", "/data/x", "x"), ("cleanup",)):
            result = self.enqueue(*arguments)
            self.assertEqual(result.returncode, 1)
            self.assertIn(b"request-refused reason=spool-unwritable", result.stderr)
        self.assertEqual((self.dir / "error.log").read_text().count("request-refused reason=spool-unwritable"), 2)
        self.assertEqual(self.enqueue("completion", "a", "b", "c").returncode, 64)

    def test_worker_runs_jobs_fifo_with_exact_arguments_and_the_lock(self):
        for directory, title in AWKWARD:
            self.enqueue("completion", directory, title)
        worker = self.start_worker()
        self.wait_for(lambda: not self.records(), message="queued jobs")
        self.terminate(worker)
        self.assertEqual(self.postdl_calls(), AWKWARD)
        self.assertEqual((self.dir / "fd3.log").read_text().split(), ["lock"] * len(AWKWARD))
        self.assertEqual((self.dir / "umask").read_text().strip(), "0022", "jobs keep the service umask")
        self.assertEqual(self.worker_log().count("outcome=done"), len(AWKWARD))
        self.assertIn("title=Show?S01", self.worker_log(), "titles are logged without control characters")

    def test_failures_are_retired_once_with_bounded_retention(self):
        self.write_script(FAILED_KEEP="2")
        for index in range(4):
            self.enqueue("completion", f"/data/{index}", "fail")
        invalid = self.queue / "job-0000000000000000-000000000000"
        invalid.write_bytes(b"only one field")
        names = self.records()
        worker = self.start_worker()
        self.wait_for(lambda: not self.records(), message="failed jobs")
        time.sleep(0.5)
        self.terminate(worker)
        self.assertEqual(len(self.postdl_calls()), 4, "failures are never retried")
        self.assertEqual(self.records(self.queue / "failed"), names[-2:])
        log = self.worker_log()
        self.assertEqual(log.count("reason=status=3"), 4)
        self.assertIn("job=job-0000000000000000-000000000000 outcome=failed reason=invalid-record", log)

    def test_timeout_stops_the_whole_process_group(self):
        self.enqueue("completion", "/data/hung", "stubborn")
        worker = self.start_worker(FILEBOT_JOB_TIMEOUT="1")
        self.wait_for(lambda: (self.dir / "child.pid").exists(), message="job start")
        leader = int((self.dir / "leader.pid").read_text())
        child = int((self.dir / "child.pid").read_text())
        self.wait_for(lambda: "reason=timeout" in self.worker_log(), seconds=15, message="timeout")
        self.assertFalse(alive(leader) or alive(child), "TERM-ignoring leader and its child are killed")
        self.terminate(worker)
        self.assertEqual(len(self.records(self.queue / "failed")), 1)

    def test_shutdown_stops_the_job_and_keeps_it_queued(self):
        self.enqueue("completion", "/data/slow", "slow")
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "child.pid").exists(), message="job start")
        child = int((self.dir / "child.pid").read_text())
        started = time.monotonic()
        self.terminate(worker)
        self.assertLess(time.monotonic() - started, 5)
        self.assertFalse(alive(child))
        self.assertEqual(len(self.records()), 1, "an interrupted job runs again on the next start")
        self.assertIn("outcome=stopped", self.worker_log())
        # The next worker runs it again; a job removed meanwhile is not invented.
        (self.dir / "child.pid").unlink()
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "child.pid").exists(), message="job restart")
        self.terminate(worker)
        self.assertEqual(len(self.postdl_calls()), 2)

    def test_cleanup_waits_for_quiet_and_reruns_once_for_requests_during_a_run(self):
        # mtime has whole seconds here: a 3-second delay waits at least 2 seconds.
        self.write_script(CLEANUP_DELAY="3")
        self.enqueue("cleanup")
        worker = self.start_worker(STUB_CLEANUP_SECONDS="2")
        runs = lambda: (self.dir / "cleanup.log").read_text().count("run") if (self.dir / "cleanup.log").exists() else 0
        time.sleep(1.5)
        self.enqueue("cleanup")  # refreshes the delay
        time.sleep(1.5)
        self.assertEqual(runs(), 0, "a refreshed request restarts the quiet period")
        self.wait_for(lambda: runs() == 1, message="first cleanup")
        for _ in range(3):
            self.enqueue("cleanup")
        self.wait_for(lambda: runs() == 2 and "cleanup outcome=done" in self.worker_log(), seconds=15,
                      message="second cleanup")
        time.sleep(3)
        self.terminate(worker)
        self.assertEqual(runs(), 2, "requests during a run cause exactly one more run")
        self.assertFalse((self.queue / "cleanup").exists())

    def test_jobs_wait_for_a_held_lock_and_an_unsafe_spool(self):
        self.enqueue("completion", "/data/a", "a")
        with open(self.queue / ".lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            worker = self.start_worker()
            self.wait_for(lambda: "waiting reason=previous-job-running" in self.worker_log(), message="lock wait")
            time.sleep(0.5)
            self.assertEqual(len(self.records()), 1)
        self.wait_for(lambda: not self.records(), message="job after lock release")
        self.terminate(worker)
        self.queue.chmod(0o777)
        self.enqueue("completion", "/data/b", "b")
        worker = self.start_worker(FILEBOT_JOB_TIMEOUT="0")
        self.wait_for(lambda: "waiting reason=spool-unsafe" in self.worker_log(1), message="spool refusal")
        self.assertEqual(len(self.records()), 1)
        self.queue.chmod(0o700)
        self.wait_for(lambda: not self.records(), message="job after repair")
        self.terminate(worker)
        log = self.worker_log(1)
        self.assertIn("resumed after reason=spool-unsafe", log)
        self.assertIn("invalid-setting FILEBOT_JOB_TIMEOUT; using 21600 seconds", log)

    # PHP answers a repeated stat()/lstat() of the last path from its stat
    # cache. Since 8.3.19 (php bug #72666) every plain-stream read or write,
    # such as a log line, drops that cache; Ubuntu 24.04's PHP 8.3.6 keeps it
    # until unlink, rename, chmod or touch. A poll that stats one path and does
    # no other file I/O must still see each change, so these cases leave
    # several silent polls before the change.

    def test_a_spool_repaired_after_several_polls_is_noticed(self):
        self.queue.chmod(0o777)
        self.enqueue("completion", "/data/b", "b")
        worker = self.start_worker()
        self.wait_for(lambda: "waiting reason=spool-unsafe" in self.worker_log(), message="spool refusal")
        time.sleep(1)  # five polls of 0.2 s that write nothing
        self.assertEqual(len(self.records()), 1)
        self.queue.chmod(0o700)
        self.wait_for(lambda: not self.records(), message="job after repair")
        self.terminate(worker)
        self.assertIn("resumed after reason=spool-unsafe", self.worker_log())
        self.assertEqual(self.postdl_calls(), [(b"/data/b", b"b")])

    def test_a_spool_made_unsafe_while_idle_is_refused_before_the_next_job(self):
        worker = self.start_worker()
        self.wait_for(lambda: (self.queue / ".lock").exists(), message="idle worker")
        time.sleep(1)  # idle polls of the safe spool
        self.queue.chmod(0o777)
        self.wait_for(lambda: "waiting reason=spool-unsafe" in self.worker_log(), message="spool refusal")
        self.enqueue("completion", "/data/c", "c")
        time.sleep(1)
        self.assertEqual(self.postdl_calls(), [], "no job runs from an unsafe spool")
        self.assertEqual(len(self.records()), 1)
        self.queue.chmod(0o700)
        self.wait_for(lambda: not self.records(), message="job after repair")
        self.terminate(worker)
        self.assertEqual(self.postdl_calls(), [(b"/data/c", b"c")])

    # Crash transitions. "next" records whether the previous job's child still
    # runs when the next job starts.

    def test_a_killed_leader_takes_its_group_down_before_the_next_job(self):
        self.enqueue("completion", "/data/slow", "slow")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists(), message="job start")
        child = self.pid("child.pid")
        os.kill(self.pid("leader.pid"), signal.SIGKILL)  # e.g. the OOM killer picks the hook leader
        self.wait_for(self.next_log, message="next job")
        self.assertEqual(self.next_log(), ["clean"], "the next job never runs beside the killed job's child")
        self.assertFalse(alive(child))
        self.wait_for(lambda: not self.records(), message="queue drained")
        self.terminate(worker)
        self.assertEqual(self.records(self.queue / "failed"), [first])
        self.assertIn(f"job={first} outcome=failed reason=signal=9", self.worker_log())

    def test_a_successful_leader_waits_for_its_background_child(self):
        self.enqueue("completion", "/data/detach", "detach")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker()
        self.wait_for(self.next_log, message="next job")
        self.assertEqual(self.next_log(), ["clean"], "the next job waits for the whole process group")
        self.assertTrue((self.dir / "child.done").exists(), "the background child finished its work")
        self.terminate(worker)
        self.assertIn(f"job={first} outcome=done", self.worker_log())
        self.assertFalse((self.queue / "failed").exists())

    def test_a_background_child_keeps_the_job_deadline(self):
        self.enqueue("completion", "/data/detach", "detach-hung")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker(FILEBOT_JOB_TIMEOUT="2")
        self.wait_for(self.next_log, seconds=15, message="next job")
        self.assertEqual(self.next_log(), ["clean"])
        self.terminate(worker)
        self.assertEqual(self.records(self.queue / "failed"), [first])
        self.assertIn(f"job={first} outcome=failed reason=timeout", self.worker_log())

    def test_a_stale_proc_snapshot_cannot_finish_a_live_process_group(self):
        # The real directory listing is captured before a fork handoff, then
        # returned unchanged after the listed parent has exited. This is a
        # scheduler pause at the syscall boundary, not a fake process table.
        self.php_boundary(r'''
function scandir(string $path, int $order = SCANDIR_SORT_ASCENDING, $context = null): array|false {
    $entries = \scandir($path, $order);
    $root = getenv('STUB_DIR');
    if ($path === '/proc' && file_exists($root . '/handoff.ready')
        && !file_exists($root . '/snapshot.done')) {
        touch($root . '/snapshot.request');
        $end = hrtime(true) + 5000000000;
        while (!file_exists($root . '/handoff.done') && hrtime(true) < $end) usleep(1000);
        file_put_contents($root . '/snapshot.done', json_encode($entries));
    }
    return $entries;
}
''')
        (self.dir / "handoff.py").write_text(r'''
import json, os, pathlib, time
root = pathlib.Path(os.environ['STUB_DIR'])
parent = os.getpid()
(root / 'child.pid').write_text(str(parent))
(root / 'handoff.ready').write_text(str(parent))
end = time.monotonic() + 10
while not (root / 'snapshot.request').exists() and time.monotonic() < end:
    time.sleep(.001)
child = os.fork()
if child:
    os._exit(0)
while os.getppid() == parent:
    time.sleep(.001)
witness = {'pid': os.getpid(), 'pgid': os.getpgrp()}
(root / 'child.pid').write_text(str(os.getpid()))
(root / 'handoff.done').write_text(json.dumps(witness))
while not (root / 'finish-child').exists() and time.monotonic() < end:
    if (root / 'next.log').exists():
        (root / 'overlap.json').write_text(json.dumps(witness))
    time.sleep(.001)
(root / 'child.done').write_text('done')
''')
        self.enqueue("completion", "/data/handoff", "handoff")
        self.enqueue("completion", "/data/next", "next")
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "snapshot.done").exists() and (self.dir / "handoff.done").exists(),
                      message="real fork handoff after the process listing")
        witness = json.loads((self.dir / "handoff.done").read_text())
        self.assertEqual(witness["pgid"], self.pid("leader.pid"), "the child never leaves the first job's group")
        self.assertNotIn(str(witness["pid"]), json.loads((self.dir / "snapshot.done").read_text()),
                         "the returned snapshot really predates this live child")
        # Give the watch loop two normal polls to act on the stale census.
        time.sleep(0.25)
        if self.next_log():
            self.wait_for(lambda: (self.dir / "overlap.json").exists(), message="live child's overlap witness")
            self.assertEqual(json.loads((self.dir / "overlap.json").read_text()), witness)
        self.assertEqual(self.next_log(), [], "a live member witnessed the next job start after a stale census")
        self.assertTrue(alive(witness["pid"]))
        (self.dir / "finish-child").touch()
        self.wait_for(self.next_log, message="next job after the replacement child finishes")
        self.terminate(worker)
        self.assertEqual(self.next_log(), ["clean"])
        self.assertTrue((self.dir / "child.done").exists())
        self.assertFalse((self.dir / "overlap.json").exists())

    def test_a_zombie_only_group_does_not_hold_the_queue(self):
        # The test runner is a subreaper, so the exited child remains a real
        # zombie until fixture cleanup; kill(-pgid, 0) still reports this group.
        (self.dir / "zombie.py").write_text(r'''
import os, pathlib, time
root = pathlib.Path(os.environ['STUB_DIR'])
parent = os.getpid()
child = os.fork()
if child:
    (root / 'child.pid').write_text(str(child))
    os._exit(0)
while os.getppid() == parent:
    time.sleep(.001)
os._exit(0)
''')
        self.enqueue("completion", "/data/zombie", "zombie")
        self.enqueue("completion", "/data/next", "next")
        worker = self.start_worker()
        self.wait_for(self.next_log, message="queue passes a group containing only a zombie")
        child = self.pid("child.pid")
        fields = Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()
        self.assertEqual(fields[0], "Z")
        self.assertEqual(int(fields[2]), self.pid("leader.pid"))
        os.killpg(int(fields[2]), 0)
        self.terminate(worker)
        self.assertEqual(self.next_log(), ["clean"])
        self.assertFalse((self.queue / "failed").exists())

    def test_a_zombie_main_thread_does_not_hide_a_live_sibling_thread(self):
        # pthread_exit ends the main thread without ending its sibling. Linux
        # exposes a Z process leader in /proc/PID/stat alongside the live task.
        (self.dir / "thread.py").write_text(r'''
import ctypes, json, os, pathlib, threading, time
root = pathlib.Path(os.environ['STUB_DIR'])
def work():
    witness = {'pid': os.getpid(), 'pgid': os.getpgrp(), 'tid': threading.get_native_id()}
    (root / 'thread.ready').write_text(json.dumps(witness))
    end = time.monotonic() + 10
    while not (root / 'finish-thread').exists() and time.monotonic() < end:
        if (root / 'next.log').exists():
            (root / 'thread.overlap').write_text(json.dumps(witness))
        time.sleep(.002)
    (root / 'thread.done').touch()
    os._exit(0)
threading.Thread(target=work).start()
ctypes.CDLL(None).pthread_exit(None)
''')
        self.enqueue("completion", "/data/thread", "thread")
        self.enqueue("completion", "/data/next", "next")
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "thread.ready").exists() and (self.dir / "child.pid").exists(),
                      message="background thread starts")
        child = self.pid("child.pid")
        try:
            self.wait_for(lambda: Path(f"/proc/{child}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z",
                          message="main thread exits while its sibling remains alive")
            witness = json.loads((self.dir / "thread.ready").read_text())
            self.assertEqual(witness["pid"], child)
            self.assertEqual(witness["pgid"], self.pid("leader.pid"))
            thread_state = Path(f"/proc/{child}/task/{witness['tid']}/stat").read_text().rsplit(")", 1)[1].split()[0]
            self.assertNotEqual(thread_state, "Z")
            time.sleep(0.3)
            if self.next_log():
                self.wait_for(lambda: (self.dir / "thread.overlap").exists(), message="live thread's overlap witness")
                self.assertEqual(json.loads((self.dir / "thread.overlap").read_text()), witness)
            self.assertEqual(self.next_log(), [], "a live sibling thread keeps the process group active")
        finally:
            (self.dir / "finish-thread").touch()
            os.waitpid(child, 0)
        self.wait_for(self.next_log, message="queue proceeds after the last thread finishes")
        self.terminate(worker)
        self.assertEqual(self.next_log(), ["clean"])
        self.assertTrue((self.dir / "thread.done").exists())
        self.assertFalse((self.dir / "thread.overlap").exists())

    def test_a_restarted_worker_enforces_the_deadline_of_an_orphaned_job(self):
        self.enqueue("completion", "/data/hung", "stubborn")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker(FILEBOT_JOB_TIMEOUT="3")
        self.wait_for(lambda: (self.dir / "leader.pid").exists(), message="job start")
        started = time.monotonic()
        leader, child = self.pid("leader.pid"), self.pid("child.pid")
        self.crash(worker)
        self.assertTrue(alive(leader) and alive(child), "the job outlives its worker and keeps the lock")
        worker = self.start_worker(FILEBOT_JOB_TIMEOUT="1")  # the job keeps its own deadline
        self.wait_for(lambda: not alive(leader) and not alive(child), seconds=15, message="orphan stop")
        self.assertLess(time.monotonic() - started, 3 + 1 + 3, "timeout and grace, with polling slack")
        self.wait_for(self.next_log, message="queue proceeds")
        self.terminate(worker)
        self.assertEqual(self.next_log(), ["clean"])
        self.assertEqual(self.records(self.queue / "failed"), [first])
        self.assertEqual(len(self.postdl_calls()), 2, "the timed-out orphan is not run again")
        self.assertIn(f"job={first} outcome=failed reason=timeout", self.worker_log(1))

    def test_an_orphaned_job_that_ends_by_itself_is_not_run_again(self):
        self.enqueue("completion", "/data/brief", "brief")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists(), message="job start")
        self.crash(worker)
        worker = self.start_worker()
        self.wait_for(self.next_log, seconds=15, message="queue proceeds")
        self.terminate(worker)
        self.assertEqual(self.next_log(), ["clean"], "the next job waits for the orphan")
        self.assertEqual([title for _, title in self.postdl_calls()], [b"brief", b"next"],
                         "its exit status is unknown, so it is kept, not run again")
        self.assertEqual(self.records(self.queue / "failed"), [first])
        self.assertIn(f"job={first} outcome=unknown", self.worker_log(1))

    def test_an_orphan_that_finishes_before_recovery_is_not_replayed(self):
        self.assert_completed_orphan_is_not_replayed()

    def test_a_completion_from_a_previous_boot_is_not_replayed(self):
        self.assert_completed_orphan_is_not_replayed(prior_boot=True)

    def assert_completed_orphan_is_not_replayed(self, *, prior_boot=False):
        self.enqueue("completion", "/data/released", "released")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        worker = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists() and (self.queue / ".running").exists(),
                      message="running job and recovery state")
        leader = self.pid("leader.pid")
        self.crash(worker)
        self.assertTrue(alive(leader), "the job survives the worker")
        (self.dir / "release").touch()
        self.wait_for(lambda: (self.dir / "child.done").exists() and not alive(leader),
                      message="orphan finishes before any successor starts")
        if prior_boot:
            state = self.queue / ".running"
            fields = state.read_text().split()
            old_boot = "00000000-0000-0000-0000-000000000000"
            self.assertNotEqual(fields[5], old_boot)
            fields[5] = old_boot
            state.write_text(" ".join(fields) + "\n")
        worker = self.start_worker()
        self.wait_for(self.next_log, message="queue proceeds after delayed recovery")
        self.terminate(worker)
        self.assertEqual([title for _, title in self.postdl_calls()], [b"released", b"next"],
                         "a completed orphan must not repeat side effects during recovery")
        self.assertEqual(self.records(self.queue / "failed"), [first])
        self.assertIn(f"job={first} outcome=unknown", self.worker_log(1))

    def test_two_successors_keep_the_next_job_recoverable(self):
        # Hold the second successor just before it forgets the old job, if it
        # incorrectly adopted that job too. All filesystem operations stay real.
        self.php_boundary(r'''
function unlink(string $path, $context = null): bool {
    $root = getenv('STUB_DIR');
    $held = false;
    if (getenv('HOLD_OLD_SETTLEMENT') === 'yes' && $path === QUEUE . '/.running'
        && !file_exists($root . '/settlement-held')) {
        touch($root . '/settlement-held');
        $held = true;
        $end = hrtime(true) + 5000000000;
        while (!file_exists($root . '/settlement-release') && hrtime(true) < $end) usleep(1000);
    }
    $result = \unlink($path);
    if ($held) touch($root . '/settlement-finished');
    return $result;
}
''')
        self.enqueue("completion", "/data/released", "released")
        self.enqueue("completion", "/data/successor", "successor-slow")
        old, following = self.records()
        original = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists() and (self.queue / ".running").exists(),
                      message="original job state")
        self.crash(original)
        first = self.start_worker(FILEBOT_JOB_TIMEOUT="2")
        self.wait_for(lambda: "adopted" in self.worker_log(1), message="first successor adoption")
        second = self.start_worker(FILEBOT_JOB_TIMEOUT="60", HOLD_OLD_SETTLEMENT="yes")
        self.wait_for(lambda: "adopted" in self.worker_log(2) or "previous-job-running" in self.worker_log(2),
                      message="second successor admission")
        double_adoption = "adopted" in self.worker_log(2)
        (self.dir / "release").touch()
        self.wait_for(lambda: (self.dir / "next.pid").exists(), message="next job starts")
        next_pid = self.pid("next.pid")
        next_parent = int(Path(f"/proc/{next_pid}/stat").read_text().rsplit(")", 1)[1].split()[1])
        state = self.queue / ".running"
        self.wait_for(lambda: state.exists() and state.read_text().split()[0] == following,
                      message="next job recovery state")
        (self.dir / "settlement-release").touch()
        if double_adoption:
            self.wait_for(lambda: (self.dir / "settlement-finished").exists(), message="stale settlement completes")
        self.assertTrue(state.exists(), "settling the old job must not delete the next job's recovery state")
        self.assertEqual(state.read_text().split()[0], following)
        owner = next(process for process in (first, second) if process.pid == next_parent)
        self.crash(owner)
        self.wait_for(lambda: not alive(next_pid), seconds=6, message="next job keeps its two-second deadline")
        survivor = second if owner is first else first
        self.wait_for(lambda: not self.records(), message="recovered timeout settles")
        self.terminate(survivor)
        self.assertEqual(self.records(self.queue / "failed"), [old, following])
        self.assertEqual([title for _, title in self.postdl_calls()], [b"released", b"successor-slow"])
        self.assertIn(f"job={following} outcome=failed reason=timeout", self.worker_log(2 if survivor is second else 1))

    def test_stop_during_unverified_adoption_does_not_replay_a_finished_job(self):
        # An inaccessible /proc stat cannot authorize a signal. If the job ends
        # naturally while identity is unavailable, requesting worker shutdown
        # alone must not turn its unknown result into a retryable interruption.
        self.deny_proc_stat_reads()
        (self.dir / "controlled.py").write_text(r'''
import os, pathlib, time
root = pathlib.Path(os.environ['STUB_DIR'])
while not (root / 'release').exists():
    time.sleep(.01)
(root / 'child.done').touch()
''')
        self.enqueue("completion", "/data/controlled", "controlled")
        name = self.records()[0]
        original = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists() and (self.queue / ".running").exists(),
                      message="original job and recovery state")
        leader = self.pid("leader.pid")
        self.crash(original)
        successor = self.start_worker(HIDE_STAT="yes")
        self.wait_for(lambda: "waiting for group identity" in self.worker_log(1),
                      message="unverified orphan stays pending")
        successor.send_signal(signal.SIGTERM)
        time.sleep(0.25)
        self.assertIsNone(successor.poll())
        self.assertTrue(alive(leader), "unverified identity does not authorize a signal")
        (self.dir / "release").touch()
        self.wait_for(lambda: (self.dir / "child.done").exists() and not alive(leader),
                      message="orphan finishes naturally without receiving a stop signal")
        os.waitpid(leader, 0)  # remove the zombie while its stat is inaccessible
        self.assertEqual(successor.wait(timeout=3), 0)
        worker = self.start_worker()
        self.wait_for(lambda: "started timeout=" in self.worker_log(2) and not self.records(),
                      message="next worker leaves the unknown completion retired")
        self.terminate(worker)
        self.assertEqual([title for _, title in self.postdl_calls()], [b"controlled"])
        self.assertEqual(self.records(self.queue / "failed"), [name])
        self.assertIn(f"job={name} outcome=unknown", self.worker_log(1))

    def test_a_restarted_worker_never_signals_an_unverified_group(self):
        self.assert_unverified_group_is_never_signalled()

    def test_a_previous_boot_record_never_signals_a_current_process_group(self):
        self.assert_unverified_group_is_never_signalled(prior_boot=True)

    def assert_unverified_group_is_never_signalled(self, *, prior_boot=False):
        self.enqueue("completion", "/data/a", "a")
        name = self.records()[0]
        bystander = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(bystander.wait, timeout=10)
        self.addCleanup(bystander.kill)
        start = int(Path(f"/proc/{bystander.pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        # A dead worker's record of its job, whose group number now names another
        # session: its leader has another start time or boot. The deadline has
        # passed. A prior-boot record must be harmless even if PID/start match.
        if prior_boot:
            boot = "00000000-0000-0000-0000-000000000000"
        else:
            start += 1
        (self.queue / ".running").write_text(f"{name} {bystander.pid} {start} {os.getpid()} 0 {boot} 0 0\n")
        with open(self.queue / ".lock", "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            worker = self.start_worker()
            self.wait_for(lambda: "waiting reason=previous-job-running" in self.worker_log(), message="lock wait")
            time.sleep(1)
            self.assertIsNone(bystander.poll(), "an unverified process group is never signalled")
            self.assertEqual(self.records(), [name])
        self.wait_for(lambda: not self.records(), message="job after lock release")
        self.terminate(worker)
        self.assertIsNone(bystander.poll())
        self.assertEqual(self.postdl_calls(), [], "an unverified saved completion has an unknown outcome")
        self.assertEqual(self.records(self.queue / "failed"), [name])
        self.assertFalse((self.queue / ".running").exists())

    def test_a_second_worker_never_adopts_the_job_of_a_running_worker(self):
        self.enqueue("completion", "/data/brief", "brief")
        first = self.start_worker()
        self.wait_for(lambda: (self.dir / "leader.pid").exists(), message="job start")
        second = self.start_worker(FILEBOT_JOB_TIMEOUT="1")
        self.wait_for(lambda: "outcome=done" in self.worker_log(), message="job end")
        self.terminate(second)
        self.terminate(first)
        self.assertNotIn("adopted", self.worker_log(1))
        self.assertIn("waiting reason=previous-job-running", self.worker_log(1))
        self.assertEqual(len(self.postdl_calls()), 1)

    def test_a_cleanup_interrupted_by_a_crash_runs_again(self):
        runs = lambda: (self.dir / "cleanup.log").read_text().count("run") if (self.dir / "cleanup.log").exists() else 0
        self.enqueue("cleanup")
        os.utime(self.queue / "cleanup", (time.time() - 60,) * 2)
        worker = self.start_worker(STUB_CLEANUP_SECONDS="30")
        self.wait_for(lambda: runs() == 1, message="cleanup start")
        cleanup = self.pid("cleanup.pid")
        # A hard stop: the worker and the cleanup's process group die together.
        self.crash(worker)
        os.killpg(cleanup, signal.SIGKILL)
        self.wait_for(lambda: not alive(cleanup), message="cleanup killed")
        worker = self.start_worker()
        self.wait_for(lambda: runs() == 2 and "cleanup outcome=done" in self.worker_log(1), message="cleanup rerun")
        self.terminate(worker)
        self.assertEqual(runs(), 2)
        self.assertEqual(sorted(os.listdir(self.queue)), [".lock", ".worker-lock"])

    def test_cleanup_retries_after_uncertain_recovery_observes_its_end(self):
        # A transient unreadable census enters adoption before a dead cleanup
        # can be recognized. Its unknown outcome must keep the idempotent work
        # requested, just like recovery that first observes an absent group.
        self.deny_proc_stat_reads()
        (self.dir / "cleanup").write_text('''#!/usr/bin/env python3
import os, pathlib, time
root = pathlib.Path(os.environ['STUB_DIR'])
(root / 'cleanup.pid').write_text(str(os.getpid()))
with (root / 'cleanup.log').open('a') as log:
    log.write('run\\n')
while not (root / 'cleanup-release').exists():
    time.sleep(.01)
''')
        runs = lambda: (self.dir / "cleanup.log").read_text().count("run") if (self.dir / "cleanup.log").exists() else 0
        self.enqueue("cleanup")
        os.utime(self.queue / "cleanup", (time.time() - 60,) * 2)
        original = self.start_worker()
        self.wait_for(lambda: runs() == 1 and (self.queue / ".running").exists(), message="original cleanup")
        cleanup = self.pid("cleanup.pid")
        self.crash(original)
        (self.dir / "cleanup-release").touch()
        self.wait_for(lambda: not alive(cleanup), message="cleanup ends before recovery")
        successor = self.start_worker(HIDE_STAT="yes")
        self.wait_for(lambda: "cleanup recovery waiting for group identity" in self.worker_log(1),
                      message="uncertain cleanup enters adoption")
        (self.dir / "stat-readable").touch()
        self.wait_for(lambda: runs() == 2 and "cleanup outcome=done" in self.worker_log(1),
                      seconds=3, message="unknown cleanup is requested again")
        self.terminate(successor)
        self.assertEqual(runs(), 2)
        self.assertFalse((self.queue / ".cleanup-claimed").exists())
        self.assertFalse((self.queue / "cleanup").exists())

    def test_a_failed_record_survives_an_unwritable_failed_directory(self):
        failed = self.queue / "failed"
        failed.mkdir()
        failed.chmod(0o500)
        if os.geteuid() == 0:  # root ignores the mode; a file in its place refuses the same way
            failed.rmdir()
            failed.write_bytes(b"")
        self.enqueue("completion", "/data/f", "fail")
        self.enqueue("completion", "/data/next", "next")
        first = self.records()[0]
        record = (self.queue / first).read_bytes()
        worker = self.start_worker()
        self.wait_for(self.next_log, message="next job")
        self.terminate(worker)
        self.assertEqual((self.queue / f"{first}.failed").read_bytes(), record, "the original bytes are kept")
        self.assertEqual(os.listdir(failed) if failed.is_dir() else [], [])
        log = self.worker_log()
        self.assertIn(f"job={first} outcome=failed reason=status=3", log)
        self.assertIn(f"failed/ is unavailable; record kept as {first}.failed", log)
        self.assertNotIn("record kept in failed/", log)
        worker = self.start_worker()
        time.sleep(1)
        self.terminate(worker)
        self.assertEqual([title for _, title in self.postdl_calls()], [b"fail", b"next"],
                         "a restart does not run the failed job again")


@requires_host_php("FileBot hook wrapper checks")
class FileBotHookWrapperTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="rem1018-u15-hooks-")
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "queue").mkdir()
        text = QUEUE_PHP.read_text().replace("'/config/filebot/queue'", f"'{self.dir}/queue'")
        (self.dir / "filebot-queue.php").write_text(text)

    def install(self, source, replacements):
        text = (ROOT / source).read_text()
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        target = self.dir / Path(source).name
        target.write_text(text)
        target.chmod(0o755)
        return target

    def test_hooks_only_queue_requests(self):
        php = {"/usr/bin/php85": shutil.which("php"),
               "/usr/local/libexec/filebot-queue.php": str(self.dir / "filebot-queue.php")}
        postdl = self.install("rootfs/usr/local/bin/postdl", php)
        postrm = self.install("rootfs/usr/local/bin/postrm", php)
        for _ in range(2):
            self.assertEqual(subprocess.run([str(postdl), "/data/a b", "a\nb"], timeout=30).returncode, 0)
            self.assertEqual(subprocess.run([str(postrm)], timeout=30).returncode, 0)
        names = sorted(os.listdir(self.dir / "queue"))
        self.assertEqual(names[0], "cleanup")
        self.assertEqual(len(names), 3)
        self.assertEqual((self.dir / "queue" / names[1]).read_bytes(), b"/data/a b\0a\nb\0")

    def test_completion_runner_prefers_an_executable_regular_custom_hook(self):
        packaged = self.dir / "filebot.sh"
        packaged.write_text("#!/bin/sh\nprintf packaged; printf '|%s' \"$@\"\n")
        packaged.chmod(0o755)
        custom = self.dir / "custom"
        runner = self.install("rootfs/usr/local/libexec/filebot-postdl", {
            "/config/filebot/postdl": str(custom), "/filebot/filebot.sh": str(packaged),
            "/tmp/filebot-error.log": str(self.dir / "error.log")})
        env = {"PATH": os.environ["PATH"], "FILEBOT_LANG": "en", "FILEBOT_RENAME_METHOD": "symlink",
               "FILEBOT_CONFLICT": "skip"}

        def run():
            return subprocess.run([str(runner), "/data/x", "x y"], capture_output=True, text=True,
                                  env=env, timeout=30).stdout

        self.assertEqual(run(), "packaged|--lang|en|-script|fn:amc|--output|/data/media|--action|symlink"
                                "|--conflict|skip|-non-strict|--log-file|/tmp/filebot-amc.log"
                                "|--def|@/filebot/args_amc.txt|--def|ut_dir=/data/x|--def|ut_title=x y")
        custom.write_text("#!/bin/sh\nprintf custom; printf '|%s' \"$@\"\n")
        self.assertIn("packaged", run(), "a non-executable hook is not run")
        custom.chmod(0o755)
        self.assertEqual(run(), "custom|/data/x|x y")
        custom.rename(self.dir / "real-hook")
        custom.symlink_to(self.dir / "real-hook")
        self.assertIn("packaged", run(), "a symlinked hook is not run")


if __name__ == "__main__":
    unittest.main()
