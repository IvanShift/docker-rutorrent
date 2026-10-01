#!/bin/sh
# Shared launcher for checks of the native binaries shipped in the image.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
[ "$#" -eq 3 ] || exit 64
scenario=$1
image=$2
platform=$3
smoke_check_image "$image"
set --
[ -z "$platform" ] || set -- --platform "$platform"
smoke_run --rm "$@" --network none --read-only \
  --tmpfs /tmp:rw,nosuid,size=16m \
  --mount "type=bind,src=$smoke_dir/native-image-check.sh,dst=/native-image-check.sh,readonly" \
  --entrypoint /bin/sh "$image" -eu /native-image-check.sh "$scenario"
