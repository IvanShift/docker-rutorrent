<?php
// Root-owned runtime check: verify FPM can load the operator's actual config.
// Loading it here avoids the plugin initialization side effects of util.php.
require '/rutorrent/app/conf/config.php';
header('Content-Type: text/plain');
echo 'ok';
