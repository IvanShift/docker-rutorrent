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
  /filebot/filebot.sh -version
  fpcalc -version
  find --version | grep -F "GNU findutils" >/dev/null
'

printf 'ok - FileBot, Chromaprint, and GNU findutils smoke\n'
