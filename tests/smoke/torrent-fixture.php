<?php
// Create a complete single-file torrent for isolated daemon smoke tests. It is
// tracker-free unless --announce names a (loopback) fixture tracker.
$arguments = array_slice($argv, 1);
$announce = null;
if (($arguments[0] ?? '') === '--announce') {
    $announce = $arguments[1] ?? '';
    $arguments = array_slice($arguments, 2);
}
if ($announce === '' || count($arguments) < 2 || count($arguments) > 4 || basename($arguments[0]) !== $arguments[0]) {
    fwrite(STDERR, "Usage: torrent-fixture.php [--announce URL] NAME SOURCE [DOWNLOAD_DIRECTORY [PAYLOAD]]\n");
    exit(64);
}
$name = $arguments[0];
$source = $arguments[1];
$directory = $arguments[2] ?? '/data/downloads';
$payload = $arguments[3] ?? 'x';
if (strlen($payload) > 16384) {
    fwrite(STDERR, "Fixture payload exceeds one piece\n");
    exit(64);
}
foreach ([dirname($source), $directory] as $parent) {
    if (!is_dir($parent) && !mkdir($parent, 0755, true)) {
        fwrite(STDERR, "Cannot create fixture directory $parent\n");
        exit(1);
    }
}
$info = 'd6:lengthi' . strlen($payload) . 'e4:name' . strlen($name) . ':' . $name
    . '12:piece lengthi16384e6:pieces20:' . sha1($payload, true) . 'e';
// Bencoded dictionary keys are sorted: "announce" precedes "info".
$torrent = 'd' . ($announce === null ? '' : '8:announce' . strlen($announce) . ':' . $announce)
    . '4:info' . $info . 'e';
if (file_put_contents($directory . '/' . $name, $payload) !== strlen($payload)
    || file_put_contents($source, $torrent) !== strlen($torrent)) {
    fwrite(STDERR, "Cannot write fixture $source\n");
    exit(1);
}
echo strtoupper(sha1($info)) . "\n";
