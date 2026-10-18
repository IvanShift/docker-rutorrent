<?php
// FileBot work outside rTorrent's synchronous event hooks. postdl and postrm only
// record a request in the persistent spool; the supervised filebot-queue service
// runs one job at a time as the selected service UID, in its own session, with a
// time limit. Hooks run this file with `php85 -n`: they use core functions only.
declare(strict_types=1);

const QUEUE = '/config/filebot/queue';
const SETSID = '/usr/bin/setsid';
const POSTDL = '/usr/local/libexec/filebot-postdl';
const CLEANUP = '/usr/local/libexec/filebot-cleanup';
const ERROR_LOG = '/tmp/filebot-error.log';
// An erase cleanup starts once no further erase was requested for this long.
const CLEANUP_DELAY = 10;
// A stopped or timed-out job's process group gets TERM, then KILL after this.
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

function worker_signal_group(int $pid, int $signal): void
{
    // Before setsid runs, the child is not yet a group leader.
    if (!@posix_kill(-$pid, $signal)) @posix_kill($pid, $signal);
}

// Run COMMAND as a session leader. The job inherits the queue lock as fd 3, so
// a restarted worker never runs beside a job that outlived its predecessor.
// Returns 'done', 'stopped' (shutdown), 'spawn-failed', 'timeout', or
// 'status=N' / 'signal=N'.
function worker_run(array $command, int $timeout, $lock): string
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
    $deadline = hrtime(true) + $timeout * 1000000000;
    $stopped = null;
    $kill_at = 0;
    while (($status = proc_get_status($process))['running']) {
        $now = hrtime(true);
        if ($stopped === null && ($GLOBALS['queue_stop'] || $now >= $deadline)) {
            $stopped = $GLOBALS['queue_stop'] ? 'stopped' : 'timeout';
            worker_signal_group($pid, SIGTERM);
            $kill_at = $now + KILL_GRACE * 1000000000;
        } elseif ($stopped !== null && $now >= $kill_at) {
            worker_signal_group($pid, SIGKILL);
        }
        usleep(100000);
    }
    if ($stopped !== null) {
        // The rest of the group gets the remaining grace period, then KILL.
        while (@posix_kill(-$pid, 0) && hrtime(true) < $kill_at) usleep(100000);
        @posix_kill(-$pid, SIGKILL);
    }
    proc_close($process);
    if ($stopped !== null) return $stopped;
    if ($status['signaled']) return 'signal=' . $status['termsig'];
    return $status['exitcode'] === 0 ? 'done' : 'status=' . $status['exitcode'];
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

// A failed record is kept for inspection or a manual retry (move it back),
// bounded to the newest FAILED_KEEP. Never retried automatically.
function worker_retire(string $name): bool
{
    $failed = QUEUE . '/failed';
    $st = @lstat($failed);
    if ($st === false && @mkdir($failed, 0700)) $st = @lstat($failed);
    if ($st !== false && ($st['mode'] & 0170000) === 0040000 && $st['uid'] === posix_geteuid()
        && @rename(QUEUE . '/' . $name, "$failed/$name")) {
        $kept = array_values(preg_grep(JOB_NAME, @scandir($failed) ?: []));
        foreach (array_slice($kept, 0, max(0, count($kept) - FAILED_KEEP)) as $old) @unlink("$failed/$old");
        return true;
    }
    return @unlink(QUEUE . '/' . $name);
}

function worker_job(string $name, int $timeout, $lock): bool
{
    $started = hrtime(true);
    $fields = worker_record(QUEUE . '/' . $name);
    $outcome = $fields === null ? 'invalid-record' : worker_run([POSTDL, ...$fields], $timeout, $lock);
    $seconds = intdiv(hrtime(true) - $started, 1000000000);
    $title = $fields === null ? '' : ' title=' . queue_text($fields[1]);
    if ($outcome === 'spawn-failed') return true;
    if ($outcome === 'stopped') {
        worker_log("job=$name outcome=stopped seconds=$seconds; it stays queued for the next start$title");
        return true;
    }
    if ($outcome === 'done') {
        worker_log("job=$name outcome=done seconds=$seconds$title");
        return @unlink(QUEUE . '/' . $name);
    }
    worker_log("job=$name outcome=failed reason=$outcome seconds=$seconds; record kept in failed/$title");
    return worker_retire($name);
}

// Return a claimed cleanup request to the flag unless a newer flag exists.
function worker_unclaim(): void
{
    $claimed = QUEUE . '/.cleanup-claimed';
    if (!@link($claimed, QUEUE . '/cleanup') && !file_exists(QUEUE . '/cleanup')) @rename($claimed, QUEUE . '/cleanup');
    @unlink($claimed);
}

// Claim a due cleanup flag. A request that refreshes the flag after the age
// check keeps its full delay.
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
    @unlink(QUEUE . '/.cleanup-claimed');
    return true;
}

function worker_cleanup(int $timeout, $lock): void
{
    $started = hrtime(true);
    $outcome = worker_run([CLEANUP], $timeout, $lock);
    $seconds = intdiv(hrtime(true) - $started, 1000000000);
    if ($outcome === 'stopped' || $outcome === 'spawn-failed') {
        @touch(QUEUE . '/cleanup');
        if ($outcome === 'stopped') worker_log("cleanup outcome=stopped seconds=$seconds; it stays requested for the next start");
    } elseif ($outcome === 'done') {
        worker_log("cleanup outcome=done seconds=$seconds");
    } else {
        worker_log("cleanup outcome=failed reason=$outcome seconds=$seconds; see /tmp/filebot-cleaner.log");
    }
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
    $lock = null;
    $stuck = [];
    while (!$GLOBALS['queue_stop']) {
        $problem = worker_spool_problem();
        foreach (['spool-missing', 'spool-unsafe'] as $reason) worker_refusal($reason, $problem === $reason);
        if ($problem !== null) { worker_pause(POLL); continue; }
        if ($lock === null) {
            $handle = @fopen(QUEUE . '/.lock', 'ce');
            $locked = $handle !== false && flock($handle, LOCK_EX | LOCK_NB);
            worker_refusal('previous-job-running', !$locked);
            if (!$locked) {
                if ($handle !== false) fclose($handle);
                worker_pause(POLL);
                continue;
            }
            $lock = $handle;
            // A cleanup claimed by a worker that died before running it.
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
