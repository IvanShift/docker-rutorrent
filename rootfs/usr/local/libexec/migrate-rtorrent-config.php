<?php
// Literal command migration only: never run an operator's rTorrent commands as root.
declare(strict_types=1);

function rc_fail(string $label, int $line, string $message): never
{
    fwrite(STDERR, "$label:$line: $message\n");
    exit(1);
}

function rc_number(string $value): ?int
{
    $value = trim($value, " \t\r\"'");
    if (!preg_match('/^([0-9]+)([kKmMgGtT]?)$/D', $value, $match)) {
        return null; // Dynamic expressions belong to the daemon's interpreter.
    }
    $power = $match[2] === '' ? 0 : strpos('kmgt', strtolower($match[2])) + 1;
    $number = (float)$match[1] * (1024 ** $power);
    return $number > PHP_INT_MAX ? PHP_INT_MAX : (int)$number;
}

$file = $argv[1] ?? '';
$label = $argv[2] ?? $file;
$legacy = ($argv[3] ?? '') === 'legacy';
$phpPath = getenv('UPDATE_PHP_PATH') ?: '/usr/bin/php';
$source = file_get_contents($file);
if ($source === false) {
    rc_fail($label, 1, 'Unable to read rTorrent configuration');
}
$lines = explode("\n", $source);
$result = [];
$changed = 0;
$deleted = [];
$lastSetter = $lastAdjustment = -1;
$restoreFlush = false;
// These aliases were shipped by earlier images. Native-only aliases are not
// applied to a working 0.9.8 file, whose limits and encoding settings are valid.
$nativeAliases = [
    // Earlier image templates: networking defaults and scheduled shell hooks.
    'dht.port.set' => 'dht.override_port.set', 'dht_port' => 'dht.override_port.set',
    'execute2' => 'execute.nothrow', 'schedule2' => 'schedule', 'schedule_remove2' => 'schedule.remove',
    'port_range' => 'network.listen.port.range.set', 'network.port_range' => 'network.listen.port.range.set',
    'network.port_range.set' => 'network.listen.port.range.set',
    'port_random' => 'network.listen.port.random.set', 'network.port_random' => 'network.listen.port.random.set',
    'network.port_random.set' => 'network.listen.port.random.set',
    // Operator rc files can still use these obsolete daemon command names.
    'dht' => 'dht.mode.set', 'bind' => 'network.bind_address.set', 'ip' => 'network.local_address.set',
    'scgi_local' => 'network.scgi.open_local', 'scgi_port' => 'network.scgi.open_port',
    'key_layout' => 'keys.layout.set', 'torrent_list_layout' => 'ui.torrent_list.layout.set',
    'network.max_open_sockets.set' => 'system.sockets.max_size.set',
    'network.max_open_files.set' => 'system.sockets.files.max_alloc.set',
    'network.http.max_total_connections.set' => 'system.sockets.http.max_alloc.set',
    'network.http.max_open.set' => 'system.sockets.http.max_alloc.set',
    'network.http.max_open' => 'system.sockets.http.max_alloc.set',
    'max_memory_usage' => 'pieces.memory.max.set',
];
$legacyAliases = [
    'network.listen.port.range.set' => 'port_range', 'network.listen.port.random.set' => 'port_random',
    'dht.override_port.set' => 'dht_port', 'dht.port.set' => 'dht_port',
    'schedule.remove' => 'schedule_remove2',
    'system.sockets.max_size.set' => 'network.max_open_sockets.set',
    'system.sockets.files.max_alloc.set' => 'network.max_open_files.set',
    'system.sockets.http.max_alloc.set' => 'network.http.max_open.set',
];
foreach ($lines as $index => $originalLine) {
    $line = rtrim($originalLine, "\r");
    $cr = str_ends_with($originalLine, "\r") ? "\r" : '';
    if (!$legacy && str_starts_with($line, '# docker-rutorrent: native ')) {
        $line = substr($line, strlen('# docker-rutorrent: native '));
    }
    if (!$legacy && $line === '# docker-rutorrent: legacy log.open_file.flush') {
        $restoreFlush = true;
        ++$changed;
        continue;
    }
    if (!preg_match('/^([ \t]*)([a-zA-Z_][a-zA-Z_0-9.]*)([ \t]*)=([ \t]*)(.*)$/D', $line, $match)) {
        $result[] = $line . $cr;
        continue;
    }
    [$all, $indent, $command, $beforeEquals, $afterEquals, $value] = $match;
    $newCommand = ($legacy ? $legacyAliases : $nativeAliases)[$command] ?? $command;
    if (!$legacy && preg_match('/^(encoding\.add|encoding_list|throttle\.ip|dht\.throttle\.name(?:\.set)?|trackers\.use_udp\.set)$/D', $command)) {
        $deleted[$command] = ($deleted[$command] ?? 0) + 1;
        ++$changed;
        continue;
    }
    if ($legacy && (in_array($command, ['network.prefer.ipv6.set', 'network.block.ipv6.set',
        'system.sockets.adjust_alloc'], true) || preg_match('/^system\.sockets\.[^.]+\.min_alloc\.set$/D', $command))) {
        $result[] = '# docker-rutorrent: native ' . $line . $cr;
        ++$changed;
        continue;
    }
    if ($legacy && str_starts_with($newCommand, 'system.sockets.')) {
        rc_fail($label, $index + 1, "Unsupported native socket command in legacy mode: $command");
    }
    if ($legacy && $command === 'log.open_file.flush') {
        $result[] = '# docker-rutorrent: legacy log.open_file.flush' . $cr;
        $newCommand = 'log.open_file';
    } elseif (!$legacy && $command === 'log.open_file' && $restoreFlush) {
        $newCommand = 'log.open_file.flush';
    } elseif (!$legacy && $line === 'log.open_file = "rtorrent", "/tmp/rtorrent.log"') {
        $newCommand = 'log.open_file.flush';
    }
    $restoreFlush = false;
    if ($legacy && $command === 'protocol.encryption.set' && trim($value) === 'allow') {
        $newCommand = 'encryption';
        $value = 'allow_incoming,try_outgoing,enable_retry';
    } elseif (!$legacy && in_array($command, ['encryption', 'protocol.encryption.set'], true)
        && preg_match('/^allow_incoming[ \t]*,[ \t]*try_outgoing[ \t]*,[ \t]*enable_retry[ \t]*$/D', $value)) {
        $newCommand = 'protocol.encryption.set';
        $value = 'allow';
    }
    if (!$legacy && in_array($command, ['proxy_address', 'network.proxy_address.set',
        'network.http.proxy_address.set'], true)) {
        $newCommand = $command === 'network.http.proxy_address.set' ? 'network.proxy.http.set' : 'network.proxy.global.set';
        $proxy = trim($value, " \t\"'");
        if ($proxy !== '' && !str_contains($proxy, '://')) {
            $proxy = 'http://' . $proxy;
        }
        // A fixed string is required here: the old command and new URL parser
        // have different syntax, so guessing at a dynamic expression is unsafe.
        if (strpbrk($proxy, "\r\n\0\"'{}(),$") !== false || str_contains($proxy, ' ')) {
            rc_fail($label, $index + 1, 'Proxy migration requires a literal host:port or proxy URL');
        }
        $value = $proxy;
    }
    $commandBody = in_array($newCommand, ['schedule', 'schedule2', 'system.method.set_key', 'method.set_key'], true);
    if (!$legacy && $commandBody) {
        // Nested aliases are commands at the beginning of a quoted schedule/
        // event body, or at the head of ((command,...)); prose arguments stay intact.
        $value = preg_replace('/("[ \t]*|\(\([ \t]*)(execute2|schedule_remove2|schedule2)(?=[ \t]*[=,])/',
            '$1__RC_COMMAND__$2', $value);
        $value = str_replace(['__RC_COMMAND__execute2', '__RC_COMMAND__schedule_remove2', '__RC_COMMAND__schedule2'],
            ['execute.nothrow', 'schedule.remove', 'schedule'], $value);
    } elseif ($legacy && $commandBody) {
        $value = preg_replace('/("[ \t]*|\(\([ \t]*)schedule\.remove(?=[ \t]*[=,])/', '$1schedule_remove2', $value);
    }
    if (in_array($command, ['system.method.set_key', 'method.set_key'], true)) {
        $value = str_replace('/usr/local/bin/postdl,$d.directory=', '/usr/local/bin/postdl,$d.base_path=', $value);
    }
    $launch = preg_match('/^execute(?:\.[a-z_]+)*$/D', $newCommand) && !preg_match('/^echo[ \t]*,/', $value);
    if ($commandBody || $launch) {
        $value = preg_replace_callback('~(?<![a-zA-Z0-9_./-])/usr/bin/php[0-9]*(?![a-zA-Z0-9_./-])~',
            static function (array $match) use ($phpPath): string {
                return $match[0] === '/usr/bin/php' || !is_executable($match[0]) ? $phpPath : $match[0];
            }, $value);
    }
    if (!$legacy && $newCommand === 'system.sockets.max_size.set') {
        $number = rc_number($value);
        if ($number !== null && $number < 512) {
            rc_fail($label, $index + 1, 'Socket limit must be at least 512');
        }
    }
    if (!$legacy && $newCommand === 'pieces.memory.max.set') {
        $number = rc_number($value);
        if ($number !== null && $number < 512 * 1024 * 1024) {
            rc_fail($label, $index + 1, 'Memory limit must be at least 512 MiB');
        }
    }
    if (!$legacy && preg_match('/^system\.sockets\.([^.]+)\.(min_alloc|max_alloc)\.set$/D', $newCommand, $socketMatch)) {
        $number = rc_number($value);
        $minimum = ['files' => 4, 'http' => 4][$socketMatch[1]] ?? null;
        if ($minimum !== null && $number !== null && $number !== 0 && $number < $minimum) {
            rc_fail($label, $index + 1, "Socket allocation for {$socketMatch[1]} must be at least $minimum");
        }
        $lastSetter = count($result);
    }
    if (!$legacy && $newCommand === 'system.sockets.adjust_alloc') {
        $lastAdjustment = count($result);
    }
    // Migrate only exact old image defaults; custom event names/commands remain.
    if ($newCommand === 'schedule' && $value === 'espace_disque_insuffisant,15,60,close_low_diskspace=1000M') {
        $value = 'low_diskspace,15,60,close_low_diskspace=1000M';
    }
    if ($newCommand === 'schedule' && $value === 'untied_directory,1,10,"stop_untied=/data/.watch/*.torrent"') {
        $value = 'untied_directory,1,10,"stop_untied="';
    }
    if ($newCommand === 'schedule' && $value === 'chmod_scgi_socket,0,0,"execute.nothrow=chmod,660,/run/rtorrent/rtorrent.sock"') {
        $value = 'chmod_scgi_socket,0,0,"execute.nothrow=chmod,600,/run/rtorrent/rtorrent.sock"';
    }
    if ($newCommand === 'log.add_output' && $value === '"critical", "rtorrent"') {
        $deleted[$newCommand . ': critical default'] = 1;
        ++$changed;
        continue;
    }
    $line = $newCommand !== $command || $value !== $match[5]
        ? $indent . $newCommand . ' = ' . $value
        : $line;
    if ($line . $cr !== $originalLine) {
        ++$changed;
    }
    $result[] = $line . $cr;
}
if (!$legacy && $lastSetter > $lastAdjustment) {
    // Keep valid operator recalculations. Add one only after the final setter.
    if (end($result) === '') {
        array_pop($result);
    }
    $result[] = '';
    $result[] = '# Recalculate socket allocations after per-category limits.';
    $result[] = 'system.sockets.adjust_alloc =';
    $result[] = '';
    ++$changed;
}
$transformed = implode("\n", $result);
if (file_put_contents($file, $transformed) !== strlen($transformed)) {
    rc_fail($label, 1, 'Unable to write complete transformed rTorrent configuration');
}
if ($changed > 0) {
    fwrite(STDOUT, "Migrated $label: $changed changed lines" . ($deleted === [] ? '' : '; removed ' . implode(', ', array_keys($deleted))) . "\n");
}
