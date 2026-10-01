#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"

smoke_check_image "$1"

# Exercise the installed CLI and shared library together. The old 26.01 library
# lacks the Matroska accessibility/original/commentary fields below.
smoke_run --rm --network none -i --entrypoint /bin/sh "$1" -s <<'SH'
set -eu
workspace=$(mktemp -d)
trap 'rm -rf "$workspace"' EXIT
export LC_ALL=C.UTF-8
installed_packages=$(apk info --no-network -v)
printf '%s\n' "$installed_packages" | grep -E '^mediainfo-26\.05-r[0-9]+$' >/dev/null
printf '%s\n' "$installed_packages" | grep -E '^libmediainfo-26\.05-r[0-9]+$' >/dev/null

sample="$workspace/Тестовый файл.mkv"
ffmpeg -hide_banner -loglevel error \
  -f lavfi -i testsrc=size=32x32:rate=1 \
  -f lavfi -i sine=frequency=1000:sample_rate=8000 \
  -t 1 -c:v ffv1 -c:a pcm_s16le \
  -disposition:a hearing_impaired+visual_impaired+original+comment "$sample"
timeout 20 mediainfo --Output=JSON "$sample" > "$workspace/mkv.json"
php85 /dev/stdin "$workspace/mkv.json" <<'PHP'
<?php
$data = json_decode(file_get_contents($argv[1]), true, 512, JSON_THROW_ON_ERROR);
$tracks = array_column($data['media']['track'], null, '@type');
if ($tracks['General']['Format'] !== 'Matroska'
    || $tracks['Video']['Width'] !== '32' || $tracks['Video']['Height'] !== '32'
    || $tracks['Audio']['SamplingRate'] !== '8000') {
    throw new RuntimeException('Unexpected media dimensions, format or sample rate');
}
$actual = preg_split('/\s*\/\s*/', $tracks['Audio']['ServiceKind'] ?? '');
if (array_diff(['HI', 'VI', 'O', 'C'], $actual)) {
    throw new RuntimeException('Missing Matroska service flags: '.json_encode($actual));
}
PHP

# ruTorrent supports user-provided --Inform templates, including Unicode paths.
printf 'Video;%%Width%%x%%Height%%' > "$workspace/template"
test "$(mediainfo --Inform="file://$workspace/template" "$sample")" = '32x32'

ffmpeg -hide_banner -loglevel error -i "$sample" \
  -c:v mpeg4 -c:a aac "$workspace/sample.mp4"
ffmpeg -hide_banner -loglevel error -i "$sample" \
  -vn -c:a libmp3lame "$workspace/sample.mp3"
test "$(timeout 20 mediainfo --Inform='General;%Format%' "$workspace/sample.mp4")" = 'MPEG-4'
test "$(timeout 20 mediainfo --Inform='General;%Format%' "$workspace/sample.mp3")" = 'MPEG Audio'

# An incomplete download and unrelated bytes must terminate without a crash.
head -c 2048 "$sample" > "$workspace/incomplete.mkv"
printf 'not a media file\000invalid' > "$workspace/invalid.bin"
for file in "$workspace/incomplete.mkv" "$workspace/invalid.bin"; do
  status=0
  timeout 20 mediainfo --Output=JSON "$file" >/dev/null || status=$?
  test "$status" -le 1
done

if [ -x /filebot/filebot.sh ]; then
  # Exercise FileBot's native library binding, not only the mediainfo executable.
  timeout 60 /filebot/filebot.sh -mediainfo "$sample" \
    --format '{width}x{height}' > "$workspace/filebot.txt"
  grep -Fx '32x32' "$workspace/filebot.txt" >/dev/null
  printf 'ok - FileBot native MediaInfo metadata extraction\n'
fi

printf 'ok - MediaInfo MKV flags, MP4/MP3, Unicode template and incomplete input\n'
SH
