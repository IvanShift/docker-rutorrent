<?php
// FileBot work outside rTorrent's synchronous event hooks. postdl and postrm only
// record a request in the persistent spool; the supervised filebot-queue service
// runs one job at a time as the selected service UID, in its own session, with a
// time limit that a restarted worker still enforces. A job lasts until its whole
// process group has ended. Hooks run this file with `php85 -n`: they use core
// functions only.
declare(strict_types=1);

const QUEUE = '/config/filebot/queue';
const SETSID = '/usr/bin/setsid';
const POSTDL = '/usr/local/libexec/filebot-postdl';
const CLEANUP = '/usr/local/libexec/filebot-cleanup';
const ERROR_LOG = '/tmp/filebot-error.log';
// An erase cleanup starts once no further erase was requested for this long.
const CLEANUP_DELAY = 10;
// A stopped or timed-out job's process group, or what remains of it after its
// leader failed, gets TERM, then KILL after this.
const KILL_GRACE = 10;
const POLL = 2;
const FAILED_KEEP = 50;
const DEFAULT_TIMEOUT = 21600;
const MAX_TIMEOUT = 604800;
const MAX_RECORD = 1048576;
const JOB_NAME = '/^job-[0-9]{16}-[0-9a-f]{12}$/D';

function queue_text(string $text): string
{
    return substr(preg_replace('/[\x00-\x1f\x7f]/', '?', $text) ?? 'invalid', 0, 200);
}

// rTorrent discards hook output, so a refused request is also logged where
// FileBot errors are written.
function queue_refuse(string $reason): never
{
    $line = "filebot-queue: request-refused reason=$reason\n";
    fwrite(STDERR, $line);
    @file_put_contents(ERROR_LOG, '[' . gmdate('Y/m/d-H:i:s') . '] ' . $line, FILE_APPEND);
    exit(1);
}

// Record: BASE_PATH NUL NAME NUL. Names sort by creation time; a temporary name
// is renamed into place, so the worker only sees complete records.
function queue_completion(string $directory, string $title): void
{
    $now = gettimeofday();
    $name = sprintf('job-%010d%06d-%s', $now['sec'], $now['usec'], bin2hex(random_bytes(6)));
    $temporary = QUEUE . '/.tmp-' . $name;
    $record = $directory . "\0" . $title . "\0";
    $handle = @fopen($temporary, 'xb');
    if ($handle === false) queue_refuse('spool-unwritable');
    $written = @fwrite($handle, $record) === strlen($record);
    if (!@fclose($handle) || !$written || !@rename($temporary, QUEUE . '/' . $name)) {
        @unlink($temporary);
        queue_refuse('spool-unwritable');
    }
}

// Every erase refreshes one pending flag; its mtime is the latest request.
function queue_cleanup_request(): void
{
    if (!@touch(QUEUE . '/cleanup')) queue_refuse('spool-unwritable');
}

function worker_log(string $line): void
{
    fwrite(STDERR, "filebot-queue: $line\n");
}

// A refusal is reported once until its condition clears.
function worker_refusal(string $reason, bool $active): void
{
    static $active_reasons = [];
    if ($active && !isset($active_reasons[$reason])) worker_log("waiting reason=$reason");
    if (!$active && isset($active_reasons[$reason])) worker_log("resumed after reason=$reason");
    if ($active) $active_reasons[$reason] = true;
    else unset($active_reasons[$reason]);
}

function worker_timeout(): int
{
    $value = getenv('FILEBOT_JOB_TIMEOUT');
    if ($value === false) return DEFAULT_TIMEOUT;
    if (preg_match('/^[0-9]{1,10}$/D', $value) && (int)$value >= 1 && (int)$value <= MAX_TIMEOUT) return (int)$value;
    worker_log('invalid-setting FILEBOT_JOB_TIMEOUT; using ' . DEFAULT_TIMEOUT . ' seconds');
    return DEFAULT_TIMEOUT;
}

function worker_pause(float $seconds): void
{
    for ($end = hrtime(true) + (int)($seconds * 1e9); !$GLOBALS['queue_stop'] && hrtime(true) < $end;) usleep(100000);
}

function worker_spool_problem(): ?string
{
    $st = @lstat(QUEUE);
    if ($st === false) return 'spool-missing';
    if (($st['mode'] & 0170000) !== 0040000 || $st['uid'] !== posix_geteuid() || ($st['mode'] & 0022) !== 0) return 'spool-unsafe';
    return null;
}

function worker_label(string $name): string
{
    return $name === 'cleanup' ? 'cleanup' : "job=$name";
}

// State, process group, session and start time of PID from /proc.
function worker_stat(int $pid): ?array
{
    $text = @file_get_contents("/proc/$pid/stat");
    $end = is_string($text) ? strrpos($text, ')') : false;
    if ($end === false) return null;
    $fields = explode(' ', substr($text, $end + 2));
    if (count($fields) < 20) return null;
    return ['state' => $fields[0], 'pgrp' => (int)$fields[2], 'session' => (int)$fields[3], 'start' => (int)$fields[19]];
}

function worker_pids(string $directory = '/proc'): ?array
{
    $entries = @scandir($directory);
    return $entries === false ? null : array_values(preg_grep('/^[0-9]+$/D', $entries));
}

// Live group members, [] for a finished group, or null for an uncertain census.
// A child can fork after scandir and exit before its stat is read. Only kernel
// absence, or an unchanged PID census with observed zombies alone, proves that
// the group ended. One bounded pass leaves retries/deadlines to worker_watch.
function worker_members(int $pgid): ?array
{
    $members = [];
    if (!@posix_kill(-$pgid, 0)) return $members;
    $pids = worker_pids();
    if ($pids === null) return null;
    $zombies = false;
    $unreadable = false;
    foreach ($pids as $entry) {
        $stat = worker_stat((int)$entry);
        if ($stat === null) { $unreadable = true; continue; }
        if ($stat['pgrp'] !== $pgid) continue;
        if ($stat['state'] !== 'Z') { $members[(int)$entry] = $stat; continue; }
        $zombies = true;
        // A process leader can be a zombie after pthread_exit while its other
        // threads still run. /proc's top-level census omits those live TIDs.
        $directory = "/proc/$entry/task";
        $tasks = worker_pids($directory);
        if ($tasks === null) { $unreadable = true; continue; }
        foreach ($tasks as $tid) {
            if ($tid === $entry) continue;
            $thread = worker_stat((int)$tid);
            if ($thread === null || $thread['pgrp'] !== $pgid) { $unreadable = true; continue; }
            if ($thread['state'] !== 'Z') $members[(int)$tid] = $thread;
        }
        if (worker_pids($directory) !== $tasks) $unreadable = true;
    }
    if ($members !== []) return $members;
    if (!@posix_kill(-$pgid, 0)) return [];
    // No observed member while kill(0) still sees the group is uncertainty,
    // even when the PID lists happen to match. Never call that group finished.
    return $zombies && !$unreadable && worker_pids() === $pids ? [] : null;
}

function worker_holds(int $pid, array $lock): bool
{
    foreach (@scandir("/proc/$pid/fd") ?: [] as $fd) {
        $st = @stat("/proc/$pid/fd/$fd");
        if ($st !== false && $st['dev'] === $lock['dev'] && $st['ino'] === $lock['ino']) return true;
    }
    return false;
}

function worker_boot(): string
{
    return trim((string)@file_get_contents('/proc/sys/kernel/random/boot_id'));
}

// The running job, kept so that a worker restarted while the job outlives its
// predecessor still enforces the job's deadline: NAME PGID LEADER_START
// WORKER_PID WORKER_START BOOT_ID STARTED DEADLINE (monotonic nanoseconds).
function worker_remember(string $name, int $pid, int $started, int $deadline): void
{
    // Only a leader already reaped by the PID lookup has no stat; its job ended.
    $leader = worker_stat($pid);
    $worker = worker_stat(getmypid());
    if ($leader === null || $worker === null) return;
    $line = "$name $pid {$leader['start']} " . getmypid() . " {$worker['start']} " . worker_boot() . " $started $deadline\n";
    $temporary = QUEUE . '/.tmp-running';
    @unlink($temporary);
    $umask = umask(0077);
    $handle = @fopen($temporary, 'xb');
    umask($umask);
    $saved = false;
    if ($handle !== false) {
        $written = @fwrite($handle, $line) === strlen($line);
        $saved = @fclose($handle) && $written && @rename($temporary, QUEUE . '/.running');
    }
    if ($saved) return;
    @unlink($temporary);
    worker_log(worker_label($name) . ' state-unsaved; if this worker fails, a restarted one waits for the job without its deadline');
}

function worker_remembered(): ?array
{
    $text = @file_get_contents(QUEUE . '/.running', false, null, 0, 256);
    $number = '([0-9]{1,18})';
    if (!is_string($text) || !preg_match("/^(cleanup|job-[0-9]{16}-[0-9a-f]{12}) $number $number $number $number ([0-9a-f-]{36}) $number $number\\n\$/D", $text, $m)) return null;
    return ['name' => $m[1], 'pgid' => (int)$m[2], 'start' => (int)$m[3], 'worker' => (int)$m[4],
        'worker_start' => (int)$m[5], 'boot' => $m[6], 'started' => (int)$m[7], 'deadline' => (int)$m[8]];
}

function worker_forget(): void
{
    @unlink(QUEUE . '/.running');
}

// Whether the process group of a job left by a dead worker still runs. The group
// is trusted only through its leader with the recorded start time, or through a
// member that holds the queue lock; a group's number is not reused while the
// group has members. A job whose worker still runs belongs to that worker.
function worker_orphan_runs(array $job, array $lock): ?bool
{
    // A prior-boot group cannot survive. Its unknown completion is still kept,
    // but none of its old numeric identities may authorize a signal now.
    if ($job['boot'] !== worker_boot()) return false;
    $worker = worker_stat($job['worker']);
    if ($worker !== null && $worker['state'] !== 'Z' && $worker['start'] === $job['worker_start']) return false;
    $leader = worker_stat($job['pgid']);
    if ($leader !== null && $leader['state'] !== 'Z') {
        return $leader['session'] === $job['pgid'] && $leader['start'] === $job['start'];
    }
    $members = worker_members($job['pgid']);
    if ($members === null) return null;
    foreach ($members as $pid => $stat) {
        if ($stat['session'] === $job['pgid'] && $stat['start'] >= $job['start'] && worker_holds($pid, $lock)) return true;
    }
    // Losing the identity witness does not prove an adopted group ended and
    // does not authorize signalling its numeric PGID. Keep watching instead.
    return $members === [] ? false : null;
}

// Follow a job until its whole process group has ended. Shutdown and the
// deadline send TERM to the group, then KILL after KILL_GRACE. A leader that
// failed has the rest of its group stopped the same way; one that succeeded
// leaves the rest the job's remaining time. $poll returns the leader's outcome
// (null while it runs) and whether processes remain; $signal returns whether it
// delivered a signal. Returns the leader's outcome, the requested stop reason,
// and whether an interruption reached the group.
function worker_watch(string $label, int $deadline, callable $poll, callable $signal): array
{
    $stop = null;
    $kill_at = 0;
    $interrupted = false;
    $noted = false;
    while (true) {
        [$leader, $remains] = $poll();
        if (!$remains) return [$leader, $stop, $interrupted];
        $now = hrtime(true);
        if ($stop === null) {
            if ($GLOBALS['queue_stop']) $stop = 'stopped';
            elseif ($now >= $deadline) $stop = 'timeout';
            elseif ($leader !== null && $leader !== 'done') $stop = 'leader-failed';
            if ($stop !== null) {
                $kill_at = $now + KILL_GRACE * 1000000000;
            }
        }
        if ($stop !== null) {
            if ($now >= $kill_at) $interrupted = $signal(SIGKILL) || $interrupted;
            // An uncertain identity may defer TERM. Retry while its original
            // grace runs; requesting shutdown alone does not prove interruption.
            elseif (!$interrupted) $interrupted = $signal(SIGTERM);
        }
        if ($leader !== null && !$noted) {
            $noted = true;
            worker_log("$label leader-exit=$leader; " . ($stop === null ? 'waiting for' : 'stopping') . ' the rest of its process group');
        }
        usleep(100000);
    }
}

function worker_signal_group(int $pid, int $signal): bool
{
    // Before setsid runs, the child is not yet a group leader.
    return @posix_kill(-$pid, $signal) || @posix_kill($pid, $signal);
}

// Run COMMAND as a session leader. The job inherits the queue lock as fd 3, so
// a restarted worker never runs beside a job that outlived its predecessor.
// Returns 'done', 'stopped' (shutdown), 'spawn-failed', 'timeout', or the
// leader's 'status=N' / 'signal=N', once the job's whole group has ended.
function worker_run(string $name, array $command, int $timeout, $lock): string
{
    if ($GLOBALS['queue_stop']) return 'stopped';
    $null = ['file', '/dev/null', 'w'];
    $process = @proc_open([SETSID, ...$command], [0 => ['file', '/dev/null', 'r'], 1 => $null, 2 => $null, 3 => $lock], $pipes);
    // A job that cannot start stays queued; the worker retries after a pause.
    worker_refusal('job-spawn-failed', !is_resource($process));
    if (!is_resource($process)) {
        worker_pause(POLL);
        return 'spawn-failed';
    }
    $pid = proc_get_status($process)['pid'];
    $started = hrtime(true);
    $deadline = $started + $timeout * 1000000000;
    worker_remember($name, $pid, $started, $deadline);
    $leader = null;
    // Until the worker reaps the leader, its PID keeps the group's number.
    $poll = static function () use ($process, $pid, &$leader): array {
        if ($leader === null) {
            $status = proc_get_status($process);
            if ($status['running']) return [null, true];
            $leader = $status['signaled'] ? 'signal=' . $status['termsig'] : ($status['exitcode'] === 0 ? 'done' : 'status=' . $status['exitcode']);
        }
        return [$leader, worker_members($pid) !== []];
    };
    $signal = static function (int $signal) use ($pid, &$leader): bool {
        if ($leader === null) return worker_signal_group($pid, $signal);
        $members = worker_members($pid);
        return $members !== null && $members !== [] && @posix_kill(-$pid, $signal);
    };
    [$leader, $stop, $interrupted] = worker_watch(worker_label($name), $deadline, $poll, $signal);
    proc_close($process);
    return ($stop === 'stopped' && $interrupted) || $stop === 'timeout' ? $stop : $leader;
}

function worker_record(string $path): ?array
{
    $st = @lstat($path);
    if ($st === false || ($st['mode'] & 0170000) !== 0100000 || $st['nlink'] !== 1
        || $st['uid'] !== posix_geteuid() || $st['size'] > MAX_RECORD) return null;
    $data = @file_get_contents($path, false, null, 0, MAX_RECORD + 1);
    if (!is_string($data)) return null;
    $fields = explode("\0", $data);
    return count($fields) === 3 && $fields[2] === '' ? [$fields[0], $fields[1]] : null;
}

function worker_title(?array $fields): string
{
    return $fields === null ? '' : ' title=' . queue_text($fields[1]);
}

// A failed record is kept for inspection or a manual retry (move it back),
// bounded to the newest FAILED_KEEP and never retried automatically. When
// failed/ cannot take it, it stays in the queue as NAME.failed, which no worker
// runs. Returns where the record went, or null when it could not move.
function worker_retire(string $name): ?string
{
    $failed = QUEUE . '/failed';
    $st = @lstat($failed);
    if ($st === false && @mkdir($failed, 0700)) $st = @lstat($failed);
    if ($st !== false && ($st['mode'] & 0170000) === 0040000 && $st['uid'] === posix_geteuid()
        && @rename(QUEUE . '/' . $name, "$failed/$name")) {
        $kept = array_values(preg_grep(JOB_NAME, @scandir($failed) ?: []));
        foreach (array_slice($kept, 0, max(0, count($kept) - FAILED_KEEP)) as $old) @unlink("$failed/$old");
        return 'record kept in failed/';
    }
    if (@rename(QUEUE . '/' . $name, QUEUE . "/$name.failed")) return "failed/ is unavailable; record kept as $name.failed";
    return null;
}

// Settle a completion after its whole process group has ended. False when the
// record could not leave the queue.
function worker_settle_job(string $name, string $outcome, int $seconds, string $title): bool
{
    $settled = true;
    if ($outcome === 'stopped') {
        worker_log("job=$name outcome=stopped seconds=$seconds; it stays queued for the next start$title");
    } elseif ($outcome === 'done') {
        worker_log("job=$name outcome=done seconds=$seconds$title");
        $settled = @unlink(QUEUE . '/' . $name);
    } elseif ($outcome !== 'spawn-failed') {
        // A job that outlived its worker ended with an exit status nobody saw.
        $result = $outcome === 'unknown' ? 'outcome=unknown reason=worker-restarted' : "outcome=failed reason=$outcome";
        $kept = worker_retire($name);
        worker_log("job=$name $result seconds=$seconds; " . ($kept ?? 'record could not leave the queue') . $title);
        $settled = $kept !== null;
    }
    worker_forget();
    return $settled;
}

function worker_job(string $name, int $timeout, $lock): bool
{
    $started = hrtime(true);
    $fields = worker_record(QUEUE . '/' . $name);
    $outcome = $fields === null ? 'invalid-record' : worker_run($name, [POSTDL, ...$fields], $timeout, $lock);
    return worker_settle_job($name, $outcome, intdiv(hrtime(true) - $started, 1000000000), worker_title($fields));
}

// Return a claimed cleanup request to the flag unless a newer flag exists.
function worker_unclaim(): void
{
    $claimed = QUEUE . '/.cleanup-claimed';
    if (!@link($claimed, QUEUE . '/cleanup') && !file_exists(QUEUE . '/cleanup')) @rename($claimed, QUEUE . '/cleanup');
    @unlink($claimed);
}

// Claim a due cleanup flag. A request that refreshes the flag after the age
// check keeps its full delay. The claim stays until the cleanup ends, so a run
// cut short by a crash or a stop is requested again on the next start; a new
// request during the run sets the flag again.
function worker_cleanup_due(): bool
{
    $due = static function (array|false $st): bool {
        $age = $st === false ? CLEANUP_DELAY : time() - $st['mtime'];
        return $age < 0 || $age >= CLEANUP_DELAY;
    };
    $st = @lstat(QUEUE . '/cleanup');
    if ($st === false) return false;
    $regular = ($st['mode'] & 0170000) === 0100000;
    worker_refusal('unsafe-cleanup-request', !$regular);
    if (!$regular || !$due($st) || !@rename(QUEUE . '/cleanup', QUEUE . '/.cleanup-claimed')) return false;
    if (!$due(@lstat(QUEUE . '/.cleanup-claimed'))) {
        worker_unclaim();
        return false;
    }
    return true;
}

function worker_settle_cleanup(string $outcome, int $seconds): void
{
    if ($outcome === 'spawn-failed') {
        worker_unclaim();
    } elseif ($outcome === 'stopped') {
        worker_log("cleanup outcome=stopped seconds=$seconds; it stays requested for the next start");
    } elseif ($outcome === 'unknown') {
        // Recovery may first observe an uncertain census, then an ended group.
        // Its exit status remains unknown; idempotent cleanup must stay requested.
        worker_log("cleanup outcome=unknown reason=worker-restarted seconds=$seconds; it stays requested");
        worker_unclaim();
    } else {
        worker_log(match ($outcome) {
            'done' => "cleanup outcome=done seconds=$seconds",
            default => "cleanup outcome=failed reason=$outcome seconds=$seconds; see /tmp/filebot-cleaner.log",
        });
        @unlink(QUEUE . '/.cleanup-claimed');
    }
    worker_forget();
}

function worker_cleanup(int $timeout, $lock): void
{
    $started = hrtime(true);
    $outcome = worker_run('cleanup', [CLEANUP], $timeout, $lock);
    worker_settle_cleanup($outcome, intdiv(hrtime(true) - $started, 1000000000));
}

// Follow a saved group, including an uncertain census, under its own deadline.
// Signals require a fresh identity witness. Returns false when its record could
// not leave the queue after the group ended.
function worker_adopt(array $job, array $lock, bool $verified): bool
{
    $label = worker_label($job['name']);
    $left = intdiv(max(0, $job['deadline'] - hrtime(true)), 1000000000);
    worker_log($verified ? "$label outlived its worker; adopted with $left seconds left"
        : "$label recovery waiting for group identity; $left seconds left");
    [, $stop, $interrupted] = worker_watch($label, $job['deadline'],
        static fn (): array => [null, worker_orphan_runs($job, $lock) !== false],
        static function (int $signal) use ($job, $lock): bool {
            return worker_orphan_runs($job, $lock) === true && @posix_kill(-$job['pgid'], $signal);
        });
    // A naturally finished orphan has an unknown outcome even when shutdown
    // was requested while its identity could not authorize an interruption.
    $outcome = $stop === 'stopped' && !$interrupted ? 'unknown' : ($stop ?? 'unknown');
    $seconds = intdiv(max(0, hrtime(true) - $job['started']), 1000000000);
    if ($job['name'] !== 'cleanup') {
        return worker_settle_job($job['name'], $outcome, $seconds, worker_title(worker_record(QUEUE . '/' . $job['name'])));
    }
    worker_settle_cleanup($outcome, $seconds);
    return true;
}

function worker(): int
{
    if (!function_exists('pcntl_async_signals') || !function_exists('posix_kill')) {
        worker_log('unavailable reason=missing-php-extension');
        return 64;
    }
    $GLOBALS['queue_stop'] = false;
    pcntl_async_signals(true);
    foreach ([SIGTERM, SIGINT] as $signal) {
        pcntl_signal($signal, static function (): void { $GLOBALS['queue_stop'] = true; });
    }
    $timeout = worker_timeout();
    worker_log("started timeout=$timeout");
    $owner = null;
    $lock = null;
    $stuck = [];
    while (!$GLOBALS['queue_stop']) {
        $problem = worker_spool_problem();
        foreach (['spool-missing', 'spool-unsafe'] as $reason) worker_refusal($reason, $problem === $reason);
        if ($problem !== null) { worker_pause(POLL); continue; }
        if ($owner === null) {
            // Exactly one worker owns recovery and settlement, including while
            // an orphan holds .lock. Close-on-exec keeps this lifetime lock out
            // of jobs, so the next worker can recover after its owner dies.
            $handle = @fopen(QUEUE . '/.worker-lock', 'ce');
            $owned = $handle !== false && flock($handle, LOCK_EX | LOCK_NB);
            worker_refusal('previous-job-running', !$owned);
            if (!$owned) {
                if ($handle !== false) fclose($handle);
                worker_pause(POLL);
                continue;
            }
            $owner = $handle;
        }
        if ($lock === null) {
            $handle = @fopen(QUEUE . '/.lock', 'ce');
            $locked = $handle !== false && flock($handle, LOCK_EX | LOCK_NB);
            // A job that outlived a dead worker keeps its original deadline.
            $job = $handle === false ? null : worker_remembered();
            if ($job !== null && ($runs = worker_orphan_runs($job, fstat($handle))) !== false) {
                if (!worker_adopt($job, fstat($handle), $runs === true)) {
                    $stuck[$job['name']] = true;
                    worker_log("job={$job['name']} outcome=unremovable; skipped until the next start");
                }
                fclose($handle);
                continue;
            }
            if ($job !== null && $locked) {
                // Its group may have finished during the worker's downtime.
                // Completion hooks can have side effects: an unknown result
                // must never authorize replay. A graceful stop already settled
                // its record and removed .running, so it still retries normally.
                if ($job['name'] !== 'cleanup') {
                    $seconds = intdiv(max(0, hrtime(true) - $job['started']), 1000000000);
                    if (!worker_settle_job($job['name'], 'unknown', $seconds, worker_title(worker_record(QUEUE . '/' . $job['name'])))) {
                        $stuck[$job['name']] = true;
                        worker_log("job={$job['name']} outcome=unremovable; skipped until the next start");
                    }
                } else {
                    // Cleanup is idempotent; its surviving claim is retried.
                    worker_forget();
                }
            }
            worker_refusal('previous-job-running', !$locked);
            if (!$locked) {
                if ($handle !== false) fclose($handle);
                worker_pause(POLL);
                continue;
            }
            $lock = $handle;
            // A cleanup claim left by a worker that stopped or died before the cleanup ended.
            if (@lstat(QUEUE . '/.cleanup-claimed') !== false) worker_unclaim();
        }
        if (worker_cleanup_due()) { worker_cleanup($timeout, $lock); continue; }
        $next = null;
        foreach (@scandir(QUEUE) ?: [] as $name) {
            if (str_starts_with($name, '.tmp-')) {
                // An interrupted request never became a record.
                $st = @lstat(QUEUE . '/' . $name);
                if ($st !== false && time() - $st['mtime'] > 3600) @unlink(QUEUE . '/' . $name);
            } elseif ($next === null && !isset($stuck[$name]) && preg_match(JOB_NAME, $name)) {
                $next = $name;
            }
        }
        if ($next === null) { worker_pause(POLL); continue; }
        // A record that cannot leave the queue is skipped until the next start.
        if (!worker_job($next, $timeout, $lock)) {
            $stuck[$next] = true;
            worker_log("job=$next outcome=unremovable; skipped until the next start");
        }
    }
    return 0;
}

$mode = $argv[1] ?? '';
// Records are private; jobs keep the service umask that rTorrent hooks had.
if (in_array($mode, ['completion', 'cleanup'], true)) umask(0077);
if ($mode === 'completion' && $argc <= 4) queue_completion($argv[2] ?? '', $argv[3] ?? '');
elseif ($mode === 'cleanup' && $argc === 2) queue_cleanup_request();
elseif ($mode === 'worker' && $argc === 2) exit(worker());
else {
    fwrite(STDERR, "Usage: filebot-queue.php completion [BASE_PATH [NAME]] | cleanup | worker\n");
    exit(64);
}
