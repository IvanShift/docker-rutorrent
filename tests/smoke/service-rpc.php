<?php
// Standalone test client: exercise daemon persistence without application APIs.
// Wrappers may select a longer per-read deadline for intentionally blocking
// daemon work ($serviceRpcTimeout), or another socket ($serviceRpcSocket).
$serviceRpcSocket ??= '/run/rtorrent/rtorrent.sock';
$serviceRpcTimeout ??= 5;
// A whole reply is read until the daemon closes the connection; this bound
// only stops a runaway peer and is far above any smoke-test reply.
$serviceRpcMaxBytes ??= 16 * 1024 * 1024;

function service_rpc_fail(string $message): never
{
    fwrite(STDERR, "service-rpc: $message\n");
    exit(1);
}

// The one child element named NAME, or a refusal: a reply must carry exactly
// one result, so other XML cannot pass for a status-only success.
function service_rpc_only_child(SimpleXMLElement $parent, string $name, string $method): SimpleXMLElement
{
    $children = iterator_to_array($parent->children(), false);
    if (count($children) !== 1 || $children[0]->getName() !== $name) {
        service_rpc_fail("XML-RPC reply to $method has no single result: <{$parent->getName()}> must hold exactly one <$name>");
    }
    return $children[0];
}

if ($argc < 2) {
    service_rpc_fail('usage: service-rpc.php METHOD [STRING_ARGUMENT...]');
}
$method = $argv[1];
$socket = @stream_socket_client('unix://' . $serviceRpcSocket, $errno, $error, 3);
if ($socket === false) {
    service_rpc_fail("cannot connect to $serviceRpcSocket for $method: $error");
}
stream_set_timeout($socket, $serviceRpcTimeout);
$xml = '<?xml version="1.0"?><methodCall><methodName>' . htmlspecialchars($method, ENT_XML1) . '</methodName><params>';
foreach (array_slice($argv, 2) as $arg) {
    $xml .= '<param><value><string>' . htmlspecialchars($arg, ENT_XML1) . '</string></value></param>';
}
$xml .= '</params></methodCall>';
$headers = "CONTENT_LENGTH\0" . strlen($xml) . "\0SCGI\0" . "1\0REQUEST_METHOD\0POST\0REQUEST_URI\0/RPC2\0";
$request = strlen($headers) . ':' . $headers . ',' . $xml;
while ($request !== '') {
    $size = fwrite($socket, $request);
    if ($size === false || $size === 0) {
        service_rpc_fail("cannot send the $method request");
    }
    $request = substr($request, $size);
}
$response = '';
while (!feof($socket)) {
    $chunk = fread($socket, 65536);
    if (stream_get_meta_data($socket)['timed_out']) {
        service_rpc_fail("no complete reply to $method within {$serviceRpcTimeout}s (" . strlen($response) . ' bytes received)');
    }
    if ($chunk === false) {
        service_rpc_fail("cannot read the reply to $method");
    }
    $response .= $chunk;
    if (strlen($response) > $serviceRpcMaxBytes) {
        service_rpc_fail("reply to $method exceeds $serviceRpcMaxBytes bytes");
    }
}
fclose($socket);
// SCGI reply: CGI-style headers, a blank line, then the XML-RPC document.
$separator = strpos($response, "\r\n\r\n");
if ($separator === false) {
    service_rpc_fail("incomplete reply to $method: no SCGI header terminator in " . strlen($response) . ' bytes');
}
$body = substr($response, $separator + 4);
if (preg_match('/^Content-Length:[ \t]*([0-9]+)[ \t]*$/mi', substr($response, 0, $separator), $length)
    && (int) $length[1] !== strlen($body)) {
    service_rpc_fail("truncated reply to $method: Content-Length {$length[1]}, received " . strlen($body) . ' bytes');
}
libxml_use_internal_errors(true);
$result = simplexml_load_string($body, SimpleXMLElement::class, LIBXML_NONET);
if ($result === false) {
    service_rpc_fail("malformed XML-RPC reply to $method (" . strlen($body) . ' bytes)');
}
if ($result->getName() !== 'methodResponse') {
    service_rpc_fail("unexpected reply to $method: root element <{$result->getName()}>, expected <methodResponse>");
}
if ($result->count() === 1 && isset($result->fault)) {
    fwrite(STDERR, $body . "\n");
    exit(1);
}
$value = service_rpc_only_child(service_rpc_only_child(service_rpc_only_child($result, 'params', $method), 'param', $method), 'value', $method);
// Print the one string or integer result; an empty string is a result, too.
$types = iterator_to_array($value->children(), false);
if (count($types) > 1) {
    service_rpc_fail("XML-RPC reply to $method has malformed <value> result");
}
// XML-RPC: a value without a type element is a string.
$type = $types[0] ?? $value;
$name = $types === [] ? 'string' : $type->getName();
if (!in_array($name, ['string', 'i8', 'int', 'i4'], true)) {
    service_rpc_fail("XML-RPC reply to $method has unsupported result type <$name>");
}
$text = (string) $type;
if ($type->count() !== 0 || ($name !== 'string' && preg_match('/\A[+-]?[0-9]+\z/', $text) !== 1)) {
    service_rpc_fail("XML-RPC reply to $method has malformed <$name> result");
}
echo $text . "\n";
