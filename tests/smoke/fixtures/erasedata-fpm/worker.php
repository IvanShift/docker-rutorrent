<?php
// Run only as the disposable proof pool's service UID; application PHP is real.
class FileUtil
{
    public static $log = array();
    public static function toLog($message) { self::$log[] = $message; }
}
require '/rutorrent/app/plugins/erasedata/filesystem.php';
$directory = getenv('RENAME_PROOF_ROOT').'/held-fd';
if (!mkdir($directory, 0700)) exit(1);
$filesystem = new ErasedataFilesystemOps();
$capability = $filesystem->acquireDirectoryCapability($directory, $filesystem->targetIdentity($directory));
if (!is_array($capability)) exit(2);
try {
    file_put_contents($directory.'/source', 'FPM descriptor payload');
    $probe = ErasedataFilesystemOps::canRenameNoReplace($directory);
    $restore = $filesystem->renameNoReplace($capability['path'].'/source', $capability['path'].'/target');
    echo json_encode(array('probe'=>$probe, 'restore'=>$restore,
        'source'=>is_file($directory.'/source'), 'target'=>is_file($directory.'/target'),
        'bytes'=>is_file($directory.'/target') ? file_get_contents($directory.'/target') : false,
        'probes'=>glob($directory.'/.rutorrent-rename-probe-*'), 'log'=>FileUtil::$log));
} finally {
    $filesystem->releaseDirectoryCapability($capability);
}
