<?php
// Monitor independently of the FPM request pool and preserve unknown long work.
declare(strict_types=1);

// A refused monitor is visibly disabled by finish(64), rather than producing a
// fatal error each second. Never let publication failure authorize recovery.
set_exception_handler(static function (Throwable $error): void {
    $known = ['state-directory-unavailable', 'state-candidate-unavailable', 'state-publication-refused'];
    $reason = in_array($error->getMessage(), $known, true) ? $error->getMessage() : 'internal-error';
    fwrite(STDERR, "service-watchdog: unavailable reason=$reason\n");
    exit(64);
});

function watchdog_run(array $arguments): int
{
    $process = @proc_open($arguments, [0 => ['file', '/dev/null', 'r'],
        1 => ['file', '/dev/null', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes);
    return is_resource($process) ? proc_close($process) : 111;
}

function watchdog_integer(string $name, int $default, int $minimum, int $maximum): int
{
    $value = getenv($name);
    if ($value === false) return $default;
    if (!ctype_digit($value) || strlen($value) > 5 || (int)$value < $minimum || (int)$value > $maximum) {
        fwrite(STDERR, "service-watchdog: invalid-setting $name\n");
        exit(64);
    }
    return (int)$value;
}

function watchdog_http(): bool
{
    $process = @proc_open(['/usr/bin/curl', '--noproxy', '*', '-fsS',
        '--connect-timeout', '1', '--max-time', '2', '--max-filesize', '128',
        'http://127.0.0.1:8080/healthz'], [0 => ['file', '/dev/null', 'r'],
        1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes);
    if (!is_resource($process)) return false;
    stream_set_timeout($pipes[1], 3);
    $body = stream_get_contents($pipes[1], 128);
    fclose($pipes[1]);
    return proc_close($process) === 0 && is_string($body) && rtrim($body, "\r\n") === 'ok';
}

function watchdog_publish(array $state): void
{
    $directory = '/run/rutorrent-watchdog';
    // The C publisher repeats descriptor-based ancestor/leaf admission before
    // fsync+rename. A failed publication never authorizes a recovery signal.
    $temporary = @tempnam($directory, '.service-health.json.tmp.');
    if ($temporary === false) throw new RuntimeException('state-candidate-unavailable');
    try {
        if (!@chmod($temporary, 0600)
            || @file_put_contents($temporary, json_encode($state, JSON_THROW_ON_ERROR) . "\n") === false
            || watchdog_run(['/usr/local/libexec/service-runtime', 'publish-health', basename($temporary)]) !== 0) {
            throw new RuntimeException('state-publication-refused');
        }
    } finally {
        if (is_file($temporary)) @unlink($temporary);
    }
}

if (getenv('RUTORRENT_WATCHDOG') === 'false') {
    fwrite(STDOUT, "service-watchdog: disabled by operator\n");
    exit(0);
}
if (getenv('RUTORRENT_WATCHDOG') !== false && getenv('RUTORRENT_WATCHDOG') !== 'true') {
    fwrite(STDERR, "service-watchdog: RUTORRENT_WATCHDOG must be true or false\n");
    exit(64);
}
umask(077);
if (watchdog_run(['/usr/local/libexec/service-runtime', 'prepare-health']) !== 0) {
    throw new RuntimeException('state-directory-unavailable');
}
$lockPath = '/run/rutorrent-watchdog/.service-health.lock';
$before = @lstat($lockPath);
if ($before !== false && (($before['mode'] & 0170000) !== 0100000
    || ($before['mode'] & 07777) !== 0600 || $before['uid'] !== 0 || $before['nlink'] !== 1)) {
    fwrite(STDERR, "service-watchdog: unsafe-monitor-lock\n");
    exit(64);
}
// Probe children must not keep the singleton lock after this monitor exits.
$lock = @fopen($lockPath, $before === false ? 'x+be' : 'r+be');
$opened = is_resource($lock) ? fstat($lock) : false;
if ($opened === false || $opened['uid'] !== 0 || $opened['nlink'] !== 1
    || ($opened['mode'] & 07777) !== 0600) {
    fwrite(STDERR, "service-watchdog: monitor-lock-unavailable\n");
    exit(64);
}
if (!flock($lock, LOCK_EX | LOCK_NB)) {
    fwrite(STDOUT, "service-watchdog: monitor-already-running\n");
    exit(0);
}
$uid = getenv('UID') ?: '991';
$gid = getenv('GID') ?: '991';
if (!ctype_digit($uid) || !ctype_digit($gid)) {
    fwrite(STDERR, "service-watchdog: invalid-service-identity\n");
    exit(64);
}
$interval = watchdog_integer('RUTORRENT_WATCHDOG_INTERVAL_SECONDS', 15, 1, 300);
$start = watchdog_integer('RUTORRENT_WATCHDOG_START_SECONDS', 600, 0, 3600);
$threshold = watchdog_integer('RUTORRENT_WATCHDOG_FAILURES', 3, 1, 100);
$startupDeadline = hrtime(true) + $start * 1000000000;
$started = false;
$lastClass = '';
$lastPublished = 0;
$attempts = 0;
$lastRecoveryAt = 0;
$lastRecoveryPid = 0;
while (true) {
    $scgi = watchdog_run(['/usr/bin/timeout', '3', '/sbin/su-exec', "$uid:$gid",
        '/usr/bin/php85', '/usr/local/libexec/rtorrent-ready.php']) === 0;
    $http = watchdog_http();
    if ($scgi) $started = true;
    $pid = 0;
    $processState = '';
    $output = [];
    $status = 0;
    // Only root-owned supervisor control supplies the process identity. PID reuse
    // is handled by service-runtime, which opens a pidfd before /proc inspection.
    exec('/usr/bin/s6-svstat -o pid /etc/s6.d/rtorrent 2>/dev/null', $output, $status);
    $text = trim(implode('', $output));
    if ($status === 0 && ctype_digit($text) && (int)$text > 1) {
        $pid = (int)$text;
        $stat = @file_get_contents("/proc/$pid/stat");
        if (is_string($stat) && preg_match('/^\d+ \(.*\) ([A-Zt]) /D', $stat, $match)) $processState = $match[1];
    }
    if ($scgi && $http) {
        $class = 'healthy';
        $attempts = 0;
    } elseif (!$started && hrtime(true) < $startupDeadline) {
        $class = 'starting';
        $attempts = 0;
    } else {
        ++$attempts;
        $class = $scgi ? 'http-unavailable' : 'scgi-unresponsive-or-busy';
        if (!$scgi && in_array($processState, ['T', 't'], true)) $class = 'daemon-stopped';
    }
    $now = time();
    $state = ['schema' => 1, 'observed_at' => $now, 'classification' => $class,
        'consecutive_failures' => $attempts, 'daemon_pid' => $pid,
        'automatic_recovery' => $class === 'daemon-stopped' ? 'pidfd-sigcont-only' : 'deferred'];
    if ($class !== $lastClass || $now - $lastPublished >= 300
        || ($class === 'daemon-stopped' && $attempts === $threshold)) {
        watchdog_publish($state);
        fwrite(STDOUT, "service-watchdog: classification=$class failures=$attempts pid=$pid\n");
        $lastClass = $class;
        $lastPublished = $now;
    }
    if ($class === 'daemon-stopped' && $attempts >= $threshold
        && ($pid !== $lastRecoveryPid || $now - $lastRecoveryAt >= 300)) {
        // SIGCONT is safe for a positively stopped daemon. Unknown blocked work
        // stays alive; a timeout alone cannot distinguish Copy from a deadlock.
        $recovered = watchdog_run(['/sbin/su-exec', "$uid:$gid", '/usr/local/libexec/service-runtime', 'resume-rtorrent', (string)$pid]) === 0;
        fwrite(STDOUT, 'service-watchdog: recovery=' . ($recovered ? 'continued' : 'refused') . " pid=$pid\n");
        $lastRecoveryAt = $now;
        $lastRecoveryPid = $pid;
    }
    sleep($interval);
}
