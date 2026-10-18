"""Host checks of the FileBot queue: hook records and the supervised worker.

A copy of filebot-queue.php runs with its spool, job runners and delays pointed
at a temporary directory; stub runners stand in for FileBot and GNU find. The
hook wrappers and the completion runner run with their image paths rewritten.
"""

import fcntl
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
        for process, _ in self.workers:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
        for name in ("leader.pid", "child.pid", "cleanup.pid"):
            path = self.dir / name
            if path.exists():
                try:
                    os.kill(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        failed = self.queue / "failed"
        if failed.is_dir():
            failed.chmod(0o700)

    def crash(self, process):
        """SIGKILL the worker alone, as an OOM kill would; its job keeps running."""
        process.kill()
        process.wait(timeout=10)

    def pid(self, name):
        return int((self.dir / name).read_text())

    def next_log(self):
        path = self.dir / "next.log"
        return path.read_text().split() if path.exists() else []

    def wait_for(self, condition, seconds=10, message="condition"):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
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

    def test_a_restarted_worker_never_signals_an_unverified_group(self):
        self.enqueue("completion", "/data/a", "a")
        name = self.records()[0]
        bystander = subprocess.Popen(["sleep", "30"], start_new_session=True)
        self.addCleanup(bystander.wait, timeout=10)
        self.addCleanup(bystander.kill)
        start = int(Path(f"/proc/{bystander.pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
        boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        # A dead worker's record of its job, whose group number now names another
        # session: its leader has another start time. The deadline has passed.
        (self.queue / ".running").write_text(f"{name} {bystander.pid} {start + 1} {os.getpid()} 0 {boot} 0 0\n")
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
        self.assertEqual(len(self.postdl_calls()), 1, "a job that ended with its worker runs again")
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
        self.assertEqual(sorted(os.listdir(self.queue)), [".lock"])

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
