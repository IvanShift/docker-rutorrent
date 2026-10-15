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

function rc_nested_aliases(string $value, array $aliases): string
{
    // An escaped quote inside a quoted rc body introduces an argument literal,
    // not a second command body. Semicolons in that literal are prose too.
    return preg_replace_callback('/("[ \t]*|;[ \t]*|\(\([ \t]*)(execute2|schedule_remove2|schedule2)(?=[ \t]*[=,])/',
        static function (array $match) use ($value, $aliases): string {
            $prefix = substr($value, 0, $match[2][1]);
            $outer = $inner = null;
            for ($i = 0, $length = strlen($prefix); $i < $length; ++$i) {
                $char = $prefix[$i];
                if ($char === '\\' && $i + 1 < $length) {
                    $next = $prefix[++$i];
                    if ($outer !== null && ($next === '"' || $next === "'")) {
                        if ($inner === $next) {
                            $inner = null;
                        } elseif ($inner === null) {
                            $inner = $next;
                        }
                    }
                    continue;
                }
                if ($outer === null && ($char === '"' || $char === "'")) {
                    $outer = $char;
                } elseif ($inner === null && $char === $outer) {
                    $outer = null;
                } elseif ($outer !== null && $char === "'") {
                    $inner = $inner === "'" ? null : ($inner ?? "'");
                }
            }
            return $inner !== null ? $match[0][0] : $match[1][0] . $aliases[$match[2][0]];
        }, $value, -1, $count, PREG_OFFSET_CAPTURE);
}

// The one definition of the image's early shutdown save hook: the template
// carries no copy, so a new rc receives it here exactly like an existing one.
const RC_EARLY_SAVE_KEY = 'docker_rutorrent_early_save';
const RC_EARLY_SAVE_COMMAND = '"session.save="';

// libtorrent a755a7a removed the cascading log groups, and an unknown group
// stops rTorrent at startup. Each name removed since 8f042ad6 maps to the
// non-cascading groups that now carry what its log.cc cascade delivered there:
// every more severe level too, storage_error as well for warn, notice, info and
// debug, and storage_info for debug (storage_critical had no callers).
// LOG_STORAGE moved to storage_errors; __non_cascading__ was a sentinel without
// messages. storage_debug is in both lists, so an old-style use cannot be told
// apart and stays as written.
const RC_LOG_GROUPS_NATIVE = [
    'critical' => ['errors'], 'error' => ['errors'],
    'warn' => ['errors', 'storage_errors'], 'notice' => ['errors', 'storage_errors'],
    'info' => ['errors', 'events', 'storage_errors'],
    'debug' => ['errors', 'events', 'debug_trace', 'storage_errors', 'storage_events'],
    'storage_critical' => ['storage_errors'], 'storage_error' => ['storage_errors'],
    'storage_warn' => ['storage_errors'], 'storage_notice' => ['storage_errors'],
    'storage_info' => ['storage_errors', 'storage_events'], 'storage' => ['storage_errors'],
    '__non_cascading__' => [],
];
// rTorrent 0.9.8 (libtorrent 0.13.8) still has the cascading names. Each new
// group returns to the cascading group of the same severity, so the image
// default "error" survives a native/legacy round trip unchanged.
const RC_LOG_GROUPS_LEGACY = [
    'errors' => ['error'], 'events' => ['info'], 'debug_trace' => ['debug'],
    'storage_errors' => ['storage_error'], 'storage_events' => ['storage_info'],
];

/**
 * Split a log.add_output value into its literal group name (quoted or bare) and
 * the text around it; null unless the group is such a literal. The output name
 * is null unless it is a lone literal argument.
 */
function rc_log_output(string $value): ?array
{
    if (!preg_match('/^([ \t]*)("?)([a-z_]+)\2(?=[ \t]*(?:,|$))(.*)$/D', $value, $match)) {
        return null; // Dynamic or escaped group expressions are left to the daemon.
    }
    $output = preg_match('/^[ \t]*,[ \t]*(?:"([^"\\\\]*)"|([^\s,"\\\\]+))[ \t]*$/D', $match[4], $name)
        ? ($name[2] ?? '') . $name[1] : null;
    return ['indent' => $match[1], 'quote' => $match[2], 'group' => $match[3], 'rest' => $match[4], 'output' => $output];
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
$shutdownHooks = [];
$earlySaveOptOut = false;
$logOutputs = [];
// These aliases were shipped by earlier images. Native-only aliases are not
// applied to a working 0.9.8 file, whose limits and encoding settings are valid.
$nativeAliases = [
    'system.method.set_key' => 'method.set_key',
    'system.method.insert' => 'method.insert', 'system.method.set' => 'method.set',
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
    // An operator who comments out the image hook (any of its keys) opts out;
    // the line is kept and the hook is not added again.
    if (preg_match('/^[ \t]*#[# \t]*(?:system\.)?method\.set_key[ \t]*=[ \t]*event\.system\.shutdown[ \t]*,[ \t]*'
        . preg_quote(RC_EARLY_SAVE_KEY, '/') . '_*[ \t]*,/', $line)) {
        $earlySaveOptOut = true;
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
        'system.sockets.adjust_alloc', 'system.file_name.allow_legacy_utf8.set'], true)
        || preg_match('/^system\.sockets\.[^.]+\.min_alloc\.set$/D', $command))) {
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
    if (!$legacy && $command === 'system.file_name.allow_legacy_utf8.set') {
        // A catch keeps explicit operator settings safe on a 0.16.24 rollback.
        $body = str_replace(['\\', '"'], ['\\\\', '\\"'], 'system.file_name.allow_legacy_utf8.set=' . $value);
        $result[] = $indent . 'catch = {"' . $body . '"}' . $cr;
        ++$changed;
        continue;
    }
    $commandBody = in_array($newCommand, ['schedule', 'schedule2', 'system.method.set_key', 'method.set_key',
        'system.method.insert', 'method.insert', 'system.method.set', 'method.set'], true);
    if (!$legacy && $commandBody) {
        // Nested aliases are commands at the beginning of a quoted schedule/
        // event body, or at the head of ((command,...)); prose arguments stay intact.
        $value = rc_nested_aliases($value,
            ['execute2' => 'execute.nothrow', 'schedule_remove2' => 'schedule.remove', 'schedule2' => 'schedule']);
    } elseif ($legacy && $commandBody) {
        $value = preg_replace('/("[ \t]*|\(\([ \t]*)schedule\.remove(?=[ \t]*[=,])/', '$1schedule_remove2', $value);
    }
    if (in_array($newCommand, ['system.method.set_key', 'method.set_key'], true)) {
        if (preg_match('/^event\.system\.shutdown[ \t]*,([^,]+),(.*)$/D', $value, $hook)) {
            $shutdownHooks[trim($hook[1], " \t\"'")] = trim($hook[2]);
        }
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
        // The pinned daemon permits small HTTP and minimum allocations; only
        // files.max_alloc has this floor, including a literal zero.
        $minimum = $socketMatch[1] === 'files' && $socketMatch[2] === 'max_alloc' ? 4 : null;
        if ($minimum !== null && $number !== null && $number < $minimum) {
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
    if ($newCommand === 'schedule' && in_array($value, ['untied_directory,1,10,"stop_untied=/data/.watch/*.torrent"',
        'untied_directory,1,10,stop_untied'], true)) {
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
    if ($newCommand === 'log.add_output' && ($logOutput = rc_log_output($value)) !== null) {
        // Only the exact first-argument name changes; the output name, any
        // trailing text and lines with other groups stay as written.
        $groupMap = $legacy ? RC_LOG_GROUPS_LEGACY : RC_LOG_GROUPS_NATIVE;
        $renamed = array_key_exists($logOutput['group'], $groupMap);
        $groups = $renamed ? $groupMap[$logOutput['group']] : [$logOutput['group']];
        $values = [];
        foreach ($groups as $group) {
            // A renamed line can expand to a group that an earlier line already
            // sends to the same output; repeating it would add nothing. Lines
            // that need no rename are never dropped.
            $key = $logOutput['output'] === null ? null : $group . "\0" . $logOutput['output'];
            if (!$renamed || $key === null || !isset($logOutputs[$key])) {
                $values[] = $logOutput['indent'] . $logOutput['quote'] . $group . $logOutput['quote'] . $logOutput['rest'];
            }
            if ($key !== null) {
                $logOutputs[$key] = true;
            }
        }
        if ($values === []) {
            $deleted[$newCommand . ($groups === [] ? ': ' . $logOutput['group'] : ': repeated group')] = 1;
            ++$changed;
            continue;
        }
        $value = array_pop($values);
        foreach ($values as $extra) {
            $result[] = $indent . $newCommand . ' = ' . $extra . $cr;
            ++$changed;
        }
    }
    $line = $newCommand !== $command || $value !== $match[5]
        ? $indent . $newCommand . ' = ' . $value
        : $line;
    if ($line . $cr !== $originalLine) {
        ++$changed;
    }
    $result[] = $line . $cr;
}
if (!$legacy) {
    // Captured 0.16.25 upgrade tests show UTF-8 aliases change persisted paths.
    // Put this before any load command; catch is also accepted by 0.16.24/0.9.8.
    $utf8Guard = 'catch = {"system.file_name.allow_legacy_utf8.set=0"}';
    $result = array_values(array_filter($result, static fn(string $line): bool => $line !== $utf8Guard));
    array_unshift($result, $utf8Guard);
    if (!str_starts_with($source, $utf8Guard . "\n")) {
        ++$changed;
    }
    // Inserting the first line moves both command indices equally.
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
// Save before stopped announces can consume the supervisor/Docker deadline.
// Existing operator hooks keep their keys and commands, including our key name.
if (!$earlySaveOptOut && !array_intersect(['"session.save="', "'session.save='", 'session.save='], $shutdownHooks)) {
    $key = RC_EARLY_SAVE_KEY;
    while (array_key_exists($key, $shutdownHooks)) $key .= '_';
    if (end($result) === '') array_pop($result);
    $result[] = 'method.set_key = event.system.shutdown,' . $key . ',' . RC_EARLY_SAVE_COMMAND;
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
