<?php
// A bounded read-only probe independent of the application's PHP configuration.
$deadline = hrtime(true) + 2000000000;
$socket = @stream_socket_client('unix:///run/rtorrent/rtorrent.sock', $errno, $error, 0.5);
if ($socket === false) {
    exit(1);
}
stream_set_blocking($socket, false);
$xml = '<?xml version="1.0"?><methodCall><methodName>system.pid</methodName><params/></methodCall>';
$headers = "CONTENT_LENGTH\0" . strlen($xml) . "\0SCGI\0" . "1\0REQUEST_METHOD\0POST\0REQUEST_URI\0/RPC2\0";
$request = strlen($headers) . ':' . $headers . ',' . $xml;
$response = '';
$wait = static function (bool $writing) use ($socket, $deadline): bool {
    $remaining = $deadline - hrtime(true);
    if ($remaining <= 0) {
        return false;
    }
    $read = $writing ? [] : [$socket];
    $write = $writing ? [$socket] : [];
    $except = [];
    return @stream_select($read, $write, $except, intdiv($remaining, 1000000000), intdiv($remaining % 1000000000, 1000)) > 0;
};
while ($request !== '') {
    if (!$wait(true)) {
        exit(1);
    }
    $written = @fwrite($socket, $request);
    if ($written === false || $written === 0) {
        exit(1);
    }
    $request = substr($request, $written);
}
while (!feof($socket) && strlen($response) < 16384) {
    if (!$wait(false)) {
        exit(1);
    }
    $chunk = @fread($socket, 4096);
    if ($chunk === false) {
        exit(1);
    }
    $response .= $chunk;
    if (strpos($response, '</methodResponse>') !== false) {
        break;
    }
}
fclose($socket);
$start = strpos($response, '<?xml');
if ($start === false || stripos($response, '<!DOCTYPE') !== false) {
    exit(1);
}
libxml_use_internal_errors(true);
$result = simplexml_load_string(substr($response, $start), SimpleXMLElement::class, LIBXML_NONET);
if ($result === false || $result->getName() !== 'methodResponse' || isset($result->fault)) {
    exit(1);
}
$value = $result->params->param->value;
$pid = (string) ($value->i8 ?? $value->int ?? $value->i4 ?? '');
exit(ctype_digit($pid) && (int) $pid > 0 ? 0 : 1);
