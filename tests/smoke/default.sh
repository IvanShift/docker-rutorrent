#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s IMAGE\n' "$0" >&2
  exit 64
fi

image=$1
suffix="task4-$$-$(date +%s)"
container="rutorrent-default-${suffix}"
rpc2_container="rutorrent-rpc2-${suffix}"
auth_container="rutorrent-auth-${suffix}"
invalid_container="rutorrent-invalid-rpc2-${suffix}"
invalid_nginx_container="rutorrent-invalid-nginx-${suffix}"
config_volume="rutorrent-config-${suffix}"
data_volume="rutorrent-data-${suffix}"
workspace=$(mktemp -d)

cleanup() {
  docker rm -f -v \
    "$container" \
    "$rpc2_container" \
    "$auth_container" \
    "$invalid_container" \
    "$invalid_nginx_container" >/dev/null 2>&1 || true
  docker volume rm "$config_volume" "$data_volume" >/dev/null 2>&1 || true
  rm -rf "$workspace"
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

wait_for_health() {
  target=$1
  attempt=0

  while [ "$attempt" -lt 120 ]; do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$target")
    case "$status" in
      healthy)
        return 0
      ;;
      exited|dead)
        docker logs "$target" >&2 || true
        return 1
      ;;
    esac
    attempt=$((attempt + 1))
    sleep 1
  done

  docker logs "$target" >&2 || true
  return 1
}

wait_for_exit() {
  target=$1
  attempt=0

  while [ "$attempt" -lt 30 ]; do
    running=$(docker inspect --format '{{.State.Running}}' "$target")
    [ "$running" = false ] && return 0
    attempt=$((attempt + 1))
    sleep 1
  done

  return 1
}

http_status() {
  target=$1
  path=$2
  docker exec "$target" curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080${path}"
}

docker image inspect "$image" >/dev/null 2>&1 || fail "image not found: $image"
docker volume create "$config_volume" >/dev/null
docker volume create "$data_volume" >/dev/null

# Seed paths containing spaces before startup to exercise quoted plugin/theme linking.
docker run --rm --entrypoint /bin/sh \
  -v "$config_volume:/config" \
  "$image" -eu -c '
    mkdir -p "/config/custom_plugins/plugin with space"
    mkdir -p "/config/custom_themes/theme with space"
  '

docker run -d --name "$container" \
  -v "$config_volume:/config" \
  -v "$data_volume:/data" \
  "$image" >/dev/null

wait_for_health "$container" || fail 'default container did not become healthy'
[ "$(http_status "$container" /healthz)" = 200 ] || fail 'local /healthz status'
[ "$(http_status "$container" /)" = 200 ] || fail 'local / status'
container_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")
[ -n "$container_ip" ] || fail 'default container IP address'
[ "$(docker exec "$container" curl -sS -o /dev/null -w '%{http_code}' "http://${container_ip}:8080/healthz")" = 403 ] \
  || fail 'non-loopback /healthz access'
docker exec "$container" test -S /run/rtorrent/rtorrent.sock || fail 'rTorrent Unix socket'
[ "$(http_status "$container" /RPC2)" = 403 ] || fail 'RPC2 is not denied by default'

version_output=$(docker exec "$container" sh -c 'rtorrent -h 2>&1' || true)
printf '%s\n' "$version_output" | grep -F "BitTorrent client version 0.16.21." >/dev/null \
  || fail 'rTorrent 0.16.21 version'
docker exec "$container" sh -c "strings /usr/local/lib/libtorrent.so.49 | grep -F 'libTorrent 0.16.21' >/dev/null" \
  || fail 'libtorrent 0.16.21 version'
internal_versions=$(docker exec --user 991:991 --workdir /rutorrent/app/php "$container" php85 -r '
  require "settings.php";
  $settings = rTorrentSettings::get(true);
  if (!$settings->linkExist || $settings->version !== "0.16.21" || $settings->libVersion !== "0.16.21") {
    fwrite(STDERR, "Unexpected internal SCGI versions\n");
    exit(1);
  }
  printf("%s/%s\n", $settings->version, $settings->libVersion);
') || fail 'ruTorrent internal PHP-to-Unix-SCGI settings probe'
[ "$internal_versions" = '0.16.21/0.16.21' ] \
  || fail 'ruTorrent internal PHP-to-Unix-SCGI versions'

docker exec "$container" php85 -r 'echo PHP_VERSION, "\n";' >/dev/null \
  || fail 'php85 execution'
docker exec "$container" nginx -v >/dev/null 2>&1 || fail 'nginx execution'
docker exec "$container" sh -c 'mktorrent -h 2>&1 | grep -F mktorrent >/dev/null' \
  || fail 'mktorrent execution'
docker exec "$container" sh -c 'dumptorrent -h 2>&1 | grep -F dumptorrent >/dev/null' \
  || fail 'dumptorrent execution'
docker exec "$container" sh -c 'unrar 2>&1 | grep -F UNRAR >/dev/null' \
  || fail 'unrar execution'

curl_version=$(docker exec "$container" curl -V)
printf '%s\n' "$curl_version" | grep -F AsynchDNS >/dev/null || fail 'curl AsynchDNS feature'
printf '%s\n' "$curl_version" | grep -F 'c-ares/' >/dev/null || fail 'curl c-ares version'

rtorrent_config=$(docker exec "$container" cat /config/rtorrent/.rtorrent.rc)
printf '%s\n' "$rtorrent_config" | grep -F 'network.listen.port.range.set = 45000-45000' >/dev/null \
  || fail 'canonical rTorrent port range'
printf '%s\n' "$rtorrent_config" | grep -F 'network.listen.port.random.set = no' >/dev/null \
  || fail 'canonical rTorrent random port setting'
printf '%s\n' "$rtorrent_config" | grep -F 'protocol.encryption.set = allow' >/dev/null \
  || fail 'canonical rTorrent encryption setting'
if printf '%s\n' "$rtorrent_config" | grep -Eq '^[[:space:]]*network\.port_[^=]*='; then
  fail 'assigned network.port_* command remains'
fi

docker exec "$container" test -L '/rutorrent/app/plugins/plugin with space' \
  || fail 'plugin name with spaces'
docker exec "$container" test -L '/rutorrent/app/plugins/theme/themes/theme with space' \
  || fail 'theme name with spaces'

# Password records are validated, fixed-string matched, and atomically replaced.
docker exec "$container" sh -eu -c '
  mkdir -p /config/nginx/passwd
  printf "%s\n" "userXname:keep" "1:keep" > /config/nginx/passwd/rutorrent_passwd
'
if ! docker exec -i "$container" /usr/local/bin/gen-http-passwd >/dev/null <<EOF
user.name
first-secret
first-secret
EOF
then
  fail 'password record creation'
fi
docker exec "$container" awk -F: '
  $1 == "userXname" && $2 == "keep" { found = 1 }
  END { exit !found }
' /config/nginx/passwd/rutorrent_passwd || fail 'fixed-string username matching'
if ! docker exec -i "$container" /usr/local/bin/gen-http-passwd >/dev/null <<EOF
01
numeric-secret
numeric-secret
EOF
then
  fail 'numeric-looking password record creation'
fi
docker exec "$container" grep -Fqx '1:keep' /config/nginx/passwd/rutorrent_passwd \
  || fail 'numeric-looking username preservation'
[ "$(docker exec "$container" grep -Ec '^01:' /config/nginx/passwd/rutorrent_passwd)" = 1 ] \
  || fail 'distinct numeric-looking username record'
before_inode=$(docker exec "$container" stat -c '%i' /config/nginx/passwd/rutorrent_passwd)
if ! docker exec -i "$container" /usr/local/bin/gen-http-passwd >/dev/null <<EOF
user.name
second-secret
second-secret
EOF
then
  fail 'password record update'
fi
after_inode=$(docker exec "$container" stat -c '%i' /config/nginx/passwd/rutorrent_passwd)
[ "$before_inode" != "$after_inode" ] || fail 'atomic password file replacement'
[ "$(docker exec "$container" awk -F: '$1 == "user.name" { count++ } END { print count + 0 }' /config/nginx/passwd/rutorrent_passwd)" = 1 ] \
  || fail 'single updated password record'
[ "$(docker exec "$container" stat -c '%a %u:%g' /config/nginx/passwd/rutorrent_passwd)" = '640 991:991' ] \
  || fail 'password file mode and owner'
before_hash=$(docker exec "$container" sha256sum /config/nginx/passwd/rutorrent_passwd | awk '{print $1}')
if docker exec -i "$container" /usr/local/bin/gen-http-passwd >/dev/null 2>&1 <<EOF
bad:name
secret
secret
EOF
then
  fail 'invalid username accepted'
fi
after_hash=$(docker exec "$container" sha256sum /config/nginx/passwd/rutorrent_passwd | awk '{print $1}')
[ "$before_hash" = "$after_hash" ] || fail 'invalid username changed password file'
[ -z "$(docker exec "$container" find /config/nginx/passwd -name 'rutorrent_passwd.tmp.*' -print -quit)" ] \
  || fail 'password temporary file cleanup'

# Invalid ENABLE_RPC2 values must fail before services start.
docker run -d --name "$invalid_container" -e ENABLE_RPC2=1 "$image" >/dev/null
wait_for_exit "$invalid_container" || fail 'invalid ENABLE_RPC2 value did not stop startup'
[ "$(docker inspect --format '{{.State.ExitCode}}' "$invalid_container")" -ne 0 ] \
  || fail 'invalid ENABLE_RPC2 value exited successfully'
docker logs "$invalid_container" 2>&1 | grep -F 'ENABLE_RPC2 must be exactly true or false' >/dev/null \
  || fail 'invalid ENABLE_RPC2 diagnostic'

# A malformed selected include must make nginx validation fail closed.
printf 'this is not valid nginx configuration;\n' > "$workspace/rpc2-disabled.conf"
docker run -d --name "$invalid_nginx_container" \
  -v "$workspace/rpc2-disabled.conf:/etc/nginx/rpc2-disabled.conf:ro" \
  "$image" >/dev/null
wait_for_exit "$invalid_nginx_container" || fail 'invalid nginx configuration did not stop startup'
[ "$(docker inspect --format '{{.State.ExitCode}}' "$invalid_nginx_container")" -ne 0 ] \
  || fail 'invalid nginx configuration exited successfully'
docker logs "$invalid_nginx_container" 2>&1 | grep -F 'nginx: configuration file' >/dev/null \
  || fail 'nginx validation diagnostic'

# Health remains auth-independent.
docker run -d --name "$auth_container" -e HTTP_AUTH=true "$image" >/dev/null
wait_for_health "$auth_container" || fail 'HTTP-auth container did not become healthy'
[ "$(http_status "$auth_container" /healthz)" = 200 ] || fail 'auth-independent /healthz status'
[ "$(http_status "$auth_container" /)" = 401 ] || fail 'HTTP auth protects /'

# RPC2 is opt-in and exposes a harmless library-version request when enabled.
docker run -d --name "$rpc2_container" -e ENABLE_RPC2=true "$image" >/dev/null
wait_for_health "$rpc2_container" || fail 'RPC2-enabled container did not become healthy'
rpc_request='<?xml version="1.0"?><methodCall><methodName>system.library_version</methodName><params></params></methodCall>'
rpc_response="$workspace/rpc2-response.xml"
rpc_status=$(docker exec -i "$rpc2_container" curl -sS -o /tmp/rpc2-response.xml -w '%{http_code}' \
  -H 'Content-Type: text/xml' --data-binary @- http://127.0.0.1:8080/RPC2 <<EOF
$rpc_request
EOF
)
docker cp "$rpc2_container:/tmp/rpc2-response.xml" "$rpc_response" >/dev/null
[ "$rpc_status" != 403 ] || fail 'RPC2-enabled version request returned 403'
[ "$rpc_status" = 200 ] || fail "RPC2-enabled version request status: $rpc_status"
grep -F '0.16.21' "$rpc_response" >/dev/null || fail 'RPC2 library version response'
docker logs "$rpc2_container" 2>&1 | grep -F 'RPC2 is enabled without HTTP authentication' >/dev/null \
  || fail 'RPC2 without-auth warning'

printf 'ok - default runtime, secure RPC2 defaults, auth-independent health, and toolchain smoke\n'
