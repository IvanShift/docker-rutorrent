<?php
// Explicit offline maintenance: inspect data only; never load operator PHP.
ini_set('display_errors', '0');
ini_set('log_errors', '0');
function refuse(string $message): never {
    fwrite(STDERR, 'Cannot attest erasedata queue root: '.preg_replace('/[\x00-\x1f\x7f]/', '?', $message)."\n");
    exit(78);
}
$uidText = getenv('UID') === false ? '991' : getenv('UID');
$gidText = getenv('GID') === false ? '991' : getenv('GID');
$group = ctype_digit($gidText) ? null : posix_getgrnam($gidText);
if (!ctype_digit($uidText) || strlen($uidText) > 10 || (int)$uidText < 1 || (int)$uidText > 4294967294 || (!ctype_digit($gidText) && $group === false)) refuse('UID must be 1..4294967294 and GID an existing group or numeric ID');
$uid = (int)$uidText;
$gid = ctype_digit($gidText) ? (int)$gidText : (int)$group['gid'];
if ($gid < 0 || $gid > 4294967294 || (ctype_digit($gidText) && strlen($gidText) > 10)) refuse('numeric GID must be 0..4294967294');
$checked = 0;
function entries(string $path): array {
    global $checked;
    $names = @scandir($path);
    if ($names === false || count($names) > 100002 || ($checked += count($names)) > 100000) refuse("$path cannot be inspected within the 100000 entry limit");
    return array_values(array_diff($names, ['.', '..']));
}
function metadata(string $path, bool $directory = false, bool $private = false, bool $allowCapturedStateMode = false): array {
    global $uid;
    clearstatcache(true, $path);
    $stat = @lstat($path);
    $safePermissions = $stat !== false && (($stat['mode'] & ($private ? 0077 : 0022)) === 0 || ($allowCapturedStateMode && ($stat['mode'] & 07777) === 0666));
    if ($stat === false || ($stat['mode'] & 0170000) !== ($directory ? 0040000 : 0100000) || !in_array($stat['uid'], [0, $uid], true) || (!$directory && $stat['nlink'] !== 1) || !$safePermissions) refuse("$path must be an owned ".($directory ? 'directory' : 'single-link regular file')." with safe permissions");
    return $stat;
}
// The captured V-8 queue is idle but contains these control files. Presence
// never establishes idleness: validate every entry and the drain-state body.
function idleQueue(string $queue): array {
    global $uid;
    metadata($queue, true, true);
    $removable = [];
    $key = $keyState = false;
    foreach (entries($queue) as $name) {
        $path = $queue.'/'.$name;
        if (in_array($name, ['.drain-state.lock', '.drain-worker.lock', 'scheduler.lock'], true) || preg_match('/^[0-9A-Fa-f]{40}\.lock$/D', $name)) {
            clearstatcache(true, $path);
            $stat = @lstat($path);
            // Historical fopen('c') locks can be 0666; they are empty data,
            // not obligations, and the private offline queue protects them.
            if ($stat === false || ($stat['mode'] & 0170000) !== 0100000 || !in_array($stat['uid'], [0, $uid], true) || $stat['nlink'] !== 1 || $stat['size'] !== 0) refuse("$path is not an empty owned single-link lock");
        } elseif ($name === '.drain-state') {
            // The real writer chmods this data file to 0666. Its already-attested
            // private queue confines it; owner, link and idle-schema checks remain.
            $stat = metadata($path, false, true, true);
            if ($stat['size'] > 16384) refuse("$path exceeds the idle state limit");
            $state = json_decode(@file_get_contents($path), true);
            $keys = ['version','user','generation','acknowledged','phase','journal','diagnostics'];
            if (!is_array($state) || count($state) !== 7 || array_diff($keys, array_keys($state)) || $state['version'] !== 1 || !is_string($state['user']) || strlen($state['user']) > 256 || str_contains($state['user'], "\0") || !is_string($state['generation']) || !preg_match('/^[0-9a-f]{16}$/D', $state['generation']) || $state['acknowledged'] !== $state['generation'] || $state['phase'] !== 'disarmed' || $state['journal'] !== [] || !is_array($state['diagnostics'])) refuse("$path does not prove an idle drain with no obligations; preserve it for manual review");
            foreach ($state['diagnostics'] as $line) if (!is_string($line) || strlen($line) > 1024) refuse("$path has invalid diagnostics");
        } elseif ($name === '.private-root-key' || $name === '.private-root-key-state') {
            $stat = metadata($path, false, true);
            $expected = $name === '.private-root-key' ? 64 : 2;
            if ($stat['size'] !== $expected) refuse("$path has invalid control data size");
            $text = @file_get_contents($path);
            if ($name === '.private-root-key') { if (!is_string($text) || !preg_match('/^[0-9a-f]{64}$/D', $text)) refuse("$path has invalid key bytes"); $key = true; }
            else { if ($text !== 'v1') refuse("$path has invalid key state"); $keyState = true; }
        } else refuse("$path is pending or unexpected data; preserve it for manual review");
        $removable[] = $path;
    }
    if ($key !== $keyState) refuse("$queue has an incomplete private key pair; preserve it for manual review");
    return $removable;
}
metadata('/config', true);
metadata('/config/rutorrent', true);
$legacy = [];
$protected = [];
$share = '/config/rutorrent/share';
if (@lstat($share) !== false) {
    metadata($share, true);
    $settings = [$share.'/settings'];
    $users = $share.'/users';
    if (@lstat($users) !== false) {
        metadata($users, true);
        foreach (entries($users) as $user) {
            if ($user === '.gitignore') { metadata($users.'/'.$user); continue; }
            metadata($users.'/'.$user, true);
            $settings[] = $users.'/'.$user.'/settings';
        }
    }
    foreach ($settings as $path) {
        if (@lstat($path) === false) continue;
        metadata($path, true);
        $queue = $path.'/erasedata';
        if (@lstat($queue) !== false) $legacy[$queue] = idleQueue($queue);
    }
}
$jobs = '/config/rutorrent/erasedata-jobs';
if (@lstat($jobs) !== false) {
    metadata($jobs, true, true);
    foreach (entries($jobs) as $profile) $protected[$jobs.'/'.$profile] = idleQueue($jobs.'/'.$profile);
}
$witness = '/config/rutorrent/.erasedata-first-use';
$oldWitness = @lstat($witness);
if ($oldWitness !== false) {
    metadata($witness, false, true);
    if ($oldWitness['size'] !== 0) refuse('existing first-use witness is nonempty');
}
// Validation precedes mutation. Protected keys and state survive witness repair;
// only idle legacy controls are removed when retiring their old queue.
foreach ($legacy as $queue => $files) {
    foreach ($files as $file) if (!@unlink($file)) refuse("cannot retire idle control $file");
    if (!@rmdir($queue)) refuse("cannot remove idle legacy queue $queue");
}
if (@lstat($jobs) === false && !@mkdir($jobs, 0700)) refuse('cannot create erasedata-jobs');
metadata($jobs, true, true);
if (!@chown($jobs, $uid) || !@chgrp($jobs, $gid) || !@chmod($jobs, 0700)) refuse('cannot set erasedata-jobs ownership and mode');
if ($oldWitness !== false && !@unlink($witness)) refuse('cannot replace clean existing first-use witness');
umask(0077);
$handle = @fopen($witness, 'x');
if ($handle === false) refuse('cannot exclusively create first-use witness');
fclose($handle);
if (!@chown($witness, 0) || !@chgrp($witness, 0) || !@chmod($witness, 0400)) { @unlink($witness); refuse('cannot set first-use witness ownership and mode'); }
fwrite(STDOUT, "Attested idle erasedata queues; protected controls retained, witness is root:root 0400. Restart the application container.\n");
