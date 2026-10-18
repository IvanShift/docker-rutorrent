#!/bin/sh
# rTorrent's FileBot hooks only queue work: a slow operator hook and the erase
# cleanup run in the filebot-queue service while SCGI stays responsive, and a
# job interrupted by a restart runs again.
set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
container="rutorrent-filebot-queue-smoke-$$-$(date +%s)"
volume="${container}-config"
cleanup() {
  smoke_remove_run_containers || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
smoke_install_traps
smoke_check_image "$image"

# A registered license fixture bypasses only the license import.
docker volume create "$volume" >/dev/null
smoke_run --rm --network none --entrypoint /bin/sh -v "$volume:/config" "$image" -eu -c '
  mkdir -p /config/filebot/data
  printf "%s\n" smoke-fixture > /config/filebot/data/.license
  printf "%s\n" smoke-fixture > /config/filebot/smoke-license.psm
  printf "%s\n" "#!/bin/sh" "printf \"%s|%s\\n\" \"\$1\" \"\$2\" >> /config/filebot/hook-runs" \
    "sleep 30" "printf \"%s\\n\" \"\$2\" >> /config/filebot/hook-done" > /config/filebot/postdl
  chmod 0755 /config/filebot/postdl
'
smoke_run -d --network none --name "$container" --stop-timeout 60 -v "$volume:/config" \
  --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
  -e FILEBOT_LICENSE=/config/filebot/smoke-license.psm "$image" >/dev/null
wait_for_scgi "$container" 120 || fail 'FileBot queue SCGI readiness'
[ "$(docker exec "$container" s6-svstat -o up /etc/s6.d/filebot-queue)" = true ] || fail 'filebot-queue service is not running'
[ "$(docker exec "$container" stat -c '%U %a' /config/filebot/queue)" = 'torrent 700' ] || fail 'FileBot queue is not a private service directory'

rpc() { docker exec --user 991:991 "$container" php85 /tests/smoke/service-rpc.php "$@"; }
in_container() { docker exec --user 991:991 "$container" sh -c "$1"; }
# An RPC answers within three seconds, including docker exec overhead. The
# former synchronous hooks held the daemon for the whole job, or 10s per erase.
responsive() {
  started=$(date +%s%N)
  rpc "$@" >/dev/null || fail "RPC $1 failed"
  elapsed=$(( ($(date +%s%N) - started) / 1000000 ))
  [ "$elapsed" -lt 3000 ] || fail "RPC $1 waited ${elapsed}ms for FileBot work"
}
# Load a complete fixture and run its configured event.download.finished
# handlers, as rTorrent does on completion.
finish_fixture() {
  hash=$(docker exec --user 991:991 "$container" php85 /tests/smoke/torrent-fixture.php "$1" "/data/$1.torrent")
  rpc load.start '' "/data/$1.torrent" >/dev/null
  complete() { [ "$(rpc d.complete "$hash")" = 1 ]; }
  smoke_wait_until "$container" 30 'fixture hash check' complete || fail "fixture $1 did not complete its hash check"
  responsive event.download.finished "$hash"
}
hook_runs() { [ "$(in_container 'cat /config/filebot/hook-runs 2>/dev/null' | wc -l)" -ge "$1" ]; }

finish_fixture 'queue fixture'
smoke_wait_until "$container" 20 'queued completion' hook_runs 1 || fail 'queued completion did not start'
responsive system.time
in_container 'mkdir -p /data/media/Show/Empty && ln -s /data/missing "/data/media/Show/dangling link" && : > /data/media/kept'
responsive d.erase "$hash"
in_container 'test ! -e /config/filebot/hook-done' || fail 'the slow hook finished before the RPC checks ran'

hook_done() { in_container 'test -e /config/filebot/hook-done'; }
smoke_wait_until "$container" 60 'slow completion job' hook_done || fail 'queued completion did not finish'
[ "$(in_container 'cat /config/filebot/hook-runs')" = '/data/downloads/queue fixture|queue fixture' ] \
  || fail 'completion job received other arguments'
cleaned() { in_container 'test ! -e /data/media/Show && test -f /data/media/kept'; }
smoke_wait_until "$container" 60 'erase cleanup' cleaned || fail 'erase cleanup did not remove the dangling link and empty directories'
logs=$(docker logs "$container" 2>&1)
printf '%s\n' "$logs" | grep -F 'filebot-queue: job=' | grep -F 'outcome=done' | grep -F 'title=queue fixture' >/dev/null \
  || fail 'filebot-queue did not report the completed job'
printf '%s\n' "$logs" | grep -F 'filebot-queue: cleanup outcome=done' >/dev/null || fail 'filebot-queue did not report the cleanup'
in_container 'test -z "$(ls /config/filebot/queue)"' || fail 'FileBot queue kept finished requests'

# An orderly stop ends the running job and keeps it queued for the next start.
finish_fixture 'restart fixture'
smoke_wait_until "$container" 20 'second completion' hook_runs 2 || fail 'second completion did not start'
started=$(date +%s)
docker restart -t 60 "$container" >/dev/null
[ $(( $(date +%s) - started )) -lt 40 ] || fail 'a running FileBot job delayed the container stop'
wait_for_scgi "$container" 120 || fail 'FileBot queue SCGI readiness after restart'
smoke_wait_until "$container" 30 'interrupted completion' hook_runs 3 || fail 'interrupted completion did not run again'
[ "$(in_container 'sed -n 3p /config/filebot/hook-runs')" = '/data/downloads/restart fixture|restart fixture' ] \
  || fail 'interrupted completion ran with other arguments'
in_container 'test "$(cat /config/filebot/hook-done)" = "queue fixture"' || fail 'the interrupted job reported completion'
docker logs "$container" 2>&1 | grep -F 'outcome=stopped' | grep -F 'title=restart fixture' >/dev/null \
  || fail 'filebot-queue did not report the interrupted job'

printf 'ok - FileBot hooks queue work; SCGI stays responsive during a slow job and erase cleanup; an interrupted job runs again\n'
