<?php
function proofRecord($type, $data)
{
    return pack('CCnnCC', 1, $type, 1, strlen($data), 0, 0).$data;
}
function proofValue($name, $value)
{
    return chr(strlen($name)).chr(strlen($value)).$name.$value;
}
$socket = stream_socket_client('unix://'.getenv('RENAME_PROOF_ROOT').'/fpm.sock', $errno, $error, 3);
if (!$socket) exit(2);
$params = proofValue('SCRIPT_FILENAME', '/proof/worker.php')
    .proofValue('REQUEST_METHOD', 'GET').proofValue('SERVER_PROTOCOL', 'HTTP/1.1');
fwrite($socket, proofRecord(1, pack('nC', 1, 0).str_repeat("\0", 5))
    .proofRecord(4, $params).proofRecord(4, '').proofRecord(5, ''));
$out = '';
while (!feof($socket)) {
    $header = fread($socket, 8);
    if (strlen($header) !== 8) break;
    $fields = unpack('Cversion/Ctype/nrequest/nlength/Cpadding/Creserved', $header);
    $data = '';
    while (strlen($data) < $fields['length']) {
        $chunk = fread($socket, $fields['length'] - strlen($data));
        if ($chunk === false || $chunk === '') exit(3);
        $data .= $chunk;
    }
    if ($fields['padding']) fread($socket, $fields['padding']);
    if ($fields['type'] === 6) $out .= $data;
    if ($fields['type'] === 7) fwrite(STDERR, $data);
    if ($fields['type'] === 3) break;
}
fclose($socket);
$body = substr($out, strpos($out, "\r\n\r\n") + 4);
echo $body."\n";
$data = json_decode($body, true);
exit(is_array($data) && $data['probe'] && $data['restore'] && !$data['source'] && $data['target']
    && $data['bytes'] === 'FPM descriptor payload' && $data['probes'] === array() && $data['log'] === array() ? 0 : 1);
