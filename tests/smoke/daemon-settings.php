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

if (($argv[2] ?? '') === 'migrated-hook') {
    $request = new rXMLRPCRequest(new rXMLRPCCommand('smoke.migrated.simple'));
    if (!$request->success() || (string)($request->val[0] ?? '') !== '0') {
        fwrite(STDERR, "Migrated nested method did not execute successfully\n");
        exit(1);
    }
}
