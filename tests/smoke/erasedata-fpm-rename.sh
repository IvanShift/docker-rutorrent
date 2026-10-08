#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
smoke_check_image "$1"
container="smoke-migration-fpm-$$"
cleanup() { smoke_remove_run_containers || true; }
smoke_install_traps
smoke_run --rm --name "$container" --network none --entrypoint /bin/sh \
  --mount "type=bind,src=$smoke_dir/fixtures/erasedata-fpm,dst=/proof,readonly" \
  "$1" /proof/run.sh
