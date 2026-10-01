<?php
// Run as the configured service identity, with /rutorrent/app/php as cwd.
require 'settings.php';
require '../plugins/erasedata/filesystem.php';
$path = erasedataQueuePath();
if ($path === false || !erasedataEnsureQueueDirectory($path)) {
    fwrite(STDERR, "Application rejected protected queue\n");
    exit(1);
}
echo $path, "\n";
