#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  printf 'Usage: %s IMAGE native|legacy [NATIVE_BINARY]\n' "$0" >&2
  exit 64
fi
image=$1
variant=$2
case "$variant" in native|legacy) ;; *) exit 64 ;; esac
[ "$#" -ne 3 ] || [ "$variant" = native ] || exit 64
smoke_check_image "$image"
name=smoke-native-metadata-$variant-$$
cleanup() { docker rm -f -v "$name" >/dev/null 2>&1 || :; }
smoke_install_traps
binary=${3:-}
set -- --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly"
# A local test-only fixture refresh must leave all packaged product actors untouched.
if [ -n "${METADATA_WITNESS_SOURCE:-}" ]; then
  [ -f "$METADATA_WITNESS_SOURCE" ] || fail 'metadata witness source is not a regular file'
  set -- "$@" --mount "type=bind,src=$METADATA_WITNESS_SOURCE,dst=/rutorrent/app/tests/plugins/retrackers/metadata-insert-live.php,readonly"
fi
if [ -n "$binary" ]; then
  [ -x "$binary" ] || fail 'native binary source is not executable'
  set -- "$@" --mount "type=bind,src=$binary,dst=/usr/local/bin/rtorrent,readonly"
fi
fork=/rutorrent/app
if [ -n "${RUTORRENT_SOURCE:-}" ]; then
  set -- "$@" --mount "type=bind,src=$RUTORRENT_SOURCE,dst=/candidate-fork,readonly"
  fork=/candidate-fork
fi
# A release candidate must package its own canonical app/native contract test.
smoke_run --rm --network none "$@" --entrypoint /bin/sh "$image" -eu -c \
  'test -r "$1/tests/plugins/retrackers/metadata-insert-live.php"' sh "$fork" \
  || fail 'fork metadata witness is not packaged; publish the compatible app source before image release'
smoke_run --rm -d --name "$name" --network none "$@" \
  --entrypoint /bin/sh "$image" /tests/smoke/retrackers-metadata-boot.sh "$variant" >/dev/null
ready=false
deadline=$(( $(date +%s) + 120 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  if docker exec --user 991:991 "$name" timeout 2 php85 /tests/smoke/service-rpc.php system.pid >/dev/null 2>&1; then
    ready=true
    break
  fi
  [ "$(docker inspect -f '{{.State.Running}}' "$name")" = true ] || {
    smoke_diagnostics "$name"
    fail 'metadata fixture daemon exited'
  }
  sleep 1
done
[ "$ready" = true ] || { smoke_diagnostics "$name"; fail 'metadata fixture daemon readiness timed out'; }
actual=$(docker exec --user 991:991 "$name" php85 /tests/smoke/service-rpc.php system.client_version)
expected=0.9.8
if [ "$variant" = native ]; then
  expected=${SMOKE_EXPECTED_RTORRENT_VERSION:-$(sed -n 's/^ARG RTORRENT_BRANCH=v//p' "$smoke_dir/../../Dockerfile")}
fi
[ "$actual" = "$expected" ] || fail "unexpected daemon version: expected $expected, got $actual"
docker exec --user 991:991 "$name" php85 "$fork/tests/plugins/retrackers/metadata-insert-live.php" "$variant"
