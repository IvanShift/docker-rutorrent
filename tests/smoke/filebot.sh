#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s IMAGE\n' "$0" >&2
  exit 64
fi

image=$1

docker image inspect "$image" >/dev/null 2>&1 || {
  printf 'Image not found: %s\n' "$image" >&2
  exit 1
}

docker run --rm --entrypoint /bin/sh "$image" -eu -c '
  filebot_version=$(/filebot/filebot.sh -version)
  printf "%s\n" "$filebot_version" | grep -F "FileBot 5.3.0" >/dev/null
  fpcalc -version
  find --version | grep -F "GNU findutils" >/dev/null
'

printf 'ok - FileBot, Chromaprint, and GNU findutils smoke\n'

"$(dirname -- "$0")/mediainfo.sh" "$image"
