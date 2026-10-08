#!/bin/sh
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 IMAGE [CANDIDATE_ROOTFS]" >&2
  exit 64
fi
image=$1
container="smoke-services-lifecycle-$$"
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$image"
cleanup() { docker rm -f -v "$container" >/dev/null 2>&1 || true; }
smoke_install_traps
if [ "$#" = 2 ]; then
  smoke_run -d --name "$container" --stop-timeout 60 \
    --mount "type=bind,src=$2,dst=/candidate,readonly" \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    --entrypoint /bin/sh "$image" -c 'cp -r /candidate/. /; exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d' >/dev/null
else
  smoke_run -d --name "$container" --stop-timeout 60 \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    "$image" >/dev/null
fi
ready() { wait_for_health "$container" || fail 'service readiness timeout'; }
rpc() { docker exec --user 991:991 "$container" php85 /tests/smoke/service-rpc.php "$@"; }
ready
hash=$(docker exec --user 991:991 "$container" php85 /tests/smoke/torrent-fixture.php service-fixture /data/service-fixture.torrent)
rpc load.normal '' /data/service-fixture.torrent >/dev/null || fail 'load test torrent'
rpc d.custom1.set "$hash" service-session-label >/dev/null || fail 'set session label'
[ "$(rpc d.custom1 "$hash")" = service-session-label ] || fail 'read session label before stop'
docker stop "$container" >/dev/null
[ "$(docker inspect -f '{{.State.ExitCode}}' "$container")" = 0 ] || fail 'orderly shutdown exit status'
docker start "$container" >/dev/null
ready
[ "$(rpc d.custom1 "$hash")" = service-session-label ] || fail 'session label after stop/start'
echo 'ok - docker stop saves session state and restart restores it'
# Pin native erase/re-add counter recovery in the image CI gate (SB-09).
if ! docker exec "$container" test -f /etc/rtorrent-legacy; then
  docker exec --user 991:991 "$container" cp /data/service-fixture.torrent /data/service-reload.torrent
  rpc d.stop "$hash" >/dev/null || fail 'stop before same-hash erase'
  rpc d.start "$hash" >/dev/null || fail 'start before same-hash erase'
  rpc d.erase "$hash" >/dev/null || fail 'erase before same-hash reload'
  # Simulate a delayed trusted writer's old record, using the native owner's
  # historical generation8 fixture. No foreign UID or external data is involved.
  docker exec --user 991:991 "$container" php85 -r '
    $p="/data/.session/.rutorrent-stop-intents/".$argv[1];
    if(file_exists($p))exit(1);
    file_put_contents($p,"0000000000000008\n");chmod($p,0600);
  ' "$hash" || fail 'seed owned stale stop-counter fixture'
  rpc load.start '' /data/service-reload.torrent >/dev/null || fail 'reload same hash'
  [ "$(rpc d.hash "$hash")" = "$hash" ] || fail 'same hash after reload'
  [ "$(rpc d.state "$hash")" = 1 ] || fail 'running state before same-hash restart'
  rpc d.custom1.set "$hash" same-hash-session-label >/dev/null || fail 'set same-hash label'
  rpc session.save >/dev/null || fail 'save same-hash session'
  docker stop "$container" >/dev/null
  [ "$(docker inspect -f '{{.State.ExitCode}}' "$container")" = 0 ] || fail 'same-hash orderly shutdown'
  docker start "$container" >/dev/null
  ready
  [ "$(rpc d.state "$hash")" = 1 ] || fail 'same-hash torrent stopped after restart'
  [ "$(rpc d.custom1 "$hash")" = same-hash-session-label ] || fail 'same-hash label after restart'
  echo 'ok - native erase/same-hash stale-counter reload/save/restart remains running'
else
  echo 'ok - same-hash native counter gate # SKIP explicit legacy daemon contract'
fi
pid=$(rpc system.pid)
docker exec "$container" kill -KILL "$pid"
wait_for_exit "$container" || fail 'daemon crash did not stop the container'
status=$(docker inspect --format '{{.State.ExitCode}}' "$container")
[ "$status" = 1 ] || fail "unexpected daemon failure exit status $status"
echo 'ok - daemon crash exits the container with status 1'
