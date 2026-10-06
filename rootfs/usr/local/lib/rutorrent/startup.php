<?php
// Root-owned startup operations. Never include operator PHP or follow volume symlinks.
declare(strict_types=1);
// Remote/operator filenames must only reach the classified, escaped diagnostics.
ini_set('display_errors', '0');
ini_set('log_errors', '0');
function message(string $text): string { return preg_replace('/[\x00-\x1f\x7f]/', '?', $text) ?? 'invalid path'; }
function refuse(string $domain, string $reason): never {
    fwrite(STDERR, 'Cannot attest '.$domain.': '.message($reason)."\n");
    exit(78);
}
function warning(string $reason): void { fwrite(STDERR, 'Warning: '.message($reason)."\n"); }
function serviceMetadata(string $path): array|false {
    global $uid, $gid;
    if (!isset($uid, $gid) || $uid <= 0 || posix_geteuid() !== 0) return false;
    // Root-squashed storage can admit the service while refusing root traversal.
    // This child only returns lstat metadata; it never evaluates volume code.
    $code = '$st = @lstat($argv[1]); if ($st === false) exit(78); echo json_encode($st);';
    $proc = proc_open(['su-exec', $uid.':'.$gid, '/usr/bin/php85', '-r', $code, $path], [0 => ['file', '/dev/null', 'r'], 1 => ['pipe', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes);
    if (!is_resource($proc)) return false;
    $json = stream_get_contents($pipes[1], 16384); fclose($pipes[1]);
    $status = proc_close($proc);
    $st = $status === 0 ? json_decode($json ?: '', true) : null;
    return is_array($st) ? $st : false;
}
function exists(string $path): bool {
    clearstatcache(true, $path);
    return @lstat($path) !== false || serviceMetadata($path) !== false;
}
function metadata(string $path, string $domain): array {
    clearstatcache(true, $path);
    $st = @lstat($path);
    if ($st === false) $st = serviceMetadata($path);
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
    if (!$safe) refuse($domain, "$path has unsafe ownership or mode (rule=$rule, mode=".sprintf("%04o", $mode).", links=".$st['nlink'].", size=".$st['size']."); repair permissions while stopped (session directories also require chmod go-w)");
    return $st;
}
function makeDir(string $path, int $mode = 0755): void {
    for ($parent = dirname($path); $parent !== '/'; $parent = dirname($parent)) {
        if (exists($parent) && (metadata($parent, 'volume path')['mode'] & 0170000) === 0120000) refuse('volume path', "$parent is a symlink ancestor; use a real directory");
    }
    if (exists($path)) {
        $st = metadata($path, 'volume path');
        if (($st['mode'] & 0170000) !== 0040000) refuse('volume path', "$path is not a directory");
        return;
    }
    if (!exists(dirname($path))) makeDir(dirname($path));
    if (!@mkdir($path, $mode) || !@chmod($path, $mode)) refuse('volume path', "cannot create $path");
}
function ownTree(string $mode, string $path, int $ownerUid, int $ownerGid, array $options = []): void {
    global $uid, $gid;
    $proc = proc_open(['/usr/local/bin/rutorrent-own-tree', $mode, (string)$ownerUid, (string)$ownerGid, $path, '--access-uid', (string)$uid, '--access-gid', (string)$gid, ...$options], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
    if (!is_resource($proc) || proc_close($proc) !== 0) refuse('ownership', "$path descriptor-bound admission failed");
}
function setOwner(string $path, int $uid, int $gid, bool $required = true): void {
    ownTree($required ? 'single' : 'single-optional', $path, $uid, $gid);
}
function trustedFile(string $path, string $domain, int $uid): void {
    $parents = [];
    for ($p = dirname($path); $p !== '/'; $p = dirname($p)) $parents[] = $p;
    foreach (array_reverse($parents) as $p) attest($p, $domain, $uid, 'parent');
    attest($path, $domain, $uid, 'file');
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
    if (!str_starts_with($value, '/data/')) refuse('configuration', "$command must stay below /data; inspect and move custom storage while stopped");
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
    } else warning('legacy erasedata profile has no protected queue; run attest-erasedata-queue --attest-clean-legacy-queue after inspecting pending operations');
    // Validate persisted queue metadata before changing even its safe outer ancestor.
    // Keep the authentication subtree's outer ancestor out of service control.
    setOwner('/config', 0, $gid);
    // Existing queue content is admitted by the application, never laundered here.
    if (exists($jobs)) setOwner($jobs, $uid, $gid);
    foreach (['/config/rtorrent', '/config/custom_plugins', '/config/custom_themes'] as $p) {
        makeDir($p);
        attest($p, 'configuration', $uid, 'parent');
    }
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
    if (getenv('RU_FORBID_USER_SETTINGS') === 'true') {
        foreach (glob('/config/rutorrent/share/users/*/settings') ?: [] as $settings) {
            if (is_dir($settings) && count(@scandir($settings) ?: []) > 2) warning("existing per-user settings at $settings are preserved but the shared profile is active; inspect and merge them, or set RU_FORBID_USER_SETTINGS=false (retrackers needs the shared profile)");
        }
    }
} elseif ($command === 'host-policy') {
    $lines = ['map $host $rutorrent_host_allowed {', '    default '.(getenv('HTTP_AUTH') === 'false' ? '0' : '1').';', '    localhost 1;', '    ~^[0-9]+\\.[0-9]+\\.[0-9]+\\.[0-9]+$ 1;', '    ~^\\[[0-9a-fA-F:.]+\\]$ 1;'];
    $allowed = ['localhost' => true];
    foreach (explode(',', getenv('HTTP_ALLOWED_HOSTS') ?: '') as $name) {
        $name = strtolower(trim($name));
        if ($name === '') continue;
        if (in_array($name, ['default', 'include', 'hostnames', 'volatile'], true) || strlen($name) > 253 || !preg_match('/^(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)*$/D', $name)) refuse('HTTP host policy', 'HTTP_ALLOWED_HOSTS must contain exact DNS names separated by commas');
        if (!isset($allowed[$name])) $lines[] = '    '.$name.' 1;';
        $allowed[$name] = true;
    }
    $lines[] = '}';
    if (@file_put_contents('/etc/nginx/http-host-policy.conf', implode("\n", $lines)."\n") === false) refuse('HTTP host policy', 'cannot write host allowlist');
} elseif ($command === 'auth') {
    foreach (['/config', '/config/nginx', '/config/nginx/passwd'] as $path) {
        if ($path !== '/config') makeDir($path, 0750);
        $st = attest($path, 'HTTP authentication', $uid, 'parent');
        setOwner($path, 0, $gid);
        if ($path !== '/config' && ($st['mode'] & 07777) !== 0750 && !@chmod($path, 0750)) refuse('HTTP authentication', "cannot secure $path");
    }
    $path = '/config/nginx/passwd/rutorrent_passwd';
    if (exists($path)) {
        attest($path, 'HTTP authentication', $uid, 'file');
        setOwner($path, 0, $gid);
        if (!@chmod($path, 0640)) {
            $st = metadata($path, 'HTTP authentication');
            if (($st['mode'] & 07777) !== 0640) refuse('HTTP authentication', "read-only password mount $path must be root:GID 0640");
        }
    }
} elseif ($command === 'auth-check') {
    foreach (['/config', '/config/nginx', '/config/nginx/passwd'] as $path) {
        attest($path, 'HTTP authentication', 0, 'parent');
    }
    if (exists('/config/nginx/passwd/rutorrent_passwd')) attest('/config/nginx/passwd/rutorrent_passwd', 'HTTP authentication', 0, 'file');
} elseif ($command === 'paths') {
    $rc = '/config/rtorrent/.rtorrent.rc';
    attest($rc, 'configuration', $uid, 'file');
    fwrite(STDOUT, rcPath($rc, 'session.path.set')."\n".rcPath($rc, 'directory.default.set')."\n");
} elseif ($command === 'session') {
    $session = $argv[2];
    makeDir($session);
    // The helper validates and changes only pinned descriptors, including all
    // native private journal rules. Legacy bypass remains explicit.
    ownTree(file_exists('/etc/rtorrent-legacy') ? 'legacy' : 'session', $session, $uid, $gid);
} elseif ($command === 'ownership') {
    $session = $argv[2];
    $options = [];
    foreach (['/config/rutorrent/.erasedata-first-use', '/config/rutorrent/erasedata-jobs', $session, '/config/nginx'] as $path) {
        $options[] = '--exclude-path'; $options[] = $path;
        if (exists($path)) {
            $st = metadata($path, 'protected ownership');
            $options[] = '--exclude'; $options[] = $st['dev'].':'.$st['ino'];
        }
    }
    $configMeta = metadata('/config', 'configuration');
    foreach (['/config', '/home/torrent', '/var/lib/nginx', '/var/log', '/run/nginx', '/run/php', '/run/rtorrent'] as $path) {
        if (exists($path)) ownTree('shared', $path, $uid, $gid, [...$options, ...($path === '/config' ? ['--preserve-root'] : [])]);
    }
    if (getenv('CHECK_PERM_DATA') === 'true') {
        ownTree('shared', '/data', $uid, $gid, [...$options, '--exclude', $configMeta['dev'].':'.$configMeta['ino']]);
    }
    $working = ['/data/.watch', getenv('DOWNLOAD_DIRECTORY')];
    if (exists('/filebot/filebot.sh')) $working[] = '/data/media';
    foreach ($working as $path) {
        makeDir($path);
        setOwner($path, $uid, $gid, false);
        $proc = proc_open(['su-exec', $uid.':'.$gid, '/bin/sh', '-ec', 'test -d "$1" && test -r "$1" && test -w "$1" && test -x "$1"', 'working-access', $path], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
        if (!is_resource($proc) || proc_close($proc) !== 0) refuse('working directory', "$path must already be readable and writable by $uid:$gid on restricted mounts; pre-provision it while stopped");
    }
} elseif ($command === 'filebot') {
    foreach (['/config/filebot', '/config/filebot/data'] as $path) {
        makeDir($path);
        attest($path, 'FileBot data', $uid, 'parent');
    }
    $license = getenv('FILEBOT_LICENSE') ?: '';
    if ($license === '' || $license[0] !== '/' || preg_match('/[\x00-\x1f\x7f]/', $license)) refuse('FileBot license', "FILEBOT_LICENSE must name an absolute readable regular file; configured path=".message($license));
    trustedFile($license, 'FileBot license', $uid);
    foreach (['/config/filebot/data/.license', '/config/filebot/args_amc.txt', '/config/filebot/postdl'] as $path) {
        if (exists($path)) trustedFile($path, 'FileBot configuration', $uid);
    }
    // Persisted data belongs to the service before FileBot reads or writes it.
    ownTree('shared', '/config/filebot', $uid, $gid);
    makeDir('/data/media');
    setOwner('/data/media', $uid, $gid, false);
    // Compatibility for a pre-defaults image used in isolated startup fixtures.
    // Save only the immutable image source, before replacing its public link.
    if (!exists('/usr/local/share/rutorrent-defaults/args_amc.txt') && !exists('/filebot/args_amc.txt.default')) {
        trustedFile('/filebot/args_amc.txt', 'image FileBot defaults', 0);
        if (!@copy('/filebot/args_amc.txt', '/filebot/args_amc.txt.default') || !@chmod('/filebot/args_amc.txt.default', 0644)) refuse('image FileBot defaults', 'cannot preserve the immutable argument default');
    }
    linkTo('/config/filebot/data', '/filebot/data');
    linkTo('/config/filebot/args_amc.txt', '/filebot/args_amc.txt');
    $proc = proc_open(['su-exec', $uid.':'.$gid, '/usr/bin/php85', __FILE__, 'filebot-import'], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
    if (!is_resource($proc) || proc_close($proc) !== 0) refuse('FileBot configuration', 'service UID license import or argument seeding failed; check service access to the configured license and data directory');
} elseif ($command === 'filebot-import') {
    if (posix_geteuid() !== $uid || $uid === 0) refuse('FileBot configuration', 'license import requires the selected non-root UID');
    $license = getenv('FILEBOT_LICENSE') ?: '';
    trustedFile($license, 'FileBot license', $uid);
    if (!exists('/config/filebot/data/.license')) {
        $proc = proc_open(['/filebot/filebot.sh', '--license', $license], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
        if (!is_resource($proc) || proc_close($proc) !== 0) refuse('FileBot license', "license import failed for $license");
    }
    $args = '/config/filebot/args_amc.txt';
    if (!exists($args)) {
        $source = '/usr/local/share/rutorrent-defaults/args_amc.txt';
        if (!exists($source)) $source = '/filebot/args_amc.txt.default';
        trustedFile($source, 'image FileBot defaults', 0);
        // Exclusive creation is performed as the service, never root through a
        // directory that the service may concurrently replace.
        $input = @fopen($source, 'rb');
        $output = @fopen($args, 'x');
        if ($input === false || $output === false || stream_copy_to_stream($input, $output) === false) refuse('FileBot arguments', "cannot seed $args from image defaults");
        fclose($input); fclose($output);
        if (!@chmod($args, 0644)) refuse('FileBot arguments', "cannot normalize $args");
    }
} elseif ($command === 'filebot-hook') {
    // Restore the immutable dispatcher on every start. It checks the operator
    // hook as the service, so deleting that hook falls back without a stale link.
    linkTo('/usr/local/libexec/filebot-postdl', '/usr/local/bin/postdl');
    if (exists('/config/filebot/postdl')) {
        trustedFile('/config/filebot/postdl', 'FileBot hook', $uid);
        $proc = proc_open(['su-exec', $uid.':'.$gid, '/bin/sh', '-ec', 'test -x "$1" || chmod u+x "$1"', 'filebot-hook', '/config/filebot/postdl'], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
        if (!is_resource($proc) || proc_close($proc) !== 0) warning('FileBot hook /config/filebot/postdl is not executable by the service UID; run chmod u+x on the trusted hook while stopped');
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
        if (!mb_check_encoding($name, 'UTF-8')) { warning('custom plugin has an invalid UTF-8 name; skipped'); continue; }
        $installed[] = $name;
        if (@file_put_contents($manifest, json_encode($installed)) === false || !@chmod($manifest, 0600)) refuse('custom plugins', 'cannot persist installation manifest');
        // Only the selected service UID reads custom source, including races on shared mounts.
        makeDir($target);
        setOwner($target, $uid, $gid);
        $proc = proc_open(['su-exec', $uid.':'.$gid, '/usr/bin/php85', __FILE__, 'custom-copy', $source, $target], [0 => STDIN, 1 => STDOUT, 2 => STDERR], $pipes);
        if (!is_resource($proc) || proc_close($proc) !== 0) {
            $paths = [];
            walk($target, static function ($p) use (&$paths) { $paths[] = $p; });
            foreach (array_reverse($paths) as $p) if (is_dir($p) && !is_link($p)) @rmdir($p); else @unlink($p);
            warning("cannot install custom plugin $name; skipped until next restart"); continue;
        }
    }
    if (@file_put_contents($manifest, json_encode($installed)) === false || !@chmod($manifest, 0600)) refuse('custom plugins', 'cannot persist installation manifest');
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
