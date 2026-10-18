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
// startup asks for these in an image with FileBot. Both daemons accept them.
const RC_FILEBOT_HOOKS = [
    ['event.download.finished', 'filebot', '"execute.nothrow=/usr/local/bin/postdl,$d.base_path=,$d.name="'],
    ['event.download.erased', 'filebot_cleaner', '"execute.nothrow=/usr/local/bin/postrm"'],
];

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

/**
 * The logical lines that rTorrent's parse_command_file reads: an odd run of
 * backslashes ending a physical line is dropped and the next line follows
 * directly. Each part keeps its run for an exact copy. A final line still
 * continued at the end of the file is open: the daemon never runs it. A line
 * is long where the daemon's 4096-byte buffer refuses it (0.9.8 stops reading).
 */
function rc_logical_lines(string $source): array
{
    $lines = $parts = [];
    $length = 0;
    $long = false;
    $physical = explode("\n", $source);
    $last = array_key_last($physical);
    foreach ($physical as $index => $text) {
        $escapes = strlen($text) - strlen(rtrim($text, '\\'));
        $join = $escapes % 2 === 1 ? substr($text, -$escapes) : '';
        $parts[] = ['text' => substr($text, 0, strlen($text) - strlen($join)), 'join' => $join];
        // Earlier runs are already dropped; this line's run still counts.
        $length += strlen($text);
        $long = $long || $length >= 4095;
        $length -= strlen($join);
        if ($join === '' || $index === $last) {
            // getline() reads no line after a final LF, so a continuation into it stays open.
            $open = $join !== '' || ($index === $last && $text === '' && count($parts) > 1);
            $lines[] = ['number' => $index + 2 - count($parts), 'parts' => $parts, 'open' => $open, 'long' => $long];
            $parts = [];
            $length = 0;
            $long = false;
        }
    }
    return $lines;
}

function rc_physical(array $parts): array
{
    return array_map(static fn(array $part): string => $part['text'] . $part['join'], $parts);
}

/**
 * Indent, name and value of a command line; null for a comment or any other
 * line. The value may hold newlines that mark continuation breaks.
 */
function rc_command(string $line): ?array
{
    return preg_match('/^([ \t]*)([a-zA-Z_][a-zA-Z_0-9.]*)[ \t]*=[ \t]*(.*)$/sD', $line, $match)
        ? array_slice($match, 1) : null;
}

// rpc/parse.cc reads arguments the same way in 56194a38 and 0.9.8. These
// helpers follow parse_string, parse_object and parse_list; each throws where
// the daemon refuses the value.
function rc_skip_space(string $value, int $at): int
{
    return $at + strspn($value, " \t", $at);
}

function rc_string(string $value, int &$at, string $delimiters): string
{
    $length = strlen($value);
    $result = '';
    if ($at < $length && $value[$at] === '"') {
        for (++$at; $at < $length; ++$at) {
            if ($value[$at] === '"') {
                ++$at;
                return $result;
            }
            if ($value[$at] === '\\' && ++$at === $length) {
                throw new UnexpectedValueException('Escape character at end of input');
            }
            $result .= $value[$at];
        }
        throw new UnexpectedValueException('Missing closing quote');
    }
    for (; $at < $length && !str_contains($delimiters, $value[$at]); ++$at) {
        if ($value[$at] === '\\' && ++$at === $length) {
            throw new UnexpectedValueException('Escape character at end of input');
        }
        $result .= $value[$at];
    }
    return $result;
}

/** A string, a {list} as an array, or null for a (call). */
function rc_object(string $value, int &$at, string $delimiters): array|string|null
{
    $length = strlen($value);
    if ($at < $length && $value[$at] === '{') {
        ++$at;
        $list = rc_list($value, $at, ',}');
        $at = rc_skip_space($value, $at);
        if ($at === $length || $value[$at] !== '}') {
            throw new UnexpectedValueException("Could not find closing '}'");
        }
        ++$at;
        return $list;
    }
    if ($at < $length && $value[$at] === '(') {
        $parentheses = strspn($value, '(', $at);
        if ($parentheses > 3) {
            throw new UnexpectedValueException('Max 3 parentheses per object allowed');
        }
        $at += $parentheses;
        rc_string($value, $at, ',)');
        $at = rc_skip_space($value, $at);
        if ($at === $length || !str_contains(',)', $value[$at])) {
            throw new UnexpectedValueException("Could not find closing ')'");
        }
        if ($value[$at] === ',') {
            ++$at;
            rc_list($value, $at, ',)');
            $at = rc_skip_space($value, $at);
        }
        $closing = min($parentheses, strspn($value, ')', $at));
        $at += $closing;
        if ($closing !== $parentheses) {
            throw new UnexpectedValueException('Parentheses mismatch');
        }
        return null;
    }
    return rc_string($value, $at, $delimiters);
}

function rc_list(string $value, int &$at, string $delimiters): array
{
    for ($list = []; ; ++$at) {
        $at = rc_skip_space($value, $at);
        $list[] = rc_object($value, $at, $delimiters);
        $at = rc_skip_space($value, $at);
        if ($at === strlen($value) || $value[$at] !== ',') {
            return $list;
        }
    }
}

/**
 * The arguments rTorrent's parse_command reads from a command value: a string
 * or a list, strings without quotes and escapes. A (call) or a $command that
 * runs while the daemon reads the line is null. An unquoted ';' ends the
 * command; parse_command_file never runs the rest of the line. False where the
 * daemon refuses the value.
 */
function rc_arguments(string $value): array|string|null|false
{
    $delimiters = ",; \t\n\v\f\r";
    try {
        $at = rc_skip_space($value, 0);
        $arguments = rc_object($value, $at, $delimiters);
        $at = rc_skip_space($value, $at);
        if ($at < strlen($value) && $value[$at] === ',') {
            ++$at;
            $arguments = [$arguments, ...rc_list($value, $at, $delimiters)];
        }
    } catch (UnexpectedValueException) {
        return false;
    }
    $at = rc_skip_space($value, $at);
    if ($at < strlen($value) && !str_contains(";\n\0", $value[$at])) {
        return false; // Junk at end of input.
    }
    // parse_command_execute replaces each top-level $string with its result.
    $literal = static fn(array|string|null $argument): array|string|null =>
        is_string($argument) && str_starts_with($argument, '$') ? null : $argument;
    return is_array($arguments) ? array_map($literal, $arguments) : $literal($arguments);
}

// Commands that only define a method or a schedule; their bodies run later.
const RC_DEFINITIONS = ['schedule', 'schedule2', 'system.method.set_key', 'method.set_key',
    'system.method.insert', 'method.insert', 'system.method.set', 'method.set'];

// Daemon commands that close no log output while rTorrent reads the rc:
// definitions and schedules store bodies that run later (main.cc starts its
// event loop after the rc), execute starts an external process and the rest
// store settings. An rc cannot redefine a daemon command: method.insert and
// method.redirect refuse an existing name, method.erase a built-in one. Each
// name was checked as a command of the daemon whose list holds it (both for
// the shared list), so an operator method, however and wherever defined, is
// never one of them.
const RC_QUIET_COMMANDS = ['method.insert', 'method.insert.simple', 'method.insert.c_simple',
    'method.insert.s_c_simple', 'method.insert.value', 'method.insert.bool', 'method.insert.string',
    'method.insert.list', 'method.redirect', 'method.set', 'method.set_key', 'schedule',
    'execute', 'execute.throw', 'execute.throw.bg', 'execute.nothrow', 'execute.nothrow.bg', 'execute.raw',
    'execute.raw.bg', 'execute.raw_nothrow', 'execute.raw_nothrow.bg', 'execute.capture', 'execute.capture_nothrow',
    'system.daemon.set', 'directory.default.set', 'session.path.set', 'protocol.encryption.set', 'encryption',
    'network.scgi.open_local', 'network.scgi.open_port', 'port_range', 'pieces.hash.on_completion.set',
    'dht.mode.set', 'dht.port.set', 'protocol.pex.set', 'throttle.min_peers.normal.set',
    'throttle.max_peers.normal.set', 'throttle.min_peers.seed.set', 'throttle.max_peers.seed.set',
    'throttle.min_uploads.set', 'throttle.max_uploads.set', 'throttle.min_downloads.set',
    'throttle.max_downloads.set', 'throttle.max_uploads.global.set', 'throttle.max_downloads.global.set',
    'throttle.global_down.max_rate.set', 'throttle.global_up.max_rate.set', 'throttle.global_down.max_rate.set_kb',
    'throttle.global_up.max_rate.set_kb', 'network.bind_address.set', 'network.local_address.set',
    'keys.layout.set', 'ui.torrent_list.layout.set', 'network.max_open_sockets.set', 'network.max_open_files.set',
    'network.http.proxy_address.set', 'pieces.memory.max.set', 'system.umask.set', 'trackers.numwant.set',
    'trackers.use_udp.set', 'system.file.allocate.set', 'protocol.connection.leech.set',
    'protocol.connection.seed.set', 'pieces.preload.type.set'];
const RC_QUIET_NATIVE = ['schedule.remove', 'network.listen.port.range.set', 'network.listen.port.random.set',
    'network.prefer.ipv6.set', 'network.block.ipv6.set', 'dht.override_port.set', 'system.sockets.max_size.set',
    'system.sockets.files.max_alloc.set', 'system.sockets.http.max_alloc.set', 'system.sockets.adjust_alloc',
    'network.proxy.global.set', 'network.proxy.http.set', 'system.file_name.allow_legacy_utf8.set'];
const RC_QUIET_LEGACY = ['schedule2', 'schedule_remove', 'schedule_remove2', 'execute2', 'port_random', 'dht_port',
    'dht', 'bind', 'ip', 'key_layout', 'torrent_list_layout', 'max_memory_usage', 'proxy_address',
    'network.port_range.set', 'network.port_random.set', 'network.http.max_open', 'network.http.max_open.set',
    'network.proxy_address.set'];

/**
 * Renames inside a value that keep its layout: nested command aliases, the old
 * postdl argument and the PHP CLI path. A continued command's value is
 * rewritten again with its breaks marked, so this must stay a pure function.
 */
function rc_rewrite(string $command, string $value, bool $legacy, string $phpPath): string
{
    $commandBody = in_array($command, RC_DEFINITIONS, true);
    if (!$legacy && $commandBody) {
        // Nested aliases are commands at the beginning of a quoted schedule/
        // event body, or at the head of ((command,...)); prose arguments stay intact.
        $value = rc_nested_aliases($value,
            ['execute2' => 'execute.nothrow', 'schedule_remove2' => 'schedule.remove', 'schedule2' => 'schedule']);
    } elseif ($legacy && $commandBody) {
        $value = preg_replace('/("[ \t]*|\(\([ \t]*)schedule\.remove(?=[ \t]*[=,])/', '$1schedule_remove2', $value);
    }
    if (in_array($command, ['system.method.set_key', 'method.set_key'], true)) {
        $value = str_replace('/usr/local/bin/postdl,$d.directory=', '/usr/local/bin/postdl,$d.base_path=', $value);
    }
    $launch = preg_match('/^execute(?:\.[a-z_]+)*$/D', $command) && !preg_match('/^echo[ \t]*,/', $value);
    if ($commandBody || $launch) {
        $value = preg_replace_callback('~(?<![a-zA-Z0-9_./-])/usr/bin/php[0-9]*(?![a-zA-Z0-9_./-])~',
            static function (array $match) use ($phpPath): string {
                return $match[0] === '/usr/bin/php' || !is_executable($match[0]) ? $phpPath : $match[0];
            }, $value);
    }
    return $value;
}

/**
 * Physical lines of a changed command. A continued command keeps its breaks
 * when its value, rewritten with a newline at each break (MARKED), is the
 * rewritten VALUE once they are removed; the whitespace after '=' that the
 * value does not hold is ignored. Otherwise, as when a break splits a renamed
 * name or the whole value was replaced, the command is written on one line.
 */
function rc_layout(array $parts, string $head, string $value, ?string $marked): array
{
    $lines = $marked === null ? [] : explode("\n", $head . $marked);
    if (count($lines) !== count($parts) || ltrim(str_replace("\n", '', $marked), " \t") !== $value) {
        return [$head . $value];
    }
    foreach ($lines as $index => $line) {
        $lines[$index] .= $parts[$index]['join'];
    }
    return $lines;
}

/** Append whole lines after the last one, which receives its LF if it lacks one. */
function rc_append(array &$result, array $lines): void
{
    if (end($result) === '') {
        array_pop($result);
    }
    array_push($result, ...$lines);
    $result[] = '';
}

$file = $argv[1] ?? '';
$label = $argv[2] ?? $file;
$legacy = ($argv[3] ?? '') === 'legacy';
$filebot = ($argv[4] ?? '') === 'filebot';
$phpPath = getenv('UPDATE_PHP_PATH') ?: '/usr/bin/php';
$source = file_get_contents($file);
if ($source === false) {
    rc_fail($label, 1, 'Unable to read rTorrent configuration');
}
// rTorrent splits commands only at LF and refuses a CR left before it, so a
// file saved with CRLF cannot start. All CRs that end a line go at once, which
// keeps a second run unchanged; any other CR stays for its refusals.
$converted = preg_replace('/\r+(?=\n)|\r+$/D', '', $source);
if ($converted !== $source) {
    fwrite(STDOUT, "Converted $label line endings from CRLF to LF: rTorrent rejects a carriage return at the end of a line\n");
    $source = $converted;
}
// Captured 0.16.25 upgrade tests show UTF-8 aliases change persisted paths.
// Put this before any load command; catch is also accepted by 0.16.24/0.9.8.
$utf8Guard = 'catch = {"system.file_name.allow_legacy_utf8.set=0"}';
$result = [];
$tail = [];
$changed = 0;
$deleted = [];
$lastSetter = $lastAdjustment = -1;
$restoreFlush = false;
$shutdownHooks = [];
$setKeys = [];
$unreadKeys = [];
$earlySaveOptOut = false;
$logOutputs = [];
$quietCommands = array_flip([...RC_QUIET_COMMANDS, ...($legacy ? RC_QUIET_LEGACY : RC_QUIET_NATIVE)]);
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
foreach (rc_logical_lines($source) as ['number' => $lineNumber, 'parts' => $parts, 'open' => $open, 'long' => $long]) {
    if ($long) {
        rc_fail($label, $lineNumber, 'Line is 4095 bytes or longer, counting continued lines; rTorrent cannot read it; shorten it while stopped');
    }
    $original = rc_physical($parts);
    if ($open) {
        // Kept last as written: no line appended below may join it.
        $tail = $original;
        $result[] = '';
        continue;
    }
    if (!$legacy && str_starts_with($parts[0]['text'], '# docker-rutorrent: native ')) {
        $parts[0]['text'] = substr($parts[0]['text'], strlen('# docker-rutorrent: native '));
    }
    $line = implode('', array_column($parts, 'text'));
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
    if (!$legacy && $line === $utf8Guard) {
        continue; // Put back as the first line below.
    }
    $parsed = rc_command($line);
    if ($parsed === null) {
        array_push($result, ...rc_physical($parts));
        continue;
    }
    [$indent, $command, $value] = $parsed;
    $written = $value;
    $newCommand = ($legacy ? $legacyAliases : $nativeAliases)[$command] ?? $command;
    if (!$legacy && preg_match('/^(encoding\.add|encoding_list|throttle\.ip|dht\.throttle\.name(?:\.set)?|trackers\.use_udp\.set)$/D', $command)) {
        $deleted[$command] = ($deleted[$command] ?? 0) + 1;
        ++$changed;
        continue;
    }
    if ($legacy && (in_array($command, ['network.prefer.ipv6.set', 'network.block.ipv6.set',
        'system.sockets.adjust_alloc', 'system.file_name.allow_legacy_utf8.set'], true)
        || preg_match('/^system\.sockets\.[^.]+\.min_alloc\.set$/D', $command))) {
        $original[0] = '# docker-rutorrent: native ' . $original[0];
        array_push($result, ...$original);
        ++$changed;
        continue;
    }
    // Subscriptions are shared only while nothing can have closed their output,
    // which drops its groups: any command but a quiet daemon command or a
    // literal log.add_output could, and so could a parse-time ( or $ evaluation.
    $logOutput = $newCommand === 'log.add_output' ? rc_log_output($value) : null;
    $quiet = strpbrk($value, '($') === false && isset($quietCommands[$newCommand]);
    if (!$quiet && ($logOutput === null || $logOutput['output'] === null)) {
        $logOutputs = [];
    }
    if ($legacy && str_starts_with($newCommand, 'system.sockets.')) {
        rc_fail($label, $lineNumber, "Unsupported native socket command in legacy mode: $command");
    }
    if ($legacy && $command === 'log.open_file.flush') {
        $result[] = '# docker-rutorrent: legacy log.open_file.flush';
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
            rc_fail($label, $lineNumber, 'Proxy migration requires a literal host:port or proxy URL');
        }
        $value = $proxy;
    }
    if (!$legacy && $command === 'system.file_name.allow_legacy_utf8.set') {
        // A catch keeps explicit operator settings safe on a 0.16.24 rollback.
        $body = str_replace(['\\', '"'], ['\\\\', '\\"'], 'system.file_name.allow_legacy_utf8.set=' . $value);
        $catch = $indent . 'catch = {"' . $body . '"}';
        if ($catch !== $utf8Guard) {
            $result[] = $catch;
        }
        ++$changed;
        continue;
    }
    $value = rc_rewrite($newCommand, $value, $legacy, $phpPath);
    if (in_array($newCommand, ['system.method.set_key', 'method.set_key'], true)) {
        $arguments = rc_arguments($value);
        [$event, $key] = is_array($arguments) ? $arguments + [null, null] : [null, null];
        if (is_string($event) && is_string($key)) {
            // Two arguments erase the key; more set it.
            $setKeys[$event . "\0" . $key] = true;
            $hookArguments = array_slice($arguments, 2);
            // Erasing an image hook key opts out like commenting the hook does.
            if ($event === 'event.system.shutdown' && $hookArguments === []
                && preg_match('/^' . preg_quote(RC_EARLY_SAVE_KEY, '/') . '_*$/D', $key)) {
                $earlySaveOptOut = true;
            }
            if ($event === 'event.system.shutdown' && $hookArguments !== []) {
                // system_method_set_key joins string arguments into one command.
                $shutdownHooks[$key] = array_filter($hookArguments, 'is_string') === $hookArguments
                    ? implode(' ;', $hookArguments) : null;
            }
        } else {
            // Unknown or refused here; it may set the key of any event it names.
            $unreadKeys[] = [$lineNumber, is_string($event) ? $event : null];
        }
    }
    if (!$legacy && $newCommand === 'system.sockets.max_size.set') {
        $number = rc_number($value);
        if ($number !== null && $number < 512) {
            rc_fail($label, $lineNumber, 'Socket limit must be at least 512');
        }
    }
    if (!$legacy && $newCommand === 'pieces.memory.max.set') {
        $number = rc_number($value);
        if ($number !== null && $number < 512 * 1024 * 1024) {
            rc_fail($label, $lineNumber, 'Memory limit must be at least 512 MiB');
        }
    }
    if (!$legacy && preg_match('/^system\.sockets\.([^.]+)\.(min_alloc|max_alloc)\.set$/D', $newCommand, $socketMatch)) {
        $number = rc_number($value);
        // The pinned daemon permits small HTTP and minimum allocations; only
        // files.max_alloc has this floor, including a literal zero.
        $minimum = $socketMatch[1] === 'files' && $socketMatch[2] === 'max_alloc' ? 4 : null;
        if ($minimum !== null && $number !== null && $number < $minimum) {
            rc_fail($label, $lineNumber, "Socket allocation for {$socketMatch[1]} must be at least $minimum");
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
    if ($logOutput !== null) {
        // Only the exact first-argument name changes; the output name, any
        // trailing text and lines with other groups stay as written.
        $groupMap = $legacy ? RC_LOG_GROUPS_LEGACY : RC_LOG_GROUPS_NATIVE;
        $renamed = array_key_exists($logOutput['group'], $groupMap);
        $groups = $renamed ? $groupMap[$logOutput['group']] : [$logOutput['group']];
        $values = [];
        foreach ($groups as $group) {
            // A renamed line can expand to a group that the subscriptions just
            // above already send to the same output; repeating it would add
            // nothing. Lines that need no rename are never dropped.
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
            $result[] = $indent . $newCommand . ' = ' . $extra;
            ++$changed;
        }
    }
    if ($newCommand === $command && $value === $written) {
        $lines = rc_physical($parts);
    } else {
        $marked = count($parts) > 1 ? rc_command(implode("\n", array_column($parts, 'text'))) : null;
        $lines = rc_layout($parts, $indent . $newCommand . ' = ', $value,
            $marked === null ? null : rc_rewrite($newCommand, $marked[2], $legacy, $phpPath));
    }
    if ($lines !== $original) {
        ++$changed;
    }
    array_push($result, ...$lines);
}
if (!$legacy) {
    array_unshift($result, $utf8Guard);
    if (!str_starts_with($source, $utf8Guard . "\n")) {
        ++$changed;
    }
    // Inserting the first line moves both command indices equally.
}
if (!$legacy && $lastSetter > $lastAdjustment) {
    // Keep valid operator recalculations. Add one only after the final setter.
    rc_append($result, ['', '# Recalculate socket allocations after per-category limits.', 'system.sockets.adjust_alloc =']);
    ++$changed;
}
// Save before stopped announces can consume the supervisor/Docker deadline.
// Existing operator hooks keep their keys and commands, including our key name;
// a commented image hook or an erase of its key is an opt-out.
if (!$earlySaveOptOut && !array_intersect(['session.save=', "'session.save='"], array_filter($shutdownHooks, 'is_string'))) {
    $key = RC_EARLY_SAVE_KEY;
    while (array_key_exists($key, $shutdownHooks)) $key .= '_';
    rc_append($result, ['method.set_key = event.system.shutdown,' . $key . ',' . RC_EARLY_SAVE_COMMAND]);
    ++$changed;
}
// A later definition of a key replaces the earlier command: any key that
// rTorrent sets for the same event, even to erase it, stays the operator's,
// and so does one that a method.set_key with unread arguments may set.
if ($filebot) {
    foreach (RC_FILEBOT_HOOKS as [$event, $key, $hookCommand]) {
        if (isset($setKeys[$event . "\0" . $key])) {
            continue;
        }
        foreach ($unreadKeys as [$unreadLine, $unreadEvent]) {
            if ($unreadEvent === null || $unreadEvent === $event) {
                fwrite(STDERR, "$label:$unreadLine: FileBot hook $event,$key not added: this method.set_key may set it, and its event or key is not a literal\n");
                continue 2;
            }
        }
        rc_append($result, ["method.set_key = $event,$key,$hookCommand"]);
        ++$changed;
    }
}
if ($tail !== []) {
    array_pop($result);
    array_push($result, ...$tail);
}
$transformed = implode("\n", $result);
foreach (rc_logical_lines($transformed) as ['number' => $lineNumber, 'long' => $long]) {
    if ($long) {
        rc_fail($label, $lineNumber, 'Migrated line would be 4095 bytes or longer, which rTorrent cannot read; shorten the command while stopped');
    }
}
if (file_put_contents($file, $transformed) !== strlen($transformed)) {
    rc_fail($label, 1, 'Unable to write complete transformed rTorrent configuration');
}
if ($changed > 0) {
    fwrite(STDOUT, "Migrated $label: $changed changed lines" . ($deleted === [] ? '' : '; removed ' . implode(', ', array_keys($deleted))) . "\n");
}
