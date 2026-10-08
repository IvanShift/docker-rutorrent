<?php
// Explicit offline maintenance: inspect data only; never load operator PHP.
// Startup's root-owned admission library supplies the one service identity
// parser and the one first-use witness rule; no startup operation runs here.
define('RUTORRENT_STARTUP_LIBRARY', true);
require '/usr/local/lib/rutorrent/startup.php';
function queueRefusal(string $message): never { refuse('erasedata queue root', $message); }
$uid = serviceUid(getenv('UID'));
$gid = serviceGid(getenv('GID'));
if ($uid === null || $gid === null) queueRefusal('UID must be 1..4294967294 and GID an existing group or numeric ID 0..4294967294');
$checked = 0;
function entries(string $path): array {
    global $checked;
    $names = @scandir($path);
    if ($names === false || count($names) > 100002 || ($checked += count($names)) > 100000) queueRefusal("$path cannot be inspected within the 100000 entry limit");
    return array_values(array_diff($names, ['.', '..']));
}
function queueMetadata(string $path, bool $directory = false, bool $private = false, bool $allowCapturedStateMode = false): array {
    global $uid;
    clearstatcache(true, $path);
    $stat = @lstat($path);
    $safePermissions = $stat !== false && (($stat['mode'] & ($private ? 0077 : 0022)) === 0 || ($allowCapturedStateMode && ($stat['mode'] & 07777) === 0666));
    // Name the owner commands only for an owner the permissions command cannot fix.
    $foreign = $stat !== false && !in_array($stat['uid'], [0, $uid], true);
    if ($stat === false || ($stat['mode'] & 0170000) !== ($directory ? 0040000 : 0100000) || $foreign || (!$directory && $stat['nlink'] !== 1) || !$safePermissions) queueRefusal("$path must be an owned ".($directory ? 'directory' : 'single-link regular file')." with safe permissions; ".repairHint('erasedata queue root', 'queue', 'repairable', $foreign));
    return $stat;
}
// The captured V-8 queue is idle but contains these control files. Presence
// never establishes idleness: validate every entry and the drain-state body.
function idleQueue(string $queue): array {
    global $uid;
    queueMetadata($queue, true, true);
    $removable = [];
    $key = $keyState = false;
    foreach (entries($queue) as $name) {
        $path = $queue.'/'.$name;
        if (in_array($name, ['.drain-state.lock', '.drain-worker.lock', 'scheduler.lock'], true) || preg_match('/^[0-9A-Fa-f]{40}\.lock$/D', $name)) {
            clearstatcache(true, $path);
            $stat = @lstat($path);
            // Historical fopen('c') locks can be 0666; they are empty data,
            // not obligations, and the private offline queue protects them.
            if ($stat === false || ($stat['mode'] & 0170000) !== 0100000 || !in_array($stat['uid'], [0, $uid], true) || $stat['nlink'] !== 1 || $stat['size'] !== 0) queueRefusal("$path is not an empty owned single-link lock");
        } elseif ($name === '.drain-state') {
            // The real writer chmods this data file to 0666. Its already-attested
            // private queue confines it; owner, link and idle-schema checks remain.
            $stat = queueMetadata($path, false, true, true);
            if ($stat['size'] > 16384) queueRefusal("$path exceeds the idle state limit");
            $state = json_decode(@file_get_contents($path), true);
            $keys = ['version','user','generation','acknowledged','phase','journal','diagnostics'];
            if (!is_array($state) || count($state) !== 7 || array_diff($keys, array_keys($state)) || $state['version'] !== 1 || !is_string($state['user']) || strlen($state['user']) > 256 || str_contains($state['user'], "\0") || !is_string($state['generation']) || !preg_match('/^[0-9a-f]{16}$/D', $state['generation']) || $state['acknowledged'] !== $state['generation'] || $state['phase'] !== 'disarmed' || $state['journal'] !== [] || !is_array($state['diagnostics'])) queueRefusal("$path does not prove an idle drain with no obligations; preserve it for manual review");
            foreach ($state['diagnostics'] as $line) if (!is_string($line) || strlen($line) > 1024) queueRefusal("$path has invalid diagnostics");
        } elseif ($name === '.private-root-key' || $name === '.private-root-key-state') {
            $stat = queueMetadata($path, false, true);
            $expected = $name === '.private-root-key' ? 64 : 2;
            if ($stat['size'] !== $expected) queueRefusal("$path has invalid control data size");
            $text = @file_get_contents($path);
            if ($name === '.private-root-key') { if (!is_string($text) || !preg_match('/^[0-9a-f]{64}$/D', $text)) queueRefusal("$path has invalid key bytes"); $key = true; }
            else { if ($text !== 'v1') queueRefusal("$path has invalid key state"); $keyState = true; }
        } else queueRefusal("$path is pending or unexpected data; preserve it for manual review");
        $removable[] = $path;
    }
    if ($key !== $keyState) queueRefusal("$queue has an incomplete private key pair; preserve it for manual review");
    return $removable;
}
queueMetadata('/config', true);
queueMetadata('/config/rutorrent', true);
$legacy = [];
$protected = [];
$share = '/config/rutorrent/share';
if (@lstat($share) !== false) {
    queueMetadata($share, true);
    $settings = [$share.'/settings'];
    $users = $share.'/users';
    if (@lstat($users) !== false) {
        queueMetadata($users, true);
        foreach (entries($users) as $user) {
            if ($user === '.gitignore') { queueMetadata($users.'/'.$user); continue; }
            queueMetadata($users.'/'.$user, true);
            $settings[] = $users.'/'.$user.'/settings';
        }
    }
    foreach ($settings as $path) {
        if (@lstat($path) === false) continue;
        queueMetadata($path, true);
        $queue = $path.'/erasedata';
        if (@lstat($queue) !== false) $legacy[$queue] = idleQueue($queue);
    }
}
$jobs = '/config/rutorrent/erasedata-jobs';
if (@lstat($jobs) !== false) {
    queueMetadata($jobs, true, true);
    foreach (entries($jobs) as $profile) $protected[$jobs.'/'.$profile] = idleQueue($jobs.'/'.$profile);
}
$witness = '/config/rutorrent/.erasedata-first-use';
clearstatcache(true, $witness);
$oldWitness = @lstat($witness);
// Startup's rule decides what this command may replace; its refusal names the
// same manual inspection, so neither side advertises a repair the other refuses.
if (witnessState($oldWitness, $uid) === 'manual') {
    $type = match ($oldWitness['mode'] & 0170000) { 0100000 => 'file', 0040000 => 'directory', 0120000 => 'symlink', default => 'other' };
    queueRefusal("$witness requires manual review (type=$type, owner=".$oldWitness['uid'].':'.$oldWitness['gid'].', mode='.sprintf('%04o', $oldWitness['mode'] & 07777).', links='.$oldWitness['nlink'].', size='.$oldWitness['size'].'); inspect it and pending queues while stopped, remove that entry only if both are trusted, then rerun this command');
}
// Validation precedes mutation. Protected keys and state survive witness repair;
// only idle legacy controls are removed when retiring their old queue.
foreach ($legacy as $queue => $files) {
    foreach ($files as $file) if (!@unlink($file)) queueRefusal("cannot retire idle control $file");
    if (!@rmdir($queue)) queueRefusal("cannot remove idle legacy queue $queue");
}
if (@lstat($jobs) === false && !@mkdir($jobs, 0700)) queueRefusal('cannot create erasedata-jobs');
queueMetadata($jobs, true, true);
if (!@chown($jobs, $uid) || !@chgrp($jobs, $gid) || !@chmod($jobs, 0700)) queueRefusal('cannot set erasedata-jobs ownership and mode');
if ($oldWitness !== false && !@unlink($witness)) queueRefusal('cannot replace clean existing first-use witness');
if (!createWitness($witness)) queueRefusal('cannot exclusively create the root:root 0400 first-use witness');
fwrite(STDOUT, "Attested idle erasedata queues; protected controls retained, witness is root:root 0400. Restart the application container.\n");
