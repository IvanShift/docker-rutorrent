<?php
// Read configuration as source. Operator PHP is never executed by this root helper.
declare(strict_types=1);

function fail(string $message): never
{
    fwrite(STDERR, $message . "\n");
    exit(1);
}

/** Tokens retain byte offsets; comments and interpolated strings cannot impersonate code. */
function source_tokens(string $source): array
{
    try {
        $raw = token_get_all($source, TOKEN_PARSE);
    } catch (ParseError $error) {
        fail('Invalid ruTorrent PHP configuration: ' . $error->getMessage());
    }
    $tokens = [];
    $offset = 0;
    $literal = null;
    foreach ($raw as $rawToken) {
        $id = is_array($rawToken) ? $rawToken[0] : null;
        $text = is_array($rawToken) ? $rawToken[1] : $rawToken;
        $start = $offset;
        $offset += strlen($text);
        if ($literal !== null) {
            $literal['text'] .= $text;
            $literal['end'] = $offset;
            if (($literal['quote'] === 'heredoc' && $id === T_END_HEREDOC)
                || ($literal['quote'] !== 'heredoc' && $id === null && $text === $literal['quote'])) {
                unset($literal['quote']);
                $tokens[] = $literal;
                $literal = null;
            }
            continue;
        }
        if ($id === T_START_HEREDOC || ($id === null && ($text === '"' || $text === '`'))) {
            $literal = ['id' => 0, 'text' => $text, 'start' => $start, 'end' => $offset,
                'quote' => $id === T_START_HEREDOC ? 'heredoc' : $text];
            continue;
        }
        if (in_array($id, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            continue;
        }
        $tokens[] = ['id' => $id, 'text' => $text, 'start' => $start, 'end' => $offset];
    }
    return $tokens;
}

function literal_value(array $token): ?string
{
    if ($token['id'] !== T_CONSTANT_ENCAPSED_STRING) {
        return null;
    }
    $text = $token['text'];
    $body = substr($text, 1, -1);
    if ($text[0] === "'") {
        return str_replace(["\\\\", "\\'"], ["\\", "'"], $body);
    }
    // TOKEN_PARSE guarantees this is a constant string, without interpolation.
    return stripcslashes($body);
}

function expression_end(array $tokens, int $start): int
{
    $stack = [];
    $pairs = [')' => '(', ']' => '[', '}' => '{'];
    for ($i = $start, $count = count($tokens); $i < $count; ++$i) {
        $text = $tokens[$i]['text'];
        if ($tokens[$i]['id'] === T_CLOSE_TAG && $stack === []) {
            return $i; // PHP's closing tag supplies an implicit semicolon.
        }
        if ($tokens[$i]['id'] !== null) {
            continue;
        }
        if ($text === ';' && $stack === []) {
            return $i;
        }
        if (in_array($text, ['(', '[', '{'], true)) {
            $stack[] = $text;
        } elseif (isset($pairs[$text])) {
            if (array_pop($stack) !== $pairs[$text]) {
                fail('Unbalanced assignment in ruTorrent PHP configuration');
            }
        }
    }
    fail('Assignment without a terminating semicolon in ruTorrent PHP configuration');
}

/** Only file-scope assignments are managed; definitions and strings remain untouched. */
function assignments(array $tokens): array
{
    $result = [];
    $depth = 0;
    for ($i = 0, $count = count($tokens); $i < $count; ++$i) {
        $token = $tokens[$i];
        if ($token['id'] === null && in_array($token['text'], ['(', '[', '{'], true)) {
            ++$depth;
        } elseif ($token['id'] === null && in_array($token['text'], [')', ']', '}'], true)) {
            --$depth;
        }
        if ($depth !== 0 || $token['id'] !== T_VARIABLE) {
            continue;
        }
        $variable = $token['text'];
        $next = $i + 1;
        $key = null;
        if ($variable === '$pathToExternals' && ($tokens[$next]['text'] ?? '') === '['
            && ($tokens[$next + 2]['text'] ?? '') === ']') {
            $key = literal_value($tokens[$next + 1]);
            if ($key === null) {
                continue;
            }
            $next += 3;
        }
        if (($tokens[$next]['text'] ?? '') !== '=') {
            continue;
        }
        $start = $next + 1;
        $end = expression_end($tokens, $start);
        $result[] = ['variable' => $variable, 'key' => $key, 'start' => $start, 'end' => $end];
        $i = $end;
    }
    return $result;
}

function apply_edits(string $source, array $edits): string
{
    usort($edits, fn(array $a, array $b): int => $b[0] <=> $a[0]);
    foreach ($edits as [$start, $length, $replacement]) {
        $source = substr_replace($source, $replacement, $start, $length);
    }
    return $source;
}

function external_replacement(string $key, ?string $value, array $defaults, string $phpPath): ?string
{
    if ($value === null) {
        return null; // Expressions and operator wrappers have the same policy for every key.
    }
    if ($key === 'php') {
        if ($value === '' || $value === '/usr/bin/php'
            || (preg_match('~^/usr/bin/php[0-9]+$~D', $value) && !is_executable($value))) {
            return $phpPath;
        }
        return null;
    }
    if ($key === 'gzip' || $key === 'stat') {
        return $value !== '' && !is_executable($value) ? '' : null;
    }
    if (!isset($defaults[$key])) {
        return null;
    }
    if ($value === '' || in_array($value, ['/usr/bin/' . $key, '/usr/local/bin/' . $key], true)) {
        return $defaults[$key];
    }
    return null;
}

/** Locate a literal array with balanced token delimiters, including short arrays. */
function array_entries(array $tokens, array $assignment): ?array
{
    $first = $assignment['start'];
    $end = $assignment['end'];
    $open = $first;
    if (($tokens[$first]['id'] ?? null) === T_ARRAY) {
        $open = $first + 1;
    }
    $openText = $tokens[$open]['text'] ?? '';
    if (!in_array($openText, ['(', '['], true)) {
        return null;
    }
    $close = $end - 1;
    if (($tokens[$close]['text'] ?? '') !== ($openText === '(' ? ')' : ']')) {
        return null;
    }
    $entries = [];
    $start = $open + 1;
    $depth = 0;
    $lastComma = null;
    for ($i = $start; $i <= $close; ++$i) {
        $token = $tokens[$i];
        $text = $token['text'];
        $boundary = $i === $close || ($token['id'] === null && $text === ',' && $depth === 0);
        if ($boundary) {
            if ($i > $start && ($tokens[$start + 1]['id'] ?? null) === T_DOUBLE_ARROW) {
                $key = literal_value($tokens[$start]);
                if ($key !== null) {
                    $entries[$key] = ['start' => $start + 2, 'end' => $i];
                }
            }
            if ($i < $close) {
                $lastComma = $i;
            }
            $start = $i + 1;
            continue;
        }
        if ($token['id'] === null && in_array($text, ['(', '[', '{'], true)) {
            ++$depth;
        } elseif ($token['id'] === null && in_array($text, [')', ']', '}'], true)) {
            --$depth;
        }
    }
    return ['entries' => $entries, 'open' => $open, 'close' => $close,
        'trailingComma' => $lastComma === $close - 1];
}

function append_php(string $source, string $code): string
{
    $inPhp = false;
    foreach (token_get_all($source) as $token) {
        if (is_array($token) && ($token[0] === T_OPEN_TAG || $token[0] === T_OPEN_TAG_WITH_ECHO)) {
            $inPhp = true;
        } elseif (is_array($token) && $token[0] === T_CLOSE_TAG) {
            $inPhp = false;
        }
    }
    return $source . ($inPhp ? "\n" : "<?php\n") . $code;
}

$file = $argv[1] ?? '';
$label = $argv[2] ?? $file;
$phpPath = getenv('UPDATE_PHP_PATH') ?: '/usr/bin/php';
$source = file_get_contents($file);
if ($source === false) {
    fail("Unable to read ruTorrent PHP configuration: $label");
}
// Remove only our known suffix. It is regenerated after all local includes on every start.
$marker = "// docker-rutorrent: managed policy begin\n";
$policy = $marker . '$forbidUserSettings = true;' . "\n" . '$enableCSRFCheck = true;' . "\n"
    . "// docker-rutorrent: managed policy end\n";
if (str_ends_with($source, $policy)) {
    $source = substr($source, 0, -strlen($policy));
    // Remove the separator added with this suffix. Reopening PHP must never
    // introduce output outside the original configuration's PHP tags.
    foreach (["\n<?php\n", "<?php\n", "\n"] as $separator) {
        if (str_ends_with($source, $separator)) {
            $source = substr($source, 0, -strlen($separator));
            break;
        }
    }
}
$tokens = source_tokens($source);
$assignments = assignments($tokens);
$downloadDir = getenv('DOWNLOAD_DIRECTORY') ?: '/data/downloads';
if ($downloadDir[0] !== '/' || strpbrk($downloadDir, "\r\n\0") !== false) {
    fail('DOWNLOAD_DIRECTORY must be an absolute directory without control characters');
}
$scgiHost = getenv('RU_SCGI_HOST') ?: 'unix:///run/rtorrent/rtorrent.sock';
$scgiPort = getenv('RU_SCGI_PORT');
$scgiPort = $scgiPort === false || $scgiPort === '' ? '0' : $scgiPort;
if (!preg_match('/^[0-9]+$/D', $scgiPort) || strlen(ltrim($scgiPort, '0')) > 5 || (int)$scgiPort > 65535) {
    fail('RU_SCGI_PORT must be a decimal integer from 0 to 65535');
}
$scalars = [
    '$topDirectory' => rtrim($downloadDir, '/') . '/',
    '$scgi_host' => $scgiHost,
    '$scgi_port' => (int)$scgiPort,
    '$forbidUserSettings' => true,
    '$enableCSRFCheck' => true,
];
$defaults = [
    'php' => $phpPath,
    'curl' => '/usr/bin/curl',
    'ffmpeg' => '/usr/bin/ffmpeg',
    'unrar' => '/usr/local/bin/unrar',
    'sox' => '/usr/bin/sox',
    'mediainfo' => '/usr/bin/mediainfo',
];
foreach ($defaults as $path) {
    if (!is_executable($path)) {
        fail("Image external executable is unavailable: $path");
    }
}
$edits = [];
$seen = [];
$lastArray = null;
$lastKeyAssignments = [];
foreach ($assignments as $index => $assignment) {
    $variable = $assignment['variable'];
    $start = $assignment['start'];
    $end = $assignment['end'];
    if (array_key_exists($variable, $scalars)) {
        $seen[$variable] = true;
        $edits[] = [$tokens[$start]['start'], $tokens[$end - 1]['end'] - $tokens[$start]['start'],
            var_export($scalars[$variable], true)];
    } elseif ($variable === '$XMLRPCMountPoint' && $start === $end - 1
        && literal_value($tokens[$start]) === '/RPC') {
        $edits[] = [$tokens[$start]['start'], strlen($tokens[$start]['text']), "'/RPC2'"];
    } elseif ($variable === '$pathToExternals') {
        if ($assignment['key'] === null) {
            $lastArray = [$index, $assignment];
            $lastKeyAssignments = [];
        } else {
            $lastKeyAssignments[$assignment['key']] = $assignment;
        }
    }
}
$present = [];
if ($lastArray !== null) {
    $array = array_entries($tokens, $lastArray[1]);
    if ($array !== null) {
        foreach ($array['entries'] as $key => $entry) {
            $present[$key] = true;
            $start = $entry['start'];
            $end = $entry['end'];
            $value = $start === $end - 1 ? literal_value($tokens[$start]) : null;
            $replacement = external_replacement($key, $value, $defaults, $phpPath);
            if ($replacement !== null && $replacement !== $value) {
                $edits[] = [$tokens[$start]['start'], $tokens[$end - 1]['end'] - $tokens[$start]['start'],
                    var_export($replacement, true)];
            }
        }
        $missing = array_diff_key($defaults, $present, $lastKeyAssignments);
        if ($missing !== []) {
            $close = $array['close'];
            $insert = '';
            if ($close > $array['open'] + 1 && !$array['trailingComma']) {
                // Insert before a trailing comment, at the last expression's token boundary.
                if ($tokens[$close - 1]['end'] === $tokens[$close]['start']) {
                    $insert = ',';
                } else {
                    $edits[] = [$tokens[$close - 1]['end'], 0, ','];
                }
            }
            foreach ($missing as $key => $value) {
                $insert .= "\n\t" . var_export($key, true) . ' => ' . var_export($value, true) . ',';
            }
            $edits[] = [$tokens[$close]['start'], 0, $insert . "\n"];
        }
    }
}
foreach ($lastKeyAssignments as $key => $assignment) {
    $start = $assignment['start'];
    $end = $assignment['end'];
    $value = $start === $end - 1 ? literal_value($tokens[$start]) : null;
    $replacement = external_replacement($key, $value, $defaults, $phpPath);
    if ($replacement !== null && $replacement !== $value) {
        $edits[] = [$tokens[$start]['start'], $tokens[$end - 1]['end'] - $tokens[$start]['start'],
            var_export($replacement, true)];
    }
}
$source = apply_edits($source, $edits);
$missingScalars = array_diff_key($scalars, $seen);
foreach (['$forbidUserSettings', '$enableCSRFCheck'] as $key) {
    unset($missingScalars[$key]); // The final policy supplies these after config.local.php.
}
if ($missingScalars !== []) {
    $code = '';
    foreach ($missingScalars as $variable => $value) {
        $code .= $variable . ' = ' . var_export($value, true) . ";\n";
    }
    $source = append_php($source, $code);
}
if ($lastArray === null) {
    if ($lastKeyAssignments === []) {
        $source = append_php($source, '$pathToExternals = ' . var_export($defaults, true) . ";\n");
    } else {
        $code = '';
        foreach (array_diff_key($defaults, $lastKeyAssignments) as $key => $value) {
            $code .= '$pathToExternals[' . var_export($key, true) . '] = ' . var_export($value, true) . ";\n";
        }
        $source = append_php($source, $code);
    }
}
$source = append_php($source, $policy);
source_tokens($source); // Validate transformed code before touching the private copy.
if (file_put_contents($file, $source) !== strlen($source)) {
    fail("Unable to write complete ruTorrent PHP configuration: $label");
}
