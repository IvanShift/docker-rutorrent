<?php
// Generate exact HTTP denials for workers in the source actually shipped.
// PHP's tokenizer distinguishes a CLI argv reference from comments and strings.
if (PHP_SAPI !== 'cli' || $argc !== 3) {
    fwrite(STDERR, "Usage: generate-cli-only.php SOURCE OUTPUT\n");
    exit(64);
}
$root = realpath($argv[1]);
if ($root === false) {
    fwrite(STDERR, "ruTorrent source directory is missing\n");
    exit(1);
}
$workers = [];
foreach (['php', 'plugins'] as $directory) {
    $path = $root.'/'.$directory;
    if (!is_dir($path)) {
        fwrite(STDERR, "Missing ruTorrent directory: ".$directory."\n");
        exit(1);
    }
    $files = new RecursiveIteratorIterator(new RecursiveDirectoryIterator($path,
        FilesystemIterator::SKIP_DOTS));
    foreach ($files as $file) {
        if (!$file->isFile() || $file->isLink() || $file->getExtension() !== 'php') {
            continue;
        }
        $source = file_get_contents($file->getPathname());
        if ($source === false) {
            throw new RuntimeException('Cannot read '.$file->getPathname());
        }
        foreach (token_get_all($source) as $token) {
            if (is_array($token) && $token[0] === T_VARIABLE && $token[1] === '$argv') {
                $relative = substr($file->getPathname(), strlen($root));
                if (!preg_match('~^/[a-zA-Z0-9_./-]+$~D', $relative)) {
                    throw new RuntimeException('Unsafe CLI worker path: '.$relative);
                }
                $workers[] = $relative;
                break;
            }
        }
    }
}
sort($workers, SORT_STRING);
$config = "# Generated from the pinned ruTorrent PHP sources; exact CLI worker paths.\n";
foreach ($workers as $worker) {
    $config .= 'location = '.$worker." { deny all; }\n";
}
if (file_put_contents($argv[2], $config) === false) {
    throw new RuntimeException('Cannot write CLI worker policy');
}
