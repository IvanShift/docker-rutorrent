<?php
// Run inside the image, with read-only fixtures and a private output directory.
require __DIR__.'/screenshots-check.php';

$fixtures = $argv[1];
$workspace = sys_get_temp_dir().'/screenshots-'.bin2hex(random_bytes(12));
mediaRequire(mkdir($workspace, 0700), 'Cannot create a fresh screenshot workspace');
$frames = 0;
$fingerprints = 0;
$filter = "scale='max(sar,1)*iw':'max(1/sar,1)*ih'";
$inputs = [
    'h264' => 'Юникод видео H264.mkv',
    'h264-10bit' => 'h264-10bit.mkv',
    'hevc' => 'hevc.mkv',
    'hevc-10bit' => 'hevc-10bit.mkv',
    'vp8' => 'vp8.webm',
    'vp9' => 'vp9.webm',
    'vp9-10bit' => 'vp9-10bit.webm',
    'av1' => 'av1.mkv',
    'av1-10bit' => 'av1-10bit.mkv',
];

function referenceRgb(string $fixtures, string $name): string
{
    $bytes = file_get_contents($fixtures.'/reference/'.$name.'.rgb.gz');
    mediaRequire($bytes !== false, 'Cannot read reference '.$name);
    $raw = gzdecode($bytes);
    mediaRequire($raw !== false, 'Cannot decompress reference '.$name);
    return $raw;
}

function screenshot(string $input, string $output, string $offset, string $filter): void
{
    // Keep this processing sequence equal to screenshots/action.php.
    mediaRender(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-ss', $offset, '-i', $input,
        '-y', '-vframes', '1', '-an', '-sn', '-vf', $filter, $output], $output);
}

try {
    foreach ($inputs as $name => $input) {
        $decoded = [];
        foreach (['first' => '1.2', 'later' => '2.3'] as $label => $offset) {
            $path = $workspace.'/'.$name.'-'.$label.'.png';
            screenshot($fixtures.'/'.$input, $path, $offset, $filter.',scale=96:-1');
            $decoded[$label] = mediaCheckFrame($path, 96, 36, IMAGETYPE_PNG,
                referenceRgb($fixtures, $name.'-'.$label), 1.0);
            ++$frames;
        }
        mediaCheckSeek($decoded['first'], $decoded['later']);
        $path = $workspace.'/'.$name.'.jpg';
        screenshot($fixtures.'/'.$input, $path, '1.2', $filter.',scale=96:-1');
        // Keep a JPEG reference too: normal JPEG chroma loss in this small
        // pattern exceeds a useful decoder-regression tolerance against PNG.
        mediaCheckFrame($path, 96, 36, IMAGETYPE_JPEG, referenceRgb($fixtures, $name.'-jpeg'), 2.0);
        ++$frames;
    }
    $path = $workspace.'/sar.png';
    screenshot($fixtures.'/'.$inputs['h264'], $path, '1.2', $filter);
    mediaCheckFrame($path, 256, 96, IMAGETYPE_PNG, referenceRgb($fixtures, 'h264-sar'), 1.0);
    ++$frames;
    $path = $workspace.'/jxl.png';
    screenshot($fixtures.'/jxl-renamed.mkv', $path, '0', $filter);
    mediaCheckFrame($path, 128, 96, IMAGETYPE_PNG, referenceRgb($fixtures, 'jxl'), 1.0);
    ++$frames;

    // FFmpeg can exit zero at EOF without creating an output. That must fail the
    // same fresh-frame assertion as a decoder which stopped writing files.
    $path = $workspace.'/eof.png';
    try {
        screenshot($fixtures.'/'.$inputs['h264'], $path, '60', $filter);
        throw new RuntimeException('EOF unexpectedly rendered a frame');
    } catch (RuntimeException $error) {
        mediaRequire(strpos($error->getMessage(), 'No fresh nonempty frame: ') === 0,
            'EOF did not exercise successful no-render: '.$error->getMessage());
    }
    $path = $workspace.'/invalid.png';
    $process = proc_open(['timeout', '20', 'ffmpeg', '-hide_banner', '-loglevel', 'error',
        '-ss', '0', '-i', $fixtures.'/invalid.bin', '-y', '-vframes', '1', '-an', '-sn', '-vf', $filter, $path],
        [0 => ['file', '/dev/null', 'r'], 1 => ['file', '/dev/null', 'w'], 2 => ['file', '/dev/null', 'w']], $pipes);
    mediaRequire(is_resource($process), 'Cannot start invalid-input probe');
    mediaRequire(proc_close($process) === 183 && !file_exists($path), 'Invalid input succeeded, crashed, timed out or left a frame');

    if (is_executable('/filebot/filebot.sh')) {
        $expected = json_decode(file_get_contents($fixtures.'/fingerprints.json'), true, 512, JSON_THROW_ON_ERROR);
        foreach (['flac', 'opus', 'ogg'] as $extension) {
            $json = mediaRun(['fpcalc', '-json', '-raw', $fixtures.'/audio.'.$extension]);
            mediaCheckFingerprint($json, $expected[$extension]);
            ++$fingerprints;
        }
    }
    printf("ok - screenshots: %d reference-checked frames, SAR, JPEG/PNG, seek, EOF/invalid refusal; %d exact fingerprints\n", $frames, $fingerprints);
} finally {
    foreach (glob($workspace.'/*') as $path) {
        unlink($path);
    }
    rmdir($workspace);
}
