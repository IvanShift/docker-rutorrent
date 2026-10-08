#!/bin/sh
# Run only synthetic bounded requests in a fresh, privately owned container.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
case_kind=${HEALTH_CAPACITY_CASE:-all}
case "$case_kind" in all|uploads) ;; *) exit 64 ;; esac
container="smoke-health-capacity-$$-$(date +%s)"
container_id=
requests_pid=
workspace=$(mktemp -d)
cleanup() {
  if [ -n "$requests_pid" ]; then kill "$requests_pid" 2>/dev/null || :; wait "$requests_pid" 2>/dev/null || :; fi
  smoke_remove_run_containers 2>/dev/null || :
  rm -rf "$workspace"
}
smoke_install_traps
smoke_check_image "$image"
if [ -n "${HEALTH_CONFIG_ROOT:-}" ]; then
  container_id=$(smoke_run -d --name "$container" --stop-timeout 60 \
    --env HTTP_AUTH=false --env ALLOW_UNAUTHENTICATED_HTTP=true \
    --env RU_PUBLIC_URL=https://public.example.test/rutorrent/ --env RUTORRENT_TEST_PRIVATE_ENV=synthetic \
    --mount "type=bind,src=$smoke_dir/health-env.php,dst=/health-env-client.php,readonly" \
    --mount "type=bind,src=$HEALTH_CONFIG_ROOT,dst=/health-candidate,readonly" \
    --entrypoint /bin/sh "$image" -eu -c '
      cp /health-candidate/nginx.conf /etc/nginx/nginx.conf
      cp /health-candidate/www.conf /etc/php85/php-fpm.d/www.conf
      cp /health-candidate/www.ini /etc/php85/conf.d/www.ini
      cp /health-candidate/fastcgi-health.conf /etc/nginx/fastcgi-health.conf
      exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d
    ')
else
  container_id=$(smoke_run -d --name "$container" --stop-timeout 60 \
    --env HTTP_AUTH=false --env ALLOW_UNAUTHENTICATED_HTTP=true \
    --env RU_PUBLIC_URL=https://public.example.test/rutorrent/ --env RUTORRENT_TEST_PRIVATE_ENV=synthetic \
    --mount "type=bind,src=$smoke_dir/health-env.php,dst=/health-env-client.php,readonly" "$image")
fi
printf 'resource container=%s image=%s owner=%s\n' "$container_id" "$(docker inspect -f '{{.Image}}' "$container_id")" "$smoke_owner"
printf 'resource mounts=%s\n' "$(docker inspect -f '{{json .Mounts}}' "$container_id")"
wait_for_health "$container" || fail 'health capacity fixture not ready'
docker exec "$container" sh -eu -c '
  printf "<?php echo json_encode([\"public\"=>getenv(\"RU_PUBLIC_URL\"),\"private\"=>getenv(\"RUTORRENT_TEST_PRIVATE_ENV\")]);\n" > /tmp/health-capacity-env.php
'
if [ -n "${HEALTH_CONFIG_ROOT:-}" ] || docker exec "$container" test -S /run/php/php85-health.sock; then
  docker exec "$container" php85 /health-env-client.php || fail 'pool environment boundary'
fi
docker exec "$container" sh -eu -c '
  mkdir /rutorrent/app/plugins/health-capacity-fixture
  printf "<?php touch(\"/tmp/health-capacity-ready-\".(int)\$_GET[\"id\"]);sleep(30);echo \"done\";\n" > /rutorrent/app/plugins/health-capacity-fixture/busy.php
  printf "<?php echo count(\$_FILES[\"torrent_file\"][\"name\"] ?? []);\n" > /rutorrent/app/plugins/health-capacity-fixture/upload.php
  printf "one" > /tmp/health-capacity-upload
'
if [ "$case_kind" = all ]; then
docker exec "$container" sh -eu -c '
  i=1
  while [ "$i" -le 15 ]; do
    curl --noproxy "*" -fsS --max-time 40 "http://127.0.0.1:8080/plugins/health-capacity-fixture/busy.php?id=$i" >/dev/null &
    i=$((i + 1))
  done
  wait
' > "$workspace/requests.log" 2>&1 &
requests_pid=$!
deadline=$(( $(date +%s) + 20 ))
while :; do
  ready=$(docker exec "$container" sh -c 'find /tmp -maxdepth 1 -name "health-capacity-ready-*" | wc -l')
  [ "$ready" -eq 15 ] && break
  [ "$(date +%s)" -lt "$deadline" ] || fail "did not fill all 15 regular FPM workers (ready=$ready)"
  sleep 1
done
body=$(docker exec "$container" curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/healthz) || fail 'health blocked behind 15 legitimate long requests'
[ "$body" = ok ] || fail 'health pool changed config check response'
echo 'ok - dedicated health pool responds with all 15 normal workers busy'
wait "$requests_pid" || { cat "$workspace/requests.log" >&2; fail 'bounded regular requests failed'; }
requests_pid=
fi
count=$(docker exec "$container" sh -eu -c '
  set --
  i=1
  while [ "$i" -le 32 ]; do set -- "$@" -F "torrent_file[]=@/tmp/health-capacity-upload;filename=upload-$i.torrent"; i=$((i + 1)); done
  curl --noproxy "*" -fsS --max-time 10 "$@" http://127.0.0.1:8080/plugins/health-capacity-fixture/upload.php
')
[ "$count" = 32 ] || fail "multipart upload truncated after $count files"
echo 'ok - actual multipart request accepts 32 files beyond previous default20'
echo 'health capacity smoke passed'
