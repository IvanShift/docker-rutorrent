#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || exit 64
image=$1
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$image"
image_version=$(docker image inspect "$image" --format '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}')
case "${image_version#v}" in 0.16.25) ;; *) echo 'ok - native service gate # SKIP image lacks reviewed .25 contract'; exit 0 ;; esac
expected_version=${SMOKE_EXPECTED_RTORRENT_VERSION:-$image_version}
case "${expected_version#v}" in
  0.16.25) ;;
  0.9.8) echo 'ok - reviewed .25 service gate # SKIP explicit legacy contract'; exit 0 ;;
  *) echo 'ok - reviewed .25 service gate # SKIP published native version has no new .25 gate contract'; exit 0 ;;
esac
container="smoke-service-schedule-errors-$$"
owned_id=''
cleanup() {
  [ -n "$owned_id" ] || return 0
  current=$(docker inspect "$container" --format '{{.Id}} {{index .Config.Labels "smoke.service-schedule.owner"}}' 2>/dev/null || true)
  [ "$current" = "$owned_id $$" ] || return 0
  docker stop --time 60 "$owned_id" >/dev/null 2>&1 || true
  docker rm -v "$owned_id" >/dev/null 2>&1 || true
}
smoke_install_traps
image_id=$(docker image inspect "$image" --format '{{.Id}}')
if [ "$#" = 2 ]; then
  native=$(CDPATH= cd -- "$(dirname -- "$2")" && pwd)/$(basename -- "$2")
  owned_id=$(smoke_run -d --name "$container" --label "smoke.service-schedule.owner=$$" --stop-timeout 60 \
    --mount "type=bind,src=$native,dst=/candidate-rtorrent,readonly" \
    --mount "type=bind,src=$smoke_dir/fixtures/service-schedule,dst=/tests/schedules,readonly" \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    --entrypoint /bin/sh "$image_id" -c 'cp /candidate-rtorrent /usr/local/bin/rtorrent; exec /bin/sh /tests/schedules/run.sh')
else
  owned_id=$(smoke_run -d --name "$container" --label "smoke.service-schedule.owner=$$" --stop-timeout 60 \
    --mount "type=bind,src=$smoke_dir/fixtures/service-schedule,dst=/tests/schedules,readonly" \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    --entrypoint /bin/sh "$image_id" /tests/schedules/run.sh)
fi
attempt=0
until docker exec --user 991:991 "$container" php85 /tests/smoke/service-runtime-rpc.php system.pid >/dev/null 2>&1; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 30 ] || { docker logs "$container" >&2; fail 'schedule fixture SCGI readiness'; }
  sleep 1
done
docker exec --user 991:991 "$container" php85 /tests/smoke/service-runtime-rpc.php schedule '' service_error_fixture 0 0 'nonexistent_fixture_command=' >/dev/null
expected='Scheduled command failed: key=service_error_fixture reason=input-error'
attempt=0
until docker logs "$container" 2>&1 | grep -Fq "$expected"; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 6 ] || fail 'scheduled command failure is not classified in container logs'
  sleep 1
done
docker exec "$container" grep -Fq "$expected" /tmp/service-scheduler.log \
  || fail 'scheduled command failure is missing from configured error output'
docker stop --time 60 "$container" >/dev/null
[ "$(docker inspect -f '{{.State.ExitCode}}' "$container")" = 0 ] || fail 'schedule fixture orderly stop'
# The classified record persists after the daemon and logging streams close.
docker cp "$container:/tmp/service-scheduler.log" - | tar -xOf - | grep -Fq "$expected" \
  || fail 'scheduled diagnostic lost after daemon stop'
printf 'ok - actual invalid schedule has classified container and retained error output\n'
