<?php
// Probe both private FPM pools with fixed synthetic input; never expose this via HTTP.
declare(strict_types=1);
function record(int $type, string $body): string {
    return pack('CCnnCC', 1, $type, 1, strlen($body), 0, 0).$body;
}
function readBytes($peer, int $length): string {
    $value = '';
    while (strlen($value) < $length) {
        $chunk = fread($peer, $length - strlen($value));
        if ($chunk === false || $chunk === '') throw new RuntimeException('bounded FPM read failed');
        $value .= $chunk;
    }
    return $value;
}
function lengthField(int $length): string { return $length < 128 ? chr($length) : pack('N', $length | 0x80000000); }
$expected = 'https://public.example.test/rutorrent/';
$params = ['SCRIPT_FILENAME' => '/tmp/health-capacity-env.php', 'SCRIPT_NAME' => '/health-capacity-env.php',
    'REQUEST_METHOD' => 'GET', 'REQUEST_URI' => '/health-capacity-env.php', 'QUERY_STRING' => '',
    'SERVER_PROTOCOL' => 'HTTP/1.1', 'SERVER_NAME' => 'localhost', 'SERVER_PORT' => '8080',
    'REMOTE_ADDR' => '127.0.0.1', 'REDIRECT_STATUS' => '200'];
$encoded = '';
foreach ($params as $key => $value) $encoded .= lengthField(strlen($key)).lengthField(strlen($value)).$key.$value;
foreach (['www' => '/run/php/php85-fpm.sock', 'health' => '/run/php/php85-health.sock'] as $pool => $socket) {
    $peer = @stream_socket_client('unix://'.$socket, $errno, $error, 2);
    if ($peer === false) throw new RuntimeException('cannot connect '.$pool.' pool');
    stream_set_timeout($peer, 2);
    $input = record(1, pack('nC', 1, 0).str_repeat("\0", 5)).record(4, $encoded).record(4, '').record(5, '');
    if (fwrite($peer, $input) !== strlen($input)) throw new RuntimeException('incomplete '.$pool.' request');
    $output = '';
    $deadline = microtime(true) + 5;
    while (true) {
        if (microtime(true) > $deadline) throw new RuntimeException('FPM probe deadline');
        $header = readBytes($peer, 8);
        $fields = unpack('Cversion/Ctype/nrequest/nlength/Cpadding/Creserved', $header);
        $needed = $fields['length'] + $fields['padding'];
        if ($needed > 4351) throw new RuntimeException('oversized FPM frame');
        $body = readBytes($peer, $needed);
        if ($fields['type'] === 6) $output .= substr($body, 0, $fields['length']);
        if (strlen($output) > 4096) throw new RuntimeException('oversized '.$pool.' response');
        if ($fields['type'] === 3) break;
    }
    fclose($peer);
    $parts = explode("\r\n\r\n", $output, 2);
    $value = json_decode($parts[1] ?? '', true);
    if ($value !== ['public' => $expected, 'private' => false]) throw new RuntimeException('environment boundary failed for '.$pool);
    echo 'ok - '.$pool.' pool passes explicit public URL and clears undeclared environment'."\n";
}
