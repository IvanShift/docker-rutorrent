<?php
// Assertions shared by the real image gate and its negative controls.

function mediaRequire(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

function mediaRun(array $command): string
{
    $process = proc_open(array_merge(['timeout', '20'], $command), [
        0 => ['file', '/dev/null', 'r'],
        1 => ['pipe', 'w'],
        2 => STDERR,
    ], $pipes);
    mediaRequire(is_resource($process), 'Cannot start media command');
    $output = stream_get_contents($pipes[1]);
    fclose($pipes[1]);
    $status = proc_close($process);
    mediaRequire($status === 0, 'Media command failed with status '.$status.': '.$command[0]);
    mediaRequire($output !== false, 'Cannot read media command output');
    return $output;
}

function mediaRender(array $command, string $output): void
{
    // Refuse reuse before running the tool: successful no-render must never
    // inherit an earlier frame, including a dangling symlink.
    mediaRequire(!file_exists($output) && !is_link($output), 'Frame output already exists: '.$output);
    mediaRun($command);
    clearstatcache(true, $output);
    mediaRequire(is_file($output) && filesize($output) > 0, 'No fresh nonempty frame: '.$output);
}

function mediaCheckRgb(string $actual, string $reference, int $width, int $height, float $limit): void
{
    $bytes = $width * $height * 3;
    mediaRequire(strlen($actual) === $bytes && strlen($reference) === $bytes, 'Wrong RGB frame length');
    $sum = $squares = [0, 0, 0];
    $difference = 0;
    for ($i = 0; $i < $bytes; ++$i) {
        $channel = $i % 3;
        $value = ord($actual[$i]);
        $sum[$channel] += $value;
        $squares[$channel] += $value * $value;
        $difference += abs($value - ord($reference[$i]));
    }
    $deviation = 0;
    $pixels = $width * $height;
    for ($channel = 0; $channel < 3; ++$channel) {
        $deviation = max($deviation, sqrt(max(0, $squares[$channel] / $pixels - ($sum[$channel] / $pixels) ** 2)));
    }
    mediaRequire($deviation >= 10, 'Blank or uninformative frame');
    mediaRequire($difference / $bytes <= $limit,
        'Frame pixels differ from the reviewed reference: MAE '.round($difference / $bytes, 3).' > '.$limit);
}

function mediaCheckFrame(string $path, int $width, int $height, int $type, string $reference, float $limit): string
{
    $size = getimagesize($path);
    mediaRequire($size !== false && $size[0] === $width && $size[1] === $height && $size[2] === $type,
        'Wrong frame dimensions or image format: '.$path);
    // The slim image has no rawvideo encoder or PHP GD. Its existing PNG
    // encoder can emit RGB rows without predictors; zlib is stock PHP.
    $png = mediaRun(['ffmpeg', '-hide_banner', '-loglevel', 'error', '-i', $path,
        '-vframes', '1', '-c:v', 'png', '-pix_fmt', 'rgb24', '-pred', 'none', '-f', 'image2pipe', 'pipe:1']);
    $raw = mediaPngRgb($png, $width, $height);
    mediaCheckRgb($raw, $reference, $width, $height, $limit);
    return $raw;
}

function mediaPngRgb(string $png, int $width, int $height): string
{
    mediaRequire(substr($png, 0, 8) === "\x89PNG\r\n\x1a\n", 'Missing RGB PNG output');
    $compressed = '';
    $header = false;
    $end = false;
    for ($offset = 8, $length = strlen($png); $offset + 12 <= $length;) {
        $size = unpack('N', substr($png, $offset, 4))[1];
        mediaRequire($size <= $length - $offset - 12, 'Truncated RGB PNG chunk');
        $type = substr($png, $offset + 4, 4);
        $data = substr($png, $offset + 8, $size);
        if ($type === 'IHDR') {
            $header = $data === pack('NNCCCCC', $width, $height, 8, 2, 0, 0, 0);
        } elseif ($type === 'IDAT') {
            $compressed .= $data;
        } elseif ($type === 'IEND') {
            $end = true;
        }
        $offset += $size + 12;
    }
    mediaRequire($header && $end && $offset === $length, 'Wrong RGB PNG structure');
    $rows = gzuncompress($compressed);
    $stride = $width * 3 + 1;
    mediaRequire($rows !== false && strlen($rows) === $stride * $height, 'Wrong RGB PNG rows');
    $raw = '';
    for ($row = 0; $row < $height; ++$row) {
        mediaRequire($rows[$row * $stride] === "\0", 'RGB PNG unexpectedly uses a predictor');
        $raw .= substr($rows, $row * $stride + 1, $stride - 1);
    }
    return $raw;
}

function mediaCheckSeek(string $first, string $later): void
{
    mediaRequire(strlen($first) > 0 && strlen($first) === strlen($later), 'Wrong seek frame lengths');
    $difference = 0;
    for ($i = 0, $length = strlen($first); $i < $length; ++$i) {
        $difference += abs(ord($first[$i]) - ord($later[$i]));
    }
    mediaRequire($difference / strlen($first) >= 3, 'Seek did not change the rendered frame');
}

function mediaCheckFingerprint(string $json, array $expected): void
{
    $actual = json_decode($json, true, 512, JSON_THROW_ON_ERROR);
    mediaRequire(is_array($actual) && isset($actual['duration'], $actual['fingerprint'])
        && is_numeric($actual['duration']) && is_array($actual['fingerprint']), 'Incomplete fpcalc JSON');
    mediaRequire(abs($actual['duration'] - $expected['duration']) <= 0.05
        && $actual['fingerprint'] === $expected['fingerprint'], 'Fingerprint or duration differs from the reviewed reference');
}
