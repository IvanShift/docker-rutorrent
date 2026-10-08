<?php
// Create a complete, tracker-free single-file torrent for isolated daemon smoke tests.
if ($argc < 3 || $argc > 5 || basename($argv[1]) !== $argv[1]) {
    fwrite(STDERR, "Usage: torrent-fixture.php NAME SOURCE [DOWNLOAD_DIRECTORY [PAYLOAD]]\n");
    exit(64);
}
$name = $argv[1];
$source = $argv[2];
$directory = $argv[3] ?? '/data/downloads';
$payload = $argv[4] ?? 'x';
if (strlen($payload) > 16384) {
    fwrite(STDERR, "Fixture payload exceeds one piece\n");
    exit(64);
}
foreach ([dirname($source), $directory] as $parent) {
    if (!is_dir($parent) && !mkdir($parent, 0755, true)) {
        exit(1);
    }
}
$info = 'd6:lengthi' . strlen($payload) . 'e4:name' . strlen($name) . ':' . $name
    . '12:piece lengthi16384e6:pieces20:' . sha1($payload, true) . 'e';
$torrent = 'd4:info' . $info . 'e';
if (file_put_contents($directory . '/' . $name, $payload) !== strlen($payload)
    || file_put_contents($source, $torrent) !== strlen($torrent)) {
    exit(1);
}
echo strtoupper(sha1($info)) . "\n";
