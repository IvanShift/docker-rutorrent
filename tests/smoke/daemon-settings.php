<?php
// Ask the daemon through the application's own Unix-SCGI path, as its service UID.
require 'settings.php';
$settings = rTorrentSettings::get(true);
$expected = $argv[1];
if (!$settings->linkExist || $settings->version !== $expected) {
    fwrite(STDERR, "Unexpected daemon SCGI version: ".$settings->version."\n");
    exit(1);
}
printf("%s/%s\n", $settings->version, $settings->libVersion);
