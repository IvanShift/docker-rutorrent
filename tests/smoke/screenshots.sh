#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
smoke_check_image "$1"

# No host outputs or service startup: Docker removes the disposable container
# and its anonymous /config and /data volumes on either success or failure.
smoke_run --rm --network none --user 991:991 \
  --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
  --mount "type=bind,src=$smoke_dir/../fixtures/media,dst=/fixtures,readonly" \
  --entrypoint php85 "$1" /tests/smoke/screenshots.php /fixtures
