<?php
// Monitor independently of the FPM request pool and preserve unknown long work.
declare(strict_types=1);

// A refused monitor is visibly disabled by finish(64), rather than producing a
// fatal error each second. Never let publication failure authorize recovery.
final class WatchdogRefusal extends RuntimeException
{
    public function __construct(string $reason, public readonly string $detail = '')
    {
        parent::__construct($reason);
    }
}
set_exception_handler(static function (Throwable $error): void {
    $reason = $error instanceof WatchdogRefusal ? $error->getMessage() : 'internal-error';
    $detail = $error instanceof WatchdogRefusal && $error->detail !== '' ? ' detail=' . $error->detail : '';
    fwrite(STDERR, "service-watchdog: unavailable reason=$reason$detail\n");
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

// Run one service-runtime operation. Null means success; otherwise only the
// helper's own classified reason is returned, never free text.
function watchdog_helper(array $arguments, string &$output = ''): ?string
{
    $process = @proc_open(['/usr/local/libexec/service-runtime', ...$arguments],
        [0 => ['file', '/dev/null', 'r'], 1 => ['pipe', 'w'], 2 => ['pipe', 'w']], $pipes);
    if (!is_resource($process)) return 'helper-unavailable';
    $output = (string)stream_get_contents($pipes[1], 4096);
    $errors = (string)stream_get_contents($pipes[2], 4096);
    fclose($pipes[1]);
    fclose($pipes[2]);
    if (proc_close($process) === 0) return null;
    return preg_match('/^service-runtime: ([a-z0-9-]{1,64})$/m', $errors, $match) ? $match[1] : 'unclassified';
}

// service-runtime owns the trusted location and its candidate prefix; it
// prepares the private directory and reports both instead of this file
// repeating them.
function watchdog_location(): array
{
    $output = '';
    $detail = watchdog_helper(['prepare-health'], $output);
    if ($detail !== null) throw new WatchdogRefusal('state-directory-unavailable', $detail);
    if (!preg_match('~\A(/[A-Za-z0-9._/-]+)\n([A-Za-z0-9._-]+)\n\z~', $output, $location)) {
        throw new WatchdogRefusal('state-directory-unavailable', 'invalid-location');
    }
    return [$location[1], $location[2]];
}

function watchdog_publish(array $state, string $directory, string $prefix): void
{
    // The C publisher repeats descriptor-based ancestor/leaf admission before
    // fsync+rename. A failed publication never authorizes a recovery signal.
    $temporary = @tempnam($directory, $prefix);
    if ($temporary === false) throw new WatchdogRefusal('state-candidate-unavailable');
    try {
        if (!@chmod($temporary, 0600)
            || @file_put_contents($temporary, json_encode($state, JSON_THROW_ON_ERROR) . "\n") === false) {
            throw new WatchdogRefusal('state-publication-refused', 'candidate-write-failed');
        }
        $detail = watchdog_helper(['publish-health', basename($temporary)]);
        if ($detail !== null) throw new WatchdogRefusal('state-publication-refused', $detail);
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
[$stateDirectory, $candidatePrefix] = watchdog_location();
$lockPath = $stateDirectory . '/.service-health.lock';
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
// Startup resolves a group name once and exports decimal UID/GID to every
// supervised service; anything else did not come from that supervisor.
// GID 0 is a supported root-group setting, so only an absent value defaults.
$uid = getenv('UID');
$gid = getenv('GID');
if ($uid === false || $uid === '') $uid = '991';
if ($gid === false || $gid === '') $gid = '991';
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
    // Refresh and resume spacing use the monotonic clock: a wall-clock step
    // must delay neither.
    $tick = intdiv(hrtime(true), 1000000000);
    // The state is a snapshot of the observation that published it: a new
    // class, the threshold crossing of any unhealthy class (once per streak)
    // or the five-minute refresh. The live count may since have grown.
    $state = ['schema' => 1, 'observed_at' => $now, 'classification' => $class,
        'consecutive_failures' => $attempts, 'daemon_pid' => $pid,
        'automatic_recovery' => $class === 'daemon-stopped' ? 'pidfd-sigcont-only' : 'deferred'];
    if ($class !== $lastClass || $tick - $lastPublished >= 300 || $attempts === $threshold) {
        watchdog_publish($state, $stateDirectory, $candidatePrefix);
        fwrite(STDOUT, "service-watchdog: classification=$class failures=$attempts pid=$pid\n");
        $lastClass = $class;
        $lastPublished = $tick;
    }
    if ($class === 'daemon-stopped' && $attempts >= $threshold
        && ($pid !== $lastRecoveryPid || $tick - $lastRecoveryAt >= 300)) {
        // SIGCONT is safe for a positively stopped daemon. Unknown blocked work
        // stays alive; a timeout alone cannot distinguish Copy from a deadlock.
        $recovered = watchdog_run(['/sbin/su-exec', "$uid:$gid", '/usr/local/libexec/service-runtime', 'resume-rtorrent', (string)$pid]) === 0;
        fwrite(STDOUT, 'service-watchdog: recovery=' . ($recovered ? 'continued' : 'refused') . " pid=$pid\n");
        $lastRecoveryAt = $tick;
        $lastRecoveryPid = $pid;
    }
    sleep($interval);
}
