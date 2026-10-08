#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
scratch=$(mktemp -d)
chmod 0777 "$scratch"
name=smoke-mktorrent-$$
cleanup() { smoke_remove_run_containers || true; rm -rf "$scratch"; }
smoke_install_traps
smoke_run --rm --name "$name" --network none --entrypoint sh \
  --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
  --mount "type=bind,src=$scratch,dst=/proof" "$image" -ec '
    su-exec 991:991 php85 -r '\''file_put_contents("/proof/small", str_repeat("payload-fixture", 328)."bytes"); file_put_contents("/proof/multiple", str_repeat("multi-piece", 190654)."extra");'\''
    exec su-exec 991:991 sh /tests/smoke/fixtures/mktorrent/run.sh
  '
python3 "$smoke_dir/mktorrent-verify.py" "$scratch"
