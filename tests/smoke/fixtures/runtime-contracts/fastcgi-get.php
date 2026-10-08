<?php
// A minimal FastCGI GET for runtime-contracts.sh: nginx serves the health pool
// only for one fixed script, so its probe goes to the pool socket directly.
// Usage: php85 fastcgi-get.php SOCKET SCRIPT_FILENAME QUERY_STRING
if ($argc !== 4) {
    fwrite(STDERR, "usage: fastcgi-get.php SOCKET SCRIPT_FILENAME QUERY_STRING\n");
    exit(64);
}
[, $socket, $script, $query] = $argv;
$params = '';
foreach (['SCRIPT_FILENAME' => $script, 'REQUEST_METHOD' => 'GET', 'QUERY_STRING' => $query,
          'GATEWAY_INTERFACE' => 'CGI/1.1', 'SERVER_PROTOCOL' => 'HTTP/1.1'] as $name => $value) {
    // One-byte FastCGI lengths: every name and value must stay below 128 bytes.
    if (strlen($name) > 127 || strlen($value) > 127) {
        fwrite(STDERR, "FastCGI parameter too long: $name\n");
        exit(64);
    }
    $params .= chr(strlen($name)) . chr(strlen($value)) . $name . $value;
}
$stream = stream_socket_client('unix://' . $socket, $errno, $error, 5);
if ($stream === false) {
    fwrite(STDERR, "cannot connect to $socket: $error\n");
    exit(1);
}
stream_set_timeout($stream, 10);
$record = static fn(int $type, string $content): string => pack('CCnnCx', 1, $type, 1, strlen($content), 0) . $content;
// BEGIN_REQUEST (responder role, close after reply), PARAMS, empty PARAMS, empty STDIN.
fwrite($stream, $record(1, pack('nCx5', 1, 0)) . $record(4, $params) . $record(4, '') . $record(5, ''));
$stdout = '';
while (true) {
    $header = stream_get_contents($stream, 8);
    if (!is_string($header) || strlen($header) !== 8) {
        fwrite(STDERR, "truncated FastCGI response\n");
        exit(1);
    }
    $fields = unpack('Cversion/Ctype/nrequest/nlength/Cpadding/Creserved', $header);
    $content = $fields['length'] > 0 ? stream_get_contents($stream, $fields['length']) : '';
    if ($fields['padding'] > 0) {
        stream_get_contents($stream, $fields['padding']);
    }
    if ($fields['type'] === 6) {
        $stdout .= $content;
    } elseif ($fields['type'] === 7) {
        fwrite(STDERR, $content);
    } elseif ($fields['type'] === 3) {
        break;
    }
}
$separator = strpos($stdout, "\r\n\r\n");
if ($separator === false) {
    fwrite(STDERR, "FastCGI response without a header block\n");
    exit(1);
}
echo substr($stdout, $separator + 4);
