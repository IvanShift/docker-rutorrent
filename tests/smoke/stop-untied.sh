#!/bin/sh
# Exercise the packaged untied-directory schedule against a real daemon.
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { echo "Usage: $0 IMAGE [RC_SOURCE]" >&2; exit 64; }
image=$1
rc_source=${2:-}
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$image"
container="smoke-stop-untied-$$"
owned_id=''
cleanup() {
  [ -n "$owned_id" ] || return 0
  current=$(docker inspect "$container" --format '{{.Id}} {{index .Config.Labels "smoke.stop-untied.owner"}}' 2>/dev/null || true)
  [ "$current" = "$owned_id $$" ] || return 0
  docker stop --time 60 "$owned_id" >/dev/null 2>&1 || true
  docker rm -v "$owned_id" >/dev/null 2>&1 || true
}
smoke_install_traps
set -- -d --name "$container" --label "smoke.stop-untied.owner=$$" --network none --stop-timeout 60 \
  --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
  --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
  --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly"
if [ -n "$rc_source" ]; then
  set -- "$@" --mount "type=bind,src=$rc_source,dst=/usr/local/share/rutorrent-defaults/rtorrent.rc,readonly"
fi
owned_id=$(smoke_run "$@" "$image")
rpc() { docker exec --user 991:991 "$container" php85 /tests/smoke/service-rpc.php "$@"; }
# This gate observes the daemon schedule, independent of image HTTP health helpers.
attempt=0
until rpc system.pid >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 40 ] || fail 'stop_untied daemon SCGI readiness'
  [ "$(docker inspect --format '{{.State.Running}}' "$owned_id")" = true ] || fail 'stop_untied daemon stopped before readiness'
  sleep 1
done
fixture() { docker exec --user 991:991 "$container" php85 /tests/smoke/torrent-fixture.php "$@"; }
watch_hash=$(fixture untied-watch /data/.watch/untied.torrent)
other_hash=$(fixture untied-other /data/other/untied.torrent)
retained_hash=$(fixture untied-retained /data/other/retained.torrent)
rpc load.start '' /data/other/untied.torrent >/dev/null
rpc load.start '' /data/other/retained.torrent >/dev/null
# The watch seed must be loaded by the packaged watch schedule itself.
attempt=0
until [ "$(rpc d.is_active "$watch_hash" 2>/dev/null || true)" = 1 ] \
  && [ "$(rpc d.is_active "$other_hash")" = 1 ] \
  && [ "$(rpc d.is_active "$retained_hash")" = 1 ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 25 ] || fail 'all tied fixture torrents must be active before removal'
  sleep 1
done
[ "$(rpc d.tied_to_file "$watch_hash")" = /data/.watch/untied.torrent ] || fail 'watch torrent is not tied'
[ "$(rpc d.tied_to_file "$other_hash")" = /data/other/untied.torrent ] || fail 'outside-watch torrent is not tied'
docker exec --user 991:991 "$container" rm /data/.watch/untied.torrent /data/other/untied.torrent
attempt=0
until [ "$(rpc d.state "$watch_hash")" = 0 ] && [ "$(rpc d.state "$other_hash")" = 0 ]; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 20 ] || fail 'packaged stop_untied schedule did not stop missing tied sources in both directories'
  sleep 1
done
[ "$(rpc d.is_active "$watch_hash")" = 0 ] || fail 'watch torrent remains active'
[ "$(rpc d.is_active "$other_hash")" = 0 ] || fail 'outside-watch torrent remains active'
[ "$(rpc d.is_active "$retained_hash")" = 1 ] || fail 'torrent with retained tied source was stopped'
[ "$(rpc d.tied_to_file "$retained_hash")" = /data/other/retained.torrent ] || fail 'retained tied source changed'
printf 'ok - packaged stop_untied schedule stops missing watch and non-watch sources, preserving retained source\n'
