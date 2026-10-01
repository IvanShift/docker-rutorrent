<?php
// Explicit offline maintenance: do not follow links or interpret queue records.
function refuse(string $message): never {
    fwrite(STDERR, "Cannot attest erasedata queue root: $message\n");
    exit(78);
}
$uid = getenv('UID') === false ? '991' : getenv('UID');
$gid = getenv('GID') === false ? '991' : getenv('GID');
if (!ctype_digit($uid) || !ctype_digit($gid) || (int) $uid <= 0 || (int) $gid < 0 || strlen($uid) > 9 || strlen($gid) > 9) {
    refuse('UID:GID must be the selected positive numeric service UID and numeric GID');
}
$uid = (int) $uid;
$gid = (int) $gid;
$checked = 0;
$directory = static function (string $path, bool $private = false) use ($uid, &$checked): array {
    if (++$checked > 1000) {
        refuse('inspection exceeded 1000 entries; inspect the volume manually');
    }
    clearstatcache(true, $path);
    $stat = @lstat($path);
    if ($stat === false || ($stat['mode'] & 0170000) !== 0040000 || !in_array($stat['uid'], [0, $uid], true) || ($stat['mode'] & ($private ? 077 : 022)) !== 0) {
        refuse("$path must be an unlinked directory owned by root or UID without " . ($private ? 'group/other permissions' : 'group/other write permissions'));
    }
    return $stat;
};
$entries = static function (string $path): array {
    $names = @scandir($path);
    if ($names === false || count($names) > 1002) {
        refuse("$path cannot be inspected within the 1000 entry limit");
    }
    return array_values(array_diff($names, ['.', '..']));
};
$directory('/config');
$directory('/config/rutorrent');
$legacy = [];
$share = '/config/rutorrent/share';
if (@lstat($share) !== false) {
    $directory($share);
    $settings = [$share . '/settings'];
    $users = $share . '/users';
    if (@lstat($users) !== false) {
        $directory($users);
        foreach ($entries($users) as $user) {
            $directory($users . '/' . $user);
            $settings[] = $users . '/' . $user . '/settings';
        }
    }
    foreach ($settings as $path) {
        if (@lstat($path) === false) {
            continue;
        }
        $directory($path);
        $queue = $path . '/erasedata';
        if (@lstat($queue) === false) {
            continue;
        }
        $directory($queue, true);
        if ($entries($queue) !== []) {
            refuse("$queue is not empty; pending or unexpected entries need manual review");
        }
        $legacy[] = $queue;
    }
}
$jobs = '/config/rutorrent/erasedata-jobs';
if (@lstat($jobs) !== false) {
    $directory($jobs, true);
    foreach ($entries($jobs) as $profile) {
        $path = $jobs . '/' . $profile;
        $directory($path, true);
        if ($entries($path) !== []) {
            refuse("$path is not empty; existing jobs need manual review");
        }
    }
}
$witness = '/config/rutorrent/.erasedata-first-use';
$oldWitness = @lstat($witness);
if ($oldWitness !== false && (($oldWitness['mode'] & 0170000) !== 0100000 || !in_array($oldWitness['uid'], [0, $uid], true) || ($oldWitness['mode'] & 077) !== 0 || $oldWitness['nlink'] !== 1 || $oldWitness['size'] !== 0)) {
    refuse('existing first-use witness is linked, foreign-owned, nonempty, or has group/other permissions');
}
// Validation is complete before mutation. rmdir refuses any newly nonempty
// queue; exclusive witness creation never opens an existing file or symlink.
foreach ($legacy as $path) {
    if (!@rmdir($path)) {
        refuse("cannot remove empty legacy queue $path");
    }
}
if (@lstat($jobs) === false && !@mkdir($jobs, 0700)) {
    refuse('cannot create erasedata-jobs');
}
$directory($jobs, true);
if (!@chown($jobs, $uid) || !@chgrp($jobs, $gid) || !@chmod($jobs, 0700)) {
    refuse('cannot set erasedata-jobs ownership and mode');
}
if ($oldWitness !== false && !@unlink($witness)) {
    refuse('cannot replace clean existing first-use witness');
}
umask(0077);
$handle = @fopen($witness, 'x');
if ($handle === false) {
    refuse('cannot exclusively create first-use witness');
}
fclose($handle);
if (!@chown($witness, 0) || !@chgrp($witness, 0) || !@chmod($witness, 0400)) {
    @unlink($witness);
    refuse('cannot set first-use witness ownership and mode');
}
fwrite(STDOUT, "Attested empty erasedata queues; erasedata-jobs is UID:GID 0700 and its witness is root:root 0400. Restart the application container.\n");
