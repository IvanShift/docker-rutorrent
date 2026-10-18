#!/bin/sh
set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
suffix="$$-$(date +%s)"
container="smoke-network-${suffix}"
workspace=$(mktemp -d)

cleanup() {
  smoke_remove_run_containers || true
  rm -rf "$workspace"
}
smoke_install_traps
smoke_check_image "$image"

# A task-local bootstrap can copy candidate configs into a baseline image.
# CI omits this variable and exercises the configs baked into its image.
if [ -n "${NETWORK_CANDIDATE_START:-}" ]; then
  repo_root=$(CDPATH= cd -- "$smoke_dir/../.." && pwd)
  smoke_run -d --rm --name "$container" --env ENABLE_RPC2=true \
    --mount "type=bind,src=${repo_root}/rootfs,dst=/candidate,readonly" \
    --mount "type=bind,src=${NETWORK_CANDIDATE_START},dst=/network-start.sh,readonly" \
    --entrypoint /bin/sh "$image" /network-start.sh >/dev/null
else
  smoke_run -d --rm --name "$container" --env ENABLE_RPC2=true "$image" >/dev/null
fi
wait_for_health "$container" || fail 'network regression container not ready'

docker exec "$container" sh -eu -c '
  hash=$(openssl passwd -6 network-pass)
  printf "network-user:%s\n" "$hash" > /config/nginx/passwd/rutorrent_passwd
  chown "0:$GID" /config/nginx/passwd/rutorrent_passwd
  chmod 640 /config/nginx/passwd/rutorrent_passwd
  mkdir -p /rutorrent/app/plugins/network-third-party
  printf "<?php echo \"plugin-ok\";\n" > /rutorrent/app/plugins/network-third-party/entry.php
  printf "<?php echo getenv(\"PATH\").\"\\n\".ini_get(\"cgi.fix_pathinfo\");\n" > /rutorrent/app/plugins/network-third-party/path.php
  printf "console.log(1);\n" > /rutorrent/app/plugins/network-third-party/change.js
  dd if=/dev/zero of=/tmp/network-upload bs=1048576 count=11 2>/dev/null
' || fail 'prepare authenticated network fixture'

request() { http_status "$container" "$1" -u network-user:network-pass; }
expect_status() {
  expected=$1
  path=$2
  [ "$(request "$path")" = "$expected" ] || fail "HTTP $expected for $path"
}

service_ids=$(docker exec "$container" sh -c 'printf "%s:%s" "$UID" "$GID"')
docker exec -u "$service_ids" "$container" nginx -t || fail 'service identity nginx config validation'
docker exec -u "$service_ids" "$container" php-fpm85 -t || fail 'service identity FPM config validation'
[ "$(docker exec "$container" curl --noproxy '*' -fsS --max-time 2 \
  http://127.0.0.1:8080/healthz)" = ok ] || fail 'PHP/config-backed health response'
expect_status 403 /healthz-plugins
container_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")
expect_status 403 "http://${container_ip}:8080/healthz"
expect_status 403 "http://${container_ip}:8080/healthz-plugins"
docker exec -u "$service_ids" "$container" curl --noproxy '*' --unix-socket /run/nginx/plugin-health.sock \
  -fsS --max-time 20 -u network-spoof:wrong http://localhost/healthz-plugins >/dev/null || fail 'authenticated internal plugin health response'
docker exec "$container" test ! -e /rutorrent/app/share/users/network-spoof \
  || fail 'internal bootstrap accepted caller profile'

for path in /conf /conf/config.php /share /share/new%0Aline/file.php \
  /tasks/check.php /tests/plugins/_task/TaskPidfdImageSmoke.php /tools/check.php /.gitignore \
  /php/Snoopy.class.inc /plugins/_task/kill-verified.c; do
  expect_status 403 "$path"
done
expect_status 404 /php/missing-network-file.php
expect_status 404 /plugins/throttle/action.php/plugins/httprpc/action.php
expect_status 200 /plugins/network-third-party/entry.php
php_environment=$(docker exec "$container" curl --noproxy '*' -fsS --max-time 3 \
  -u network-user:network-pass http://127.0.0.1:8080/plugins/network-third-party/path.php)
[ "$php_environment" = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
0' ] || fail 'FPM PATH or fix_pathinfo setting'
worker_paths=$(docker exec "$container" awk '/^location = / { print $3 }' /etc/nginx/cli-only.conf)
[ -n "$worker_paths" ] || fail 'CLI policy is empty'
for path in $worker_paths; do
  docker exec "$container" test -f "/rutorrent/app${path}" || fail "missing CLI worker $path"
  expect_status 403 "$path"
done
printf 'ok - exact CLI policy and third-party PHP routing\n'

[ "$(http_status "$container" /php/getplugins.php -u network-user:network-pass \
  -H 'X-Requested-With: XMLHttpRequest' -H 'Origin: http://127.0.0.1:8080')" = 200 ] \
  || fail 'normal plugin bootstrap no longer works'
docker exec "$container" curl --noproxy '*' -fsS --max-time 20 -u network-user:network-pass \
  -H 'Content-Type: application/octet-stream' --data-binary @/tmp/network-upload \
  http://127.0.0.1:8080/plugins/network-third-party/entry.php > "$workspace/upload" || fail '11 MiB PHP upload request'
[ "$(cat "$workspace/upload")" = plugin-ok ] || fail '11 MiB PHP upload rejected'
printf 'ok - paired upload limits accept 11 MiB\n'

docker exec "$container" curl --noproxy '*' -fsS --max-time 3 -u network-user:network-pass \
  -D - http://127.0.0.1:8080/plugins/network-third-party/change.js > "$workspace/js" || fail 'static JS headers request'
grep -i '^Cache-Control: no-cache' "$workspace/js" >/dev/null || fail 'JS must revalidate'
etag=$(sed -n 's/^[Ee][Tt][Aa][Gg]: \(.*\)\r$/\1/p' "$workspace/js")
[ -n "$etag" ] || fail 'missing static validator'
docker exec "$container" sh -c 'printf "console.log(222);\n" > /rutorrent/app/plugins/network-third-party/change.js'
[ "$(http_status "$container" /plugins/network-third-party/change.js -u network-user:network-pass \
  -H "If-None-Match: $etag")" = 200 ] || fail 'updated JS validator stayed stale'
docker exec "$container" curl --noproxy '*' -fsS --max-time 3 -u network-user:network-pass \
  -D - http://127.0.0.1:8080/php/getsettings.php > "$workspace/php" || fail 'PHP security headers request'
for header in X-Frame-Options X-Content-Type-Options Referrer-Policy; do
  [ "$(grep -ic "^${header}:" "$workspace/php")" = 1 ] || fail "duplicate PHP $header"
done
# Upload results render in a same-origin iframe; a global DENY would hide them.
grep -i '^X-Frame-Options: SAMEORIGIN' "$workspace/php" >/dev/null || fail 'PHP frame policy must stay SAMEORIGIN'
grep -i '^Referrer-Policy: same-origin' "$workspace/php" >/dev/null || fail 'referrer policy'
if grep -i '^X-XSS-Protection:' "$workspace/php" >/dev/null; then fail 'obsolete XSS header'; fi
printf 'ok - code assets revalidate and PHP security headers have one owner\n'

docker exec "$container" sh -eu -c '
  master=$(cat /run/nginx/nginx.pid)
  worker=$(pgrep -P "$master" | head -n 1)
  kill -KILL "$worker"
  sleep 1
  grep -q "worker process .* exited on signal 9" /tmp/nginx-error.log
' || fail 'nginx worker lifecycle error log'
printf 'ok - nginx master lifecycle errors use the rotated main log\n'

docker exec "$container" sh -eu -c '
  mode=$(stat -c %a /run/rtorrent/rtorrent.sock)
  [ "$mode" = 600 ]
  grep -q "^schedule = low_diskspace,15,60,close_low_diskspace=1000M$" /config/rtorrent/.rtorrent.rc
  grep -q "^schedule = untied_directory,1,10,\"stop_untied=\"$" /config/rtorrent/.rtorrent.rc
  ! grep -q "log.add_output = \"critical\"" /config/rtorrent/.rtorrent.rc
' || fail 'SCGI socket mode and stock scheduler configuration'

# Pause FPM without triggering the container's fail-fast service supervision.
docker exec "$container" sh -eu -c '
  master=$(cat /run/php/php85-fpm.pid)
  pids="$master $(pgrep -P "$master" || :)"
  printf "%s\n" "$pids" > /tmp/network-fpm-pids
  kill -STOP $pids
' || fail 'suspend FPM fixture'
if docker exec "$container" curl --noproxy '*' -fsS --max-time 2 \
  http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
  fail 'health did not detect paused FPM'
fi
docker exec "$container" sh -c 'kill -CONT $(cat /tmp/network-fpm-pids)' || fail 'resume FPM fixture'
wait_for_health "$container" || fail 'FPM did not recover'

# Restart FPM to drop cached bytecode before checking a broken operator config.
docker exec "$container" sh -eu -c '
  mv /config/rutorrent/conf/config.php /config/rutorrent/conf/config.php.network-saved
  printf "<?php deliberate invalid config;\n" > /config/rutorrent/conf/config.php
' || fail 'prepare invalid operator config fixture'
docker exec "$container" sh -c 'kill -USR2 "$(cat /run/php/php85-fpm.pid)"' || fail 'reload FPM fixture'
sleep 1
[ "$(http_status "$container" /healthz)" = 500 ] || fail 'health did not detect broken PHP config'
docker exec "$container" sh -c 'mv /config/rutorrent/conf/config.php.network-saved /config/rutorrent/conf/config.php' || fail 'restore operator config fixture'
docker exec "$container" sh -c 'kill -USR2 "$(cat /run/php/php85-fpm.pid)"' || fail 'reload FPM fixture'
wait_for_health "$container" || fail 'healthy config did not recover'
printf 'ok - health rejects paused FPM and invalid operator config\n'
printf 'network regression smoke passed\n'
