#!/bin/sh
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 IMAGE [CANDIDATE_ROOTFS]" >&2
  exit 64
fi
image=$1
container="rem4-services-lifecycle-$$"
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cleanup() { docker rm -f -v "$container" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
fail() { echo "not ok - $*" >&2; docker logs "$container" >&2 || true; exit 1; }
if [ "$#" = 2 ]; then
  docker run -d --name "$container" --stop-timeout 60 \
    --mount "type=bind,src=$2,dst=/candidate,readonly" \
    --mount "type=bind,src=$smoke_dir/service-rpc.php,dst=/tests/service-rpc.php,readonly" \
    --entrypoint /bin/sh "$image" -c 'cp -r /candidate/. /; exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d' >/dev/null
else
  docker run -d --name "$container" --stop-timeout 60 \
    --mount "type=bind,src=$smoke_dir/service-rpc.php,dst=/tests/service-rpc.php,readonly" \
    "$image" >/dev/null
fi
ready() {
  attempts=0
  while [ "$attempts" -lt 60 ]; do
    if docker exec "$container" /usr/local/bin/rutorrent-healthcheck >/dev/null 2>&1; then return 0; fi
    [ "$(docker inspect -f '{{.State.Running}}' "$container")" = true ] || fail 'container exited before readiness'
    attempts=$((attempts + 1))
    sleep 1
  done
  fail 'service readiness timeout'
}
rpc() { docker exec --user 991:991 "$container" php85 /tests/service-rpc.php "$@"; }
ready
hash=$(docker exec --user 991:991 "$container" php85 -r '$info = "d6:lengthi1e4:name15:service-fixture12:piece lengthi16384e6:pieces20:" . str_repeat("\0", 20) . "e"; file_put_contents("/data/service-fixture.torrent", "d4:info" . $info . "e"); echo strtoupper(sha1($info));')
rpc load.normal '' /data/service-fixture.torrent >/dev/null || fail 'load test torrent'
rpc d.custom1.set "$hash" service-session-label >/dev/null || fail 'set session label'
[ "$(rpc d.custom1 "$hash")" = service-session-label ] || fail 'read session label before stop'
docker stop "$container" >/dev/null
[ "$(docker inspect -f '{{.State.ExitCode}}' "$container")" = 0 ] || fail 'orderly shutdown exit status'
docker start "$container" >/dev/null
ready
[ "$(rpc d.custom1 "$hash")" = service-session-label ] || fail 'session label after stop/start'
echo 'ok - docker stop saves session state and restart restores it'
pid=$(rpc system.pid)
docker exec "$container" kill -KILL "$pid"
status=$(docker wait "$container")
[ "$status" = 1 ] || fail "unexpected daemon failure exit status $status"
echo 'ok - daemon crash exits the container with status 1'
