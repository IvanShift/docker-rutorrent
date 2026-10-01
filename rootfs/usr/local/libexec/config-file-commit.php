<?php
// The only non-atomic update is an explicitly mounted writable regular file.
declare(strict_types=1);

function commit_fail(string $message): never
{
    fwrite(STDERR, $message . "\n");
    exit(1);
}

function mounted_file(string $path): bool
{
    $resolved = realpath($path);
    if ($resolved === false) {
        return false;
    }
    $mounts = file('/proc/self/mountinfo');
    if ($mounts === false) {
        commit_fail('Unable to inspect mount information');
    }
    foreach ($mounts as $line) {
        $fields = explode(' ', $line);
        $mountpoint = preg_replace_callback('/\\\\([0-7]{3})/',
            static fn(array $match): string => chr(octdec($match[1])), $fields[4] ?? '');
        if ($mountpoint === $resolved) {
            return true;
        }
    }
    return false;
}

$action = $argv[1] ?? '';
$target = $argv[2] ?? '';
if ($action === 'is-mounted') {
    exit(mounted_file($target) ? 0 : 1);
}
if ($action !== 'write-mounted' || !mounted_file($target)) {
    commit_fail('In-place configuration updates require an individual file mount');
}
$candidate = $argv[3] ?? '';
$snapshot = $argv[4] ?? '';
$expectedMetadata = $argv[5] ?? '';
if (is_link($target)) {
    commit_fail("Refusing to update symbolic link: $target");
}
$original = file_get_contents($snapshot);
$replacement = file_get_contents($candidate);
if ($original === false || $replacement === false) {
    commit_fail("Unable to read validated mounted-file migration: $target");
}
// r+b opens without truncation. Validate and lock the opened inode before writing.
$handle = @fopen($target, 'r+b');
if ($handle === false || !flock($handle, LOCK_EX)) {
    commit_fail("Configuration requires migration but mounted file is not writable: $target");
}
$stat = fstat($handle);
$metadata = $stat === false ? '' : sprintf('%o:%d:%d:%d:%d', $stat['mode'] & 07777,
    $stat['uid'], $stat['gid'], $stat['dev'], $stat['ino']);
if ($stat === false || ($stat['mode'] & 0170000) !== 0100000 || $metadata !== $expectedMetadata
    || stream_get_contents($handle) !== $original) {
    commit_fail("Configuration changed during migration: $target");
}
// An individual mount cannot change inode atomically. Defer ordinary shutdown
// signals until the entire validated write (or its error rollback) is durable.
// SIGKILL and power loss cannot be made atomic on this fallback path.
if (!function_exists('pcntl_sigprocmask')
    || !pcntl_sigprocmask(SIG_BLOCK, [SIGHUP, SIGINT, SIGTERM], $originalSignalMask)) {
    commit_fail("Unable to defer signals during mounted configuration update: $target");
}
function write_all($handle, string $contents): bool
{
    if (!rewind($handle)) {
        return false;
    }
    for ($offset = 0, $length = strlen($contents); $offset < $length;) {
        $written = fwrite($handle, substr($contents, $offset));
        if ($written === false || $written === 0) {
            return false;
        }
        $offset += $written;
    }
    return ftruncate($handle, strlen($contents)) && fflush($handle) && fsync($handle);
}
if (!write_all($handle, $replacement)) {
    $restored = write_all($handle, $original);
    commit_fail("Unable to write mounted configuration: $target" . ($restored ? '; original restored' : '; restoration failed'));
}
// A write may clear set-ID bits. Ownership is preserved by keeping the inode.
if (!chmod($target, $stat['mode'] & 07777)) {
    write_all($handle, $original);
    commit_fail("Unable to restore mounted configuration mode: $target");
}
fclose($handle);
pcntl_sigprocmask(SIG_SETMASK, $originalSignalMask);
fwrite(STDOUT, "Updated mounted configuration in place: $target\n");
