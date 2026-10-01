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
name=rem4-native-metadata-$variant-$$
cleanup() { docker rm -f -v "$name" >/dev/null 2>&1 || :; }
smoke_install_traps
binary=${3:-}
set -- --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly"
if [ -n "$binary" ]; then
  [ -x "$binary" ] || fail 'native binary source is not executable'
  set -- "$@" --mount "type=bind,src=$binary,dst=/usr/local/bin/rtorrent,readonly"
fi
fork=/rutorrent/app
if [ -n "${RUTORRENT_SOURCE:-}" ]; then
  set -- "$@" --mount "type=bind,src=$RUTORRENT_SOURCE,dst=/candidate-fork,readonly"
  fork=/candidate-fork
fi
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
case "$variant:$actual" in native:0.16.24|legacy:0.9.8) ;; *) fail "unexpected daemon version: $actual" ;; esac
docker exec --user 991:991 "$name" php85 "$fork/tests/plugins/retrackers/metadata-insert-live.php" "$variant"
