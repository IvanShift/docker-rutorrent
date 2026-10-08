#!/bin/sh
# Real-container runtime contracts: a named GID reaches services as its decimal
# ID (the watchdog stays enabled), nginx keeps the standard combined access
# format, both FPM pools keep argv derivation off even when an ini file enables
# it, and monitor publication refusals carry the monitor's own names.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
fixtures=$smoke_dir/fixtures/runtime-contracts
container=''
cleanup() {
  smoke_remove_run_containers force || fail 'refusing or failing cleanup of the containers of this run'
  container=''
}
smoke_install_traps
container=$(smoke_run -d --network none --stop-timeout 60 \
  --mount "type=bind,src=$fixtures/argv-on.ini,dst=/etc/php85/conf.d/zz-runtime-contracts-argv-on.ini,readonly" \
  --mount "type=bind,src=$fixtures,dst=/tests/runtime-contracts,readonly" \
  -e UID=0991 -e GID=users -e HTTP_AUTH=false -e ALLOW_UNAUTHENTICATED_HTTP=true \
  -e HTTP_ALLOWED_HOSTS=nas.example -e RUTORRENT_WATCHDOG_INTERVAL_SECONDS=1 \
  -e RUTORRENT_WATCHDOG_START_SECONDS=0 "$image")
if [ -n "${SMOKE_RESOURCE_LEDGER:-}" ]; then
  printf 'container %s owner %s image %s\n' "$container" "$smoke_owner" "$image" >> "$SMOKE_RESOURCE_LEDGER"
fi
wait_for_health "$container" 60 || fail 'named-group container did not start'

watchdog_healthy() {
  docker exec "$container" sh -c 'grep -q "\"classification\":\"healthy\"" /run/rutorrent-watchdog/.service-health.json' 2>/dev/null
}
smoke_wait_until "$container" 30 'watchdog publication' watchdog_healthy \
  || fail 'watchdog did not publish a healthy state with a named GID'
[ "$(docker exec "$container" s6-svstat -o up /etc/s6.d/service-watchdog)" = true ] || fail 'watchdog is not running with a named GID'
logs=$(docker logs "$container" 2>&1)
if printf '%s\n' "$logs" | grep -F 'invalid-service-identity' >/dev/null; then fail 'watchdog refused the startup-normalized identity'; fi
if printf '%s\n' "$logs" | grep -F 'DNS names require HTTP_ALLOWED_HOSTS' >/dev/null; then fail 'host allowlist warning printed although names are configured'; fi
docker exec "$container" sh -ec '
  pid=$(s6-svstat -o pid /etc/s6.d/rtorrent)
  test "$(stat -c %u:%g "/proc/$pid")" = 991:100
  # The root monitor inherits the same supervisor environment as every service;
  # a daemon running as another UID keeps its environ private from docker exec.
  monitor=$(s6-svstat -o pid /etc/s6.d/service-watchdog)
  tr "\000" "\n" < "/proc/$monitor/environ" | grep -qx "GID=100"
  tr "\000" "\n" < "/proc/$monitor/environ" | grep -qx "UID=991"
' || fail 'services did not receive the decimal identity'
printf 'ok - a named GID reaches services as its decimal ID and the watchdog stays enabled\n'

docker exec "$container" sh -ec '
  log=/tmp/nginx-rutorrent-access.log
  curl --noproxy "*" -sS -o /dev/null -A runtime-contracts http://127.0.0.1:8080/
  line=$(tail -n 1 "$log")
  printf "%s\n" "$line" | grep -Eq "^[^ ]+ - [^ ]+ \[[^]]+\] \"GET / HTTP/1\.1\" 200 [0-9]+ \"[^\"]*\" \"runtime-contracts\"$"
  test "$(curl --noproxy "*" -sS -o /tmp/refusal-body -w "%{http_code}" -A runtime-contracts -H "Host: refused.example" http://127.0.0.1:8080/)" = 421
  line=$(tail -n 1 "$log")
  printf "%s\n" "$line" | grep -Eq "^[^ ]+ - [^ ]+ \[[^]]+\] \"GET / HTTP/1\.1\" 421 [0-9]+ \"[^\"]*\" \"runtime-contracts\" \"host-rejected-configure-HTTP_ALLOWED_HOSTS\"$"
  # The browser sees the setting to change, never an echo of the requested Host.
  grep -F HTTP_ALLOWED_HOSTS /tmp/refusal-body >/dev/null
  ! grep -F refused.example /tmp/refusal-body >/dev/null
' || fail 'nginx access log or Host refusal body changed'
printf 'ok - admitted requests use the combined access format; a refused Host gets a body naming HTTP_ALLOWED_HOSTS\n'

docker exec "$container" sh -ec '
  # FPM must not derive argv from the query string (deprecated in PHP 8.5): its
  # per-request E_DEPRECATED replaced error_get_last() and hid PHP warnings. The
  # mounted conf.d file enables argv derivation and carries a marker, so only
  # the php_admin_value of each pool can keep argv off here. Reading $_SERVER
  # is what derives argv.
  mkdir -p /rutorrent/app/plugins/runtime-contracts
  cat > /rutorrent/app/plugins/runtime-contracts/probe.php <<"PHP"
<?php
$method = $_SERVER["REQUEST_METHOD"] ?? "";
echo ini_get("max_file_uploads"), " ", ini_get("register_argc_argv") ? "argv-on" : "argv-off", " ", ini_get("highlight.html"), " ", json_encode(error_get_last()), "\n";
PHP
  expected="100 argv-off #0C0B0A null"
  answer=$(curl --noproxy "*" -sS "http://127.0.0.1:8080/plugins/runtime-contracts/probe.php?first=1&second=2")
  [ "$answer" = "$expected" ] || { printf "www pool answered: %s\n" "$answer" >&2; exit 1; }
  # nginx serves the health pool only for its fixed script; ask its socket directly.
  answer=$(su-exec 991:100 php85 /tests/runtime-contracts/fastcgi-get.php /run/php/php85-health.sock \
    /rutorrent/app/plugins/runtime-contracts/probe.php "first=1&second=2")
  [ "$answer" = "$expected" ] || { printf "health pool answered: %s\n" "$answer" >&2; exit 1; }
  # CLI plugin workers still receive their arguments.
  test "$(su-exec 991:100 php85 -r "echo \$argc, \" \", \$argv[2];" one two)" = "3 two"
' || fail 'PHP-FPM argv derivation, upload limit or CLI argv contract changed'
printf 'ok - with argv derivation enabled in conf.d, both FPM pools keep it off and error_get_last() clean with max_file_uploads=100; CLI workers keep $argv\n'

docker exec "$container" sh -ec '
  location=$(/usr/local/libexec/service-runtime prepare-health)
  test "$location" = "$(printf "/run/rutorrent-watchdog\n.service-health.json.tmp.")"
  candidate=/run/rutorrent-watchdog/.service-health.json.tmp.runtime-contracts
  printf "{}\n" > "$candidate"
  chmod 0644 "$candidate"
  if /usr/local/libexec/service-runtime publish-health "${candidate##*/}" 2>/tmp/publish-health.err; then exit 1; fi
  rm -f "$candidate"
  grep -qx "service-runtime: unsafe-health-candidate" /tmp/publish-health.err
  if /usr/local/libexec/service-runtime publish-auth not-a-candidate 2>/tmp/publish-auth.err; then exit 1; fi
  grep -qx "service-runtime: invalid-auth-publication" /tmp/publish-auth.err
' || fail 'monitor publication contract or refusal names changed'
printf 'ok - prepare-health reports the one monitor location and refusals name their publication\n'

# A refused publication names the helper's classified reason in the one line
# the monitor logs before it stops.
docker exec "$container" sh -ec '
  s6-svc -d /etc/s6.d/service-watchdog
  i=0; until [ "$(s6-svstat -o up /etc/s6.d/service-watchdog)" = false ]; do i=$((i + 1)); [ "$i" -lt 50 ] || exit 1; sleep 0.2; done
  rm -f /run/rutorrent-watchdog/.service-health.json
  ln -s /tmp/runtime-contracts-target /run/rutorrent-watchdog/.service-health.json
  s6-svc -u /etc/s6.d/service-watchdog
' || fail 'cannot prepare the refused watchdog publication'
refusal_logged() {
  docker logs "$container" 2>&1 | grep -F 'service-watchdog: unavailable reason=state-publication-refused detail=unsafe-health-target' >/dev/null
}
smoke_wait_until "$container" 30 'watchdog refusal' refusal_logged \
  || fail 'watchdog refusal does not name the helper reason'
printf 'ok - a refused monitor publication logs the helper reason as its detail\n'
