<?php
// Root-owned startup operations. Never include operator PHP or follow volume symlinks.
declare(strict_types=1);
function message(string $text): string { return preg_replace('/[\x00-\x1f\x7f]/', '?', $text) ?? 'invalid path'; }
function refuse(string $domain, string $reason): never {
    fwrite(STDERR, 'Cannot attest '.$domain.': '.message($reason)."\n");
    exit(78);
}
function warning(string $reason): void { fwrite(STDERR, 'Warning: '.message($reason)."\n"); }
function exists(string $path): bool { return file_exists($path) || is_link($path); }
function metadata(string $path, string $domain): array {
    clearstatcache(true, $path);
    $st = @lstat($path);
    if ($st === false) refuse($domain, "$path cannot be inspected");
    return $st;
}
function attest(string $path, string $domain, int $uid, string $rule): array {
    $st = metadata($path, $domain);
    $type = $st['mode'] & 0170000;
    $mode = $st['mode'] & 07777;
    $file = in_array($rule, ['file', 'private-file', 'witness'], true);
    if ($type !== ($file ? 0100000 : 0040000)) refuse($domain, "$path is not a ".($file ? 'regular file' : 'directory'));
    if ($st['uid'] !== 0 && $st['uid'] !== $uid) refuse($domain, "$path is writable or foreign-owned; repair ownership while the container is stopped");
    $safe = match ($rule) {
        'ancestor' => ($mode & 0022) === 0 || ($mode & 01000) !== 0,
        'parent' => ($mode & 0022) === 0,
        'session' => ($mode & 0022) === 0 && ($mode & 04000) === 0,
        'journal-dir' => ($mode & 04777) === 0700,
        'private-dir' => $mode === 0700,
        'private-file' => $mode === 0600 && $st['nlink'] === 1,
        'file' => ($mode & 0022) === 0 && $st['nlink'] === 1,
        'witness' => $st['uid'] === 0 && $mode === 0400 && $st['nlink'] === 1 && $st['size'] === 0,
        default => false,
    };
    if (!$safe) refuse($domain, "$path has unsafe ownership or mode; repair permissions while stopped (session directories also require chmod go-w)");
    return $st;
}
function makeDir(string $path, int $mode = 0755): void {
    for ($parent = dirname($path); $parent !== '/'; $parent = dirname($parent)) {
        if (is_link($parent)) refuse('volume path', "$parent is a symlink ancestor; use a real directory");
    }
    if (exists($path)) {
        $st = metadata($path, 'volume path');
        if (($st['mode'] & 0170000) !== 0040000) refuse('volume path', "$path is not a directory");
        return;
    }
    if (!is_dir(dirname($path))) makeDir(dirname($path));
    if (!@mkdir($path, $mode) || !@chmod($path, $mode)) refuse('volume path', "cannot create $path");
}
function setOwner(string $path, int $uid, int $gid, bool $required = true): void {
    $st = metadata($path, 'ownership');
    if ($st['uid'] === $uid && $st['gid'] === $gid) return;
    // lchown also handles regular files and cannot change a symlink target.
    $ok = @lchown($path, $uid) && @lchgrp($path, $gid);
    if (!$ok) {
        if ($required) refuse('ownership', "cannot assign $path to $uid:$gid; check mount permissions");
        warning("leaving ownership of $path unchanged (read-only or restricted mount); the service must already have access");
    }
}
function walk(string $path, callable $visit): void {
    $st = metadata($path, 'volume path');
    if ($visit($path, $st) === false || ($st['mode'] & 0170000) !== 0040000) return;
    $entries = @scandir($path);
    if ($entries === false) refuse('volume path', "$path cannot be inspected");
    foreach ($entries as $entry) if ($entry !== '.' && $entry !== '..') walk($path.'/'.$entry, $visit);
}
function copyTree(string $source, string $target): void {
    $st = metadata($source, 'image defaults');
    if ($st['uid'] !== 0 || ($st['mode'] & 0022) !== 0 || is_link($source)) refuse('image defaults', "$source is not immutable root-owned data");
    if (($st['mode'] & 0170000) === 0040000) {
        makeDir($target);
        foreach (scandir($source) ?: [] as $entry) if ($entry !== '.' && $entry !== '..') copyTree($source.'/'.$entry, $target.'/'.$entry);
    } elseif (($st['mode'] & 0170000) === 0100000) {
        if (!@copy($source, $target) || !@chmod($target, $st['mode'] & 0777)) refuse('image defaults', "cannot copy $source");
    } else refuse('image defaults', "$source is not a regular file or directory");
}
function linkTo(string $source, string $target): void {
    if (is_link($target)) {
        if (readlink($target) === $source) return;
        unlink($target);
    } elseif (exists($target)) {
        // Only image paths are removed; never remove a persisted operator path.
        $paths = [];
        walk($target, static function ($p) use (&$paths) { $paths[] = $p; });
        foreach (array_reverse($paths) as $p) if (is_dir($p) && !is_link($p)) rmdir($p); else unlink($p);
    }
    if (!@symlink($source, $target)) refuse('configuration', "cannot link $target");
}
function rcPath(string $rc, string $command): string {
    $text = @file_get_contents($rc);
    if ($text === false) refuse('configuration', "$rc cannot be read");
    $aliases = $command === 'session.path.set' ? '(?:session\.path\.set|session|session\.set)' : '(?:directory\.default\.set|directory|directory\.set)';
    preg_match_all('/^\h*'.$aliases.'\h*=\h*(.*?)\h*$/m', $text, $matches);
    if (count($matches[1]) !== 1) refuse('configuration', "$command must have exactly one absolute literal assignment in $rc");
    $value = $matches[1][0];
    if (str_starts_with($value, '"')) {
        if (!preg_match('/^"((?:[^"\\\\]|\\\\["\\\\])*)"\h*(?:#.*)?$/D', $value, $quoted)) refuse('configuration', "$command must use a literal path");
        $value = preg_replace('/\\\\(["\\\\])/', '$1', $quoted[1]);
    } else {
        $value = preg_replace('/\h+#.*$/', '', $value);
        if (preg_match('/[\s,"\\\\{}]/', $value)) refuse('configuration', "$command must use a quoted literal path");
    }
    if (!is_string($value) || !str_starts_with($value, '/') || preg_match('/[\x00-\x1f\x7f"]/', $value) || preg_match('~(?:^|/)\.\.?(/|$)~', $value)) refuse('configuration', "$command must be an absolute literal path without traversal or control characters");
    $value = preg_replace('~/+~', '/', $value);
    $value = rtrim($value, '/') ?: '/';
    if ($value === '/' || ($command === 'session.path.set' && $value === '/data') || preg_match('~^/(?:etc|usr|bin|sbin|lib|proc|sys|dev|root|run|var|home|config|rutorrent|filebot)(?:/|$)~', $value)) refuse('configuration', "$command points into protected image or system configuration; use a data directory");
    return $value;
}
$uid = (int)getenv('UID');
$gidText = getenv('GID');
$group = ctype_digit($gidText) ? null : posix_getgrnam($gidText);
$gid = ctype_digit($gidText) ? (int)$gidText : (int)($group['gid'] ?? -1);
if ($gid < 0) refuse('identity', 'GID group does not exist');
$command = $argv[1] ?? '';
if ($command === 'config') {
    attest('/config', 'erasedata queue root', $uid, 'parent');
    $new = !exists('/config/rutorrent');
    if ($new) makeDir('/config/rutorrent', 0755);
    attest('/config/rutorrent', 'erasedata queue root', $uid, 'parent');
    $jobs = '/config/rutorrent/erasedata-jobs';
    $witness = '/config/rutorrent/.erasedata-first-use';
    if ($new) {
        makeDir($jobs, 0700);
        $f = @fopen($witness, 'x');
        if ($f === false) refuse('erasedata queue root', "cannot create $witness; run attest-erasedata-queue after inspecting the profile");
        fclose($f);
        chmod($witness, 0400);
    }
    if (exists($jobs) || exists($witness)) {
        if (!exists($witness)) refuse('erasedata queue root', 'pre-existing sibling lacks first-use witness; run attest-erasedata-queue after inspecting legacy obligations');
        attest($witness, 'erasedata queue root', $uid, 'witness');
        if (!exists($jobs)) refuse('erasedata queue root', 'protected queue is missing; run attest-erasedata-queue after inspecting legacy obligations');
        attest($jobs, 'erasedata queue root', $uid, 'private-dir');
        // Existing queue content is admitted by the application, never laundered here.
        setOwner($jobs, $uid, $gid);
    } else warning('legacy erasedata profile has no protected queue; run attest-erasedata-queue --attest-clean-legacy-queue after inspecting pending operations');
    foreach (['/config/rtorrent', '/config/custom_plugins', '/config/custom_themes'] as $p) makeDir($p);
} elseif ($command === 'externalize') {
    $dist = '/rutorrent/conf-dist';
    attest($dist, 'image defaults', 0, 'parent');
    foreach (['conf', 'share'] as $name) {
        $persisted = '/config/rutorrent/'.$name;
        $app = '/rutorrent/app/'.$name;
        if (is_link($persisted)) refuse('configuration', "$persisted is an operator symlink; use a real directory");
        if (!exists($persisted)) {
            if ($name === 'conf') makeDir($persisted);
            elseif (is_dir($app) && !is_link($app)) copyTree($app, $persisted);
            else makeDir($persisted);
        }
        attest($persisted, 'configuration', $uid, 'parent');
        linkTo($persisted, $app);
    }
    // Sources must stay root-owned and symlink-free. Iterator names are never line parsed.
    walk($dist, static function ($source, $st) use ($dist, $uid) {
        if ($st['uid'] !== 0 || ($st['mode'] & 0022) !== 0 || is_link($source)) refuse('image defaults', "$source is not immutable root-owned data");
        $relative = substr($source, strlen($dist));
        if ($relative === '/xmlrpc_proxy.php') return false;
        $target = '/config/rutorrent/conf'.$relative;
        if (($st['mode'] & 0170000) === 0040000) {
            makeDir($target);
            attest($target, 'configuration', $uid, 'parent');
        } elseif (($st['mode'] & 0170000) === 0100000) {
            if (!exists($target)) {
                if (!copy($source, $target) || !chmod($target, $st['mode'] & 0777)) refuse('configuration', "cannot seed $target");
                fwrite(STDOUT, 'Seeded missing conf'.message($relative)." from image\n");
            }
        } else refuse('image defaults', "$source is not regular data");
        return true;
    });
    foreach (['settings', 'users', 'torrents'] as $name) makeDir('/config/rutorrent/share/'.$name);
} elseif ($command === 'paths') {
    $rc = '/config/rtorrent/.rtorrent.rc';
    attest($rc, 'configuration', $uid, 'file');
    fwrite(STDOUT, rcPath($rc, 'session.path.set')."\n".rcPath($rc, 'directory.default.set')."\n");
} elseif ($command === 'session') {
    $session = $argv[2];
    $legacy = file_exists('/etc/rtorrent-legacy');
    $validate = null;
    if (!$legacy) {
        $parents = [];
        for ($p = dirname($session); $p !== '/'; $p = dirname($p)) $parents[] = $p;
        foreach (array_reverse($parents) as $p) attest($p, 'stop intent', $uid, 'ancestor');
    }
    if (!$legacy && exists($session)) attest($session, 'stop intent', $uid, 'session');
    else makeDir($session);
    if (!$legacy) {
        attest($session, 'stop intent', $uid, 'session');
        $validate = static function ($p, $st) use ($session, $uid) {
            if ($p === $session) return true;
            $rel = substr($p, strlen($session) + 1);
            $head = explode('/', $rel)[0];
            $journal = in_array($head, ['.rutorrent-stop-intents', '.retrackers-migration', '.autotools-file-jobs'], true);
            $domain = $head === '.rutorrent-stop-intents' ? 'stop intent' : 'session';
            $directory = ($st['mode'] & 0170000) === 0040000;
            $rule = $journal ? ($rel === $head ? 'journal-dir' : 'private-file') : ($directory ? 'session' : 'file');
            // AutoTools fopen('c') leaves its empty advisory lock0644 under umask022.
            // Durable JSON jobs stay private; the lock must still be regular, owned and single-link.
            if ($rel === '.autotools-file-jobs/.lock') $rule = 'file';
            attest($p, $domain, $uid, $rule);
            return true;
        };
        walk($session, $validate);
    }
    // Recheck each entry at handoff too; newly created entries never bypass admission.
    walk($session, static function ($p, $st) use ($uid, $gid, $legacy, $validate) {
        if (!$legacy) $validate($p, $st);
        setOwner($p, $uid, $gid);
        return true;
    });
    if (exists($session.'/rtorrent.lock') && !@unlink($session.'/rtorrent.lock')) refuse('session', 'cannot remove the stale session lock');
} elseif ($command === 'ownership') {
    $session = $argv[2];
    $witness = exists('/config/rutorrent/.erasedata-first-use') ? metadata('/config/rutorrent/.erasedata-first-use', 'erasedata queue root') : null;
    $jobs = exists('/config/rutorrent/erasedata-jobs') ? metadata('/config/rutorrent/erasedata-jobs', 'erasedata queue root') : null;
    $sessionMeta = metadata($session, 'session');
    $visit = static function ($path, $st) use ($witness, $jobs, $sessionMeta, $uid, $gid) {
        foreach ([$witness, $jobs, $sessionMeta] as $protected) if ($protected !== null && $st['dev'] === $protected['dev'] && $st['ino'] === $protected['ino']) return false;
        setOwner($path, $uid, $gid, false);
        return true;
    };
    foreach (['/config', '/home/torrent', '/var/lib/nginx', '/var/log', '/run/nginx', '/run/php', '/run/rtorrent'] as $path) if (exists($path)) walk($path, $visit);
    if (getenv('CHECK_PERM_DATA') === 'true') walk('/data', $visit);
    foreach (['/data/.watch', getenv('DOWNLOAD_DIRECTORY')] as $path) {
        makeDir($path);
        setOwner($path, $uid, $gid, false);
    }
} elseif ($command === 'custom') {
    $manifest = '/run/rutorrent-custom-plugins.json';
    $old = is_file($manifest) ? json_decode(file_get_contents($manifest), true) : [];
    if (!is_array($old)) $old = [];
    foreach ($old as $name) {
        if (!is_string($name) || basename($name) !== $name || $name === '.' || $name === '..') refuse('custom plugins', 'invalid installation manifest');
        $target = '/rutorrent/app/plugins/'.$name;
        if (exists($target)) {
            $paths = [];
            walk($target, static function ($p) use (&$paths) { $paths[] = $p; });
            foreach (array_reverse($paths) as $p) if (is_dir($p) && !is_link($p)) rmdir($p); else unlink($p);
        }
    }
    $installed = [];
    foreach (scandir('/config/custom_plugins') ?: [] as $name) {
        if ($name === '.' || $name === '..') continue;
        $source = '/config/custom_plugins/'.$name;
        $target = '/rutorrent/app/plugins/'.$name;
        if (exists($target)) { warning("custom plugin $name conflicts with an image plugin; keeping image plugin"); continue; }
        if (is_link($source) || !is_dir($source)) { warning("custom plugin $name is not a real directory; skipped"); continue; }
        // Only the selected service UID reads custom source, including races on shared mounts.
        makeDir($target);
        setOwner($target, $uid, $gid);
        $proc = proc_open(['su-exec', $uid.':'.$gid, 'php85', __FILE__, 'custom-copy', $source, $target], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
        if (!is_resource($proc) || proc_close($proc) !== 0) refuse('custom plugins', "cannot install $name");
        $installed[] = $name;
    }
    file_put_contents($manifest, json_encode($installed, JSON_THROW_ON_ERROR));
    chmod($manifest, 0600);
    foreach (scandir('/config/custom_themes') ?: [] as $name) {
        if ($name === '.' || $name === '..') continue;
        $source = '/config/custom_themes/'.$name;
        $target = '/rutorrent/app/plugins/theme/themes/'.$name;
        if (!is_dir($source)) { warning("custom theme $name is missing or is not a directory; skipped"); continue; }
        if (is_link($target) && readlink($target) === $source) continue;
        if (exists($target)) { warning("custom theme $name conflicts with an image theme; keeping image theme"); continue; }
        if (!symlink($source, $target)) refuse('custom themes', "cannot install $name");
    }
} elseif ($command === 'custom-copy') {
    $source = $argv[2];
    $target = $argv[3];
    walk($source, static function ($p, $st) use ($source, $target) {
            $dest = $target.substr($p, strlen($source));
            if (is_link($p)) refuse('custom plugins', "$p is a symlink; use regular files");
            if (($st['mode'] & 0170000) === 0040000) makeDir($dest);
            elseif (($st['mode'] & 0170000) === 0100000) { if (!copy($p, $dest)) refuse('custom plugins', "cannot copy $p"); chmod($dest, $st['mode'] & 0777); }
            else refuse('custom plugins', "$p is not regular data");
            return true;
        });
} else { fwrite(STDERR, "Unknown startup operation\n"); exit(64); }
