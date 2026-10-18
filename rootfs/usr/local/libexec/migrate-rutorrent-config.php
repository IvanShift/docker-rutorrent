<?php
// Read configuration as source. Operator PHP is never executed by this root helper.
declare(strict_types=1);

function fail(string $message): never
{
    fwrite(STDERR, $message . "\n");
    exit(1);
}

/** All tokens of one TOKEN_PARSE pass, comments and whitespace included, with byte offsets. */
function raw_tokens(string $source): array
{
    try {
        $pieces = token_get_all($source, TOKEN_PARSE);
    } catch (ParseError $error) {
        fail(($GLOBALS['label'] ?? 'ruTorrent PHP configuration') . ':' . $error->getLine()
            . ': Invalid ruTorrent PHP configuration: ' . $error->getMessage());
    }
    $raw = [];
    $offset = 0;
    foreach ($pieces as $piece) {
        $text = is_array($piece) ? $piece[1] : $piece;
        $raw[] = ['id' => is_array($piece) ? $piece[0] : null, 'text' => $text,
            'start' => $offset, 'end' => $offset + strlen($text)];
        $offset += strlen($text);
    }
    return $raw;
}

/** Code tokens; comments and interpolated strings cannot impersonate code. */
function code_tokens(array $raw): array
{
    $tokens = [];
    $literal = null;
    foreach ($raw as $token) {
        $id = $token['id'];
        $text = $token['text'];
        if ($literal !== null) {
            $literal['text'] .= $text;
            $literal['end'] = $token['end'];
            if (($literal['quote'] === 'heredoc' && $id === T_END_HEREDOC)
                || ($literal['quote'] !== 'heredoc' && $id === null && $text === $literal['quote'])) {
                unset($literal['quote']);
                $tokens[] = $literal;
                $literal = null;
            }
            continue;
        }
        if ($id === T_START_HEREDOC || ($id === null && in_array($text, ['"', 'b"', 'B"', '`'], true))) {
            $literal = ['id' => 0, 'text' => $text, 'start' => $token['start'], 'end' => $token['end'],
                'quote' => $id === T_START_HEREDOC ? 'heredoc' : substr($text, -1)];
            continue;
        }
        if (!in_array($id, [T_WHITESPACE, T_COMMENT, T_DOC_COMMENT], true)) {
            $tokens[] = $token;
        }
    }
    return $tokens;
}

function source_tokens(string $source): array
{
    return code_tokens(raw_tokens($source));
}

/** A quoted constant string decoded as PHP decodes it; null for any other token. */
function literal_value(array $token): ?string
{
    if ($token['id'] !== T_CONSTANT_ENCAPSED_STRING) {
        return null;
    }
    $text = ltrim($token['text'], 'bB'); // A binary prefix does not change the value.
    $body = substr($text, 1, -1);
    if ($text[0] === "'") {
        return strtr($body, ['\\\\' => '\\', "\\'" => "'"]);
    }
    // TOKEN_PARSE guarantees a constant string with valid \u{...}; PHP keeps
    // the backslash of every other sequence.
    return preg_replace_callback('/\\\\(?:([nrtvef\\\\$"])|([0-7]{1,3})|x([0-9A-Fa-f]{1,2})|u\{([0-9A-Fa-f]+)\})/',
        escape_value(...), $body, flags: PREG_UNMATCHED_AS_NULL);
}

/** The bytes of one double-quoted escape sequence. */
function escape_value(array $match): string
{
    [, $simple, $octal, $hex, $code] = $match + [null, null, null, null, null];
    if ($simple !== null) {
        return ['n' => "\n", 'r' => "\r", 't' => "\t", 'v' => "\v", 'e' => "\e", 'f' => "\f"][$simple] ?? $simple;
    }
    if ($octal !== null) {
        return chr(octdec($octal) & 0xff); // PHP truncates an octal escape above \377.
    }
    if ($hex !== null) {
        return chr(hexdec($hex));
    }
    $code = hexdec($code);
    return match (true) {
        $code < 0x80 => chr($code),
        $code < 0x800 => chr(0xc0 | $code >> 6) . chr(0x80 | $code & 0x3f),
        $code < 0x10000 => chr(0xe0 | $code >> 12) . chr(0x80 | $code >> 6 & 0x3f) . chr(0x80 | $code & 0x3f),
        default => chr(0xf0 | $code >> 18) . chr(0x80 | $code >> 12 & 0x3f) . chr(0x80 | $code >> 6 & 0x3f)
            . chr(0x80 | $code & 0x3f),
    };
}

/** Delimiter text of a code token, or ''; an attribute's #[ is closed by a plain ]. */
function delimiter(array $token): string
{
    if ($token['id'] === T_ATTRIBUTE) {
        return '[';
    }
    return $token['id'] === null && in_array($token['text'], ['(', '[', '{', ')', ']', '}'], true) ? $token['text'] : '';
}

function nesting(array $token): int
{
    return match (delimiter($token)) {
        '(', '[', '{' => 1,
        ')', ']', '}' => -1,
        default => 0,
    };
}

function expression_end(array $tokens, int $start): int
{
    $stack = [];
    $pairs = [')' => '(', ']' => '[', '}' => '{'];
    for ($i = $start, $count = count($tokens); $i < $count; ++$i) {
        if ($tokens[$i]['id'] === T_CLOSE_TAG && $stack === []) {
            return $i; // PHP's closing tag supplies an implicit semicolon.
        }
        if ($tokens[$i]['id'] === null && $tokens[$i]['text'] === ';' && $stack === []) {
            return $i;
        }
        $delimiter = delimiter($tokens[$i]);
        if (isset($pairs[$delimiter])) {
            if (array_pop($stack) !== $pairs[$delimiter]) {
                fail('Unbalanced assignment in ruTorrent PHP configuration');
            }
        } elseif ($delimiter !== '') {
            $stack[] = $delimiter;
        }
    }
    fail('Assignment without a terminating semicolon in ruTorrent PHP configuration');
}

/** First token outside an arrow body, including an outer ternary's sibling branch. */
function arrow_end(array $tokens, int $start): int
{
    $depth = 0;
    $count = count($tokens);
    for ($i = $start + 1; $i < $count; ++$i) {
        $depth += nesting($tokens[$i]);
        if ($depth === 0 && $tokens[$i]['id'] === T_DOUBLE_ARROW) {
            break; // Parameters and a return type belong to the signature, not the body.
        }
    }
    $ternaries = 0;
    for (++$i; $i < $count; ++$i) {
        $token = $tokens[$i];
        $change = nesting($token);
        if ($depth === 0) {
            if ($change < 0 || $token['id'] === T_CLOSE_TAG
                || ($token['id'] === null && in_array($token['text'], [';', ','], true))) {
                return $i;
            }
            if ($token['id'] === T_FN) {
                $i = arrow_end($tokens, $i) - 1;
                continue; // A nested signature may itself contain a return-type colon.
            }
            if ($token['id'] === null && $token['text'] === '?') {
                ++$ternaries;
            } elseif ($token['id'] === null && $token['text'] === ':') {
                if ($ternaries === 0) {
                    return $i;
                }
                --$ternaries;
            }
        }
        $depth += $change;
    }
    return $count;
}

/**
 * Only file-scope assignments to global variables are managed; definitions, braced
 * blocks, strings, properties and variable variables remain untouched.
 */
function assignments(array $tokens): array
{
    $result = [];
    $depth = 0;
    for ($i = 0, $count = count($tokens); $i < $count; ++$i) {
        $token = $tokens[$i];
        $depth += nesting($token);
        if ($depth !== 0) {
            continue;
        }
        if ($token['id'] === T_FN) {
            $i = arrow_end($tokens, $i) - 1; // Revisit the outer expression's boundary next.
            continue;
        }
        $previous = $tokens[$i - 1] ?? ['id' => null, 'text' => ''];
        if ($token['id'] !== T_VARIABLE || ($previous['id'] === null && $previous['text'] === '$')
            || in_array($previous['id'], [T_DOUBLE_COLON, T_OBJECT_OPERATOR, T_NULLSAFE_OBJECT_OPERATOR], true)) {
            continue; // $$name, Foo::$name and $object->$name are other targets.
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
        // A key may also be written with ??=, which writes only a null key.
        $operator = $tokens[$next]['text'] ?? '';
        if ($operator !== '=' && ($key === null || $operator !== '??=')) {
            continue;
        }
        $start = $next + 1;
        $end = expression_end($tokens, $start);
        $result[] = ['variable' => $variable, 'key' => $key, 'start' => $start, 'end' => $end];
        $i = $end;
    }
    return $result;
}

/** Replace the expression an assignment or array entry spans with a literal. */
function expression_edit(array $tokens, array $range, mixed $value): array
{
    $offset = $tokens[$range['start']]['start'];
    return [$offset, $tokens[$range['end'] - 1]['end'] - $offset, var_export($value, true)];
}

function apply_edits(string $source, array $edits): string
{
    usort($edits, fn(array $a, array $b): int => $b[0] <=> $a[0]);
    foreach ($edits as [$start, $length, $replacement]) {
        $source = substr_replace($source, $replacement, $start, $length);
    }
    return $source;
}

/** Token shapes of our generated blocks; any literal may be regenerated. */
const MANAGED_BLOCKS = [
    'defaults' => '~^(?:\$(?:topDirectory|scgi_host|scgi_port) \?\?= (?:<string>|<int>) ; '
        . '|\$pathToExternals \[ <string> \] \?\?= <string> ; )+$~D',
    'policy' => '~^\$forbidUserSettings = (?:true|false) ; \$enableCSRFCheck = true ; $~D',
];

/** Remove only our exact, file-scope token blocks, wherever an operator appended code. */
function strip_managed_blocks(string $source): string
{
    $raw = raw_tokens($source);
    $code = code_tokens($raw);
    $removals = [];
    $depth = 0;
    $cursor = 0;
    foreach ($raw as $index => $piece) {
        // code_tokens treats strings as single literals, so interpolation braces
        // cannot impersonate file scope or move a marker out of an operator function.
        while (isset($code[$cursor]) && $code[$cursor]['end'] <= $piece['start']) {
            $depth += nesting($code[$cursor++]);
        }
        if ($depth !== 0 || $piece['id'] !== T_COMMENT || !preg_match(
            '~^// docker-rutorrent: managed (defaults|policy) begin$~D', trim($piece['text']), $kind)) continue;
        $previous = $code[$cursor - 1] ?? null;
        if ($previous === null || ($previous['id'] !== T_OPEN_TAG
            && !($previous['id'] === null && in_array($previous['text'], [';', '}'], true)))) continue;
        $shape = '';
        for ($i = $index + 1; isset($raw[$i]) && $raw[$i]['id'] !== T_COMMENT && strlen($shape) < 4096; ++$i) {
            $shape .= match ($raw[$i]['id']) {
                T_WHITESPACE => '',
                T_CONSTANT_ENCAPSED_STRING => '<string> ',
                T_LNUMBER => '<int> ',
                default => $raw[$i]['text'] . ' ',
            };
        }
        if (!isset($raw[$i]) || trim($raw[$i]['text']) !== "// docker-rutorrent: managed $kind[1] end"
            || !preg_match(MANAGED_BLOCKS[$kind[1]], $shape)) continue;
        $start = $piece['start'];
        $end = $raw[$i]['end'];
        if (substr($source, $end, 2) === "\r\n") $end += 2;
        elseif (substr($source, $end, 1) === "\n") ++$end;
        $last = count($removals) - 1;
        if ($last >= 0 && $removals[$last][1] === $start) {
            $removals[$last][1] = $end; // Adjacent blocks are one run.
        } else {
            $removals[] = [$start, $end];
        }
    }
    $last = count($removals) - 1;
    if ($last >= 0 && $removals[$last][1] === strlen($source)) {
        // Remove only the separator we generated for the final suffix. Interior
        // PHP tags, output and whitespace still belong to the operator source.
        $priorEnd = $last > 0 ? $removals[$last - 1][1] : 0;
        foreach (["<?php\n", "\n"] as $separator) {
            $start = $removals[$last][0] - strlen($separator);
            if ($start >= $priorEnd && substr($source, $start, strlen($separator)) === $separator) {
                $removals[$last][0] = $start;
                break;
            }
        }
    }
    return apply_edits($source, array_map(
        fn(array $range): array => [$range[0], $range[1] - $range[0], ''], $removals));
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

/**
 * Entries of an expression that is exactly one array literal whose elements all
 * have literal keys. Null for any other form: its keys are unknown.
 */
function array_entries(array $tokens, array $assignment): ?array
{
    $open = $assignment['start'];
    $long = $tokens[$open]['id'] === T_ARRAY;
    $open += (int)$long;
    $close = $assignment['end'] - 1;
    if (delimiter($tokens[$open]) !== ($long ? '(' : '[')) {
        return null;
    }
    $entries = [];
    $start = $open + 1;
    $depth = 0;
    for ($i = $start; $i <= $close; ++$i) {
        $token = $tokens[$i];
        $nesting = nesting($token);
        if ($depth === 0 && ($nesting < 0 || ($token['id'] === null && $token['text'] === ','))) {
            if ($nesting < 0 && $i !== $close) {
                return null; // The array is only an operand, as in a union or a dimension fetch.
            }
            if ($i > $start) {
                $key = literal_value($tokens[$start]);
                if ($key === null || ($tokens[$start + 1]['id'] ?? null) !== T_DOUBLE_ARROW) {
                    return null; // Spread, computed or missing keys can supply any key.
                }
                $entries[$key] = ['start' => $start + 2, 'end' => $i];
            }
            $start = $i + 1;
            continue;
        }
        $depth += $nesting;
    }
    return $entries;
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
$forbidSettings = getenv('RU_FORBID_USER_SETTINGS');
if ($forbidSettings === false || $forbidSettings === '') {
    $forbidSettings = 'true';
}
if (!in_array($forbidSettings, ['true', 'false'], true)) {
    fail('RU_FORBID_USER_SETTINGS must be true or false');
}
// Regenerate the managed blocks after every operator statement and local include.
$source = strip_managed_blocks($source);
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
    '$forbidUserSettings' => $forbidSettings === 'true',
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
$externals = []; // All proven literal ranges: a later syntactic write may never run.
foreach ($assignments as $assignment) {
    $variable = $assignment['variable'];
    $start = $assignment['start'];
    $end = $assignment['end'];
    if (array_key_exists($variable, $scalars)) {
        $edits[] = expression_edit($tokens, $assignment, $scalars[$variable]);
    } elseif ($variable === '$XMLRPCMountPoint' && $start === $end - 1
        && literal_value($tokens[$start]) === '/RPC') {
        $edits[] = expression_edit($tokens, $assignment, '/RPC2');
    } elseif ($variable === '$pathToExternals') {
        if ($assignment['key'] === null) {
            foreach (array_entries($tokens, $assignment) ?? [] as $key => $entry) {
                $externals[$key][] = $entry;
            }
        } else {
            $externals[$assignment['key']][] = $assignment;
        }
    }
}
foreach ($externals as $key => $ranges) { // PHP turns a numeric string key into an int.
    foreach ($ranges as $range) {
        $value = $range['start'] === $range['end'] - 1 ? literal_value($tokens[$range['start']]) : null;
        $replacement = external_replacement((string)$key, $value, $defaults, $phpPath);
        if ($replacement !== null && $replacement !== $value) {
            $edits[] = expression_edit($tokens, $range, $replacement);
        }
    }
}
$source = apply_edits($source, $edits);
// Conditions, expressions and config.local.php may set or replace anything the
// tokens cannot prove, so each default fills only a value still unset after all
// operator code. The policy itself is enforced last.
$code = "// docker-rutorrent: managed defaults begin\n";
foreach (['$topDirectory', '$scgi_host', '$scgi_port'] as $variable) {
    $code .= $variable . ' ??= ' . var_export($scalars[$variable], true) . ";\n";
}
foreach ($defaults as $key => $value) {
    $code .= '$pathToExternals[' . var_export($key, true) . '] ??= ' . var_export($value, true) . ";\n";
}
$source = append_php($source, $code . "// docker-rutorrent: managed defaults end\n"
    . "// docker-rutorrent: managed policy begin\n"
    . '$forbidUserSettings = ' . $forbidSettings . ";\n" . '$enableCSRFCheck = true;' . "\n"
    . "// docker-rutorrent: managed policy end\n");
source_tokens($source); // Validate transformed code before touching the private copy.
if (file_put_contents($file, $source) !== strlen($source)) {
    fail("Unable to write complete ruTorrent PHP configuration: $label");
}
