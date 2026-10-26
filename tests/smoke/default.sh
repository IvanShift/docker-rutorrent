#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"

image=$1
suffix="smoke-$$-$(date +%s)"
container="rutorrent-default-${suffix}"
rpc2_container="rutorrent-rpc2-${suffix}"
auth_container="rutorrent-auth-${suffix}"
noauth_container="rutorrent-noauth-${suffix}"
invalid_container="rutorrent-invalid-rpc2-${suffix}"
invalid_nginx_container="rutorrent-invalid-nginx-${suffix}"
config_volume="rutorrent-config-${suffix}"
data_volume="rutorrent-data-${suffix}"
workspace=$(mktemp -d)
rtorrent_version=${SMOKE_EXPECTED_RTORRENT_VERSION:-$(sed -n 's/^ARG RTORRENT_BRANCH=v//p' "$smoke_dir/../../Dockerfile")}
libtorrent_version=${SMOKE_EXPECTED_LIBTORRENT_VERSION:-$(sed -n 's/^ARG LIBTORRENT_BRANCH=v//p' "$smoke_dir/../../Dockerfile")}
curl_package_version=${SMOKE_EXPECTED_CURL_VERSION:-$(sed -n 's/^ARG CURL_VERSION=//p' "$smoke_dir/../../Dockerfile")}
ffmpeg_package_version=${SMOKE_EXPECTED_FFMPEG_VERSION:-$(sed -n 's/^ARG FFMPEG_VERSION=//p' "$smoke_dir/../../Dockerfile")}
php_binary=php85

cleanup() {
  smoke_remove_run_containers || true
  docker volume rm "$config_volume" "$data_volume" >/dev/null 2>&1 || true
  rm -rf "$workspace"
}

smoke_install_traps

smoke_check_image "$image"
healthcheck_test=$(docker image inspect --format '{{json .Config.Healthcheck.Test}}' "$image")
[ "$healthcheck_test" = '["CMD-SHELL","/usr/local/bin/rutorrent-healthcheck"]' ] \
  || fail 'image HEALTHCHECK does not invoke the tested readiness command'
rtorrent_revision=$(docker image inspect --format '{{index .Config.Labels "io.github.ivanshift.rtorrent.revision"}}' "$image")
expected_rtorrent_revision=${SMOKE_EXPECTED_RTORRENT_REVISION:-$(sed -n 's/^ARG RTORRENT_VERSION=//p' "$smoke_dir/../../Dockerfile")}
[ -n "$rtorrent_revision" ] && [ "$rtorrent_revision" = "$expected_rtorrent_revision" ] \
  || fail 'rTorrent source revision label differs from expected source revision'
rutorrent_revision=$(docker image inspect --format '{{index .Config.Labels "io.github.ivanshift.rutorrent.revision"}}' "$image")
resolved_rutorrent_revision=$(smoke_run --rm --network none --entrypoint /bin/cat "$image" /usr/share/build-info/rutorrent-revision)
[ -n "$rutorrent_revision" ] && [ "$rutorrent_revision" = "$resolved_rutorrent_revision" ] \
  || fail 'ruTorrent source revision label differs from resolved build record'
expected_rutorrent_revision=${SMOKE_EXPECTED_RUTORRENT_REVISION:-$(sed -n 's/^ARG RUTORRENT_REF=//p' "$smoke_dir/../../Dockerfile")}
[ "$rutorrent_revision" = "$expected_rutorrent_revision" ] \
  || fail 'ruTorrent source revision differs from expected source pin'

docker volume create "$config_volume" >/dev/null
docker volume create "$data_volume" >/dev/null

# Seed paths containing spaces before startup to exercise quoted plugin/theme linking.
smoke_run --rm --entrypoint /bin/sh \
  -v "$config_volume:/config" \
  "$image" -eu -c '
    mkdir -p "/config/custom_plugins/plugin with space"
    printf "%s\n" smoke-marker > "/config/custom_plugins/plugin with space/marker.txt"
    printf "%s\n" "<?php if (!is_file(__DIR__.\"/../../php/util.php\")) exit(7); echo \"plugin-core-ok\";" > "/config/custom_plugins/plugin with space/action.php"
    mkdir -p "/config/custom_themes/theme with space"
  '

smoke_run -d --name "$container" --health-interval=2s --health-start-period=0s \
  -v "$config_volume:/config" \
  -v "$data_volume:/data" \
  "$image" >/dev/null

wait_for_health "$container" || fail 'default container did not become healthy'
wait_for_docker_health "$container" || fail 'Docker HEALTHCHECK did not become healthy'

[ "$(http_status "$container" /healthz)" = 200 ] || fail 'local /healthz status'
[ "$(http_status "$container" /healthz-plugins)" = 403 ] || fail 'network plugin bootstrap is exposed'
[ "$(http_status "$container" http://localhost/healthz-plugins --unix-socket /run/nginx/plugin-health.sock)" = 200 ] \
  || fail 'Unix-socket plugin health status'
[ "$(http_status "$container" /)" = 401 ] || fail 'new-install HTTP auth status'
for sensitive_path in '/php/getplugins.php' '/plugins/loginmgr/action.php?mode=info' '/plugins/cookies/action.php?mode=info'; do
  [ "$(http_status "$container" "$sensitive_path")" = 401 ] \
    || fail "anonymous request reached $sensitive_path"
done
[ "$(http_status "$container" /plugins/loginmgr/action.php --data 'mode=set')" = 401 ] \
  || fail 'anonymous loginmgr mutation reached PHP'
for cookies_mode in add set; do
  [ "$(http_status "$container" /plugins/cookies/action.php --data "mode=${cookies_mode}")" = 401 ] \
    || fail "anonymous cookies ${cookies_mode} reached PHP"
done
container_ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' "$container")
[ -n "$container_ip" ] || fail 'default container IP address'
[ "$(http_status "$container" "http://${container_ip}:8080/healthz")" = 403 ] \
  || fail 'non-loopback /healthz access'
[ "$(http_status "$container" "http://${container_ip}:8080/healthz-plugins")" = 403 ] \
  || fail 'non-loopback plugin bootstrap access'
docker exec "$container" test -S /run/rtorrent/rtorrent.sock || fail 'rTorrent Unix socket'
[ "$(http_status "$container" /RPC2)" = 403 ] || fail 'RPC2 is not denied by default'

version_output=$(docker exec "$container" sh -c 'rtorrent -h 2>&1' || true)
printf '%s\n' "$version_output" | grep -F "BitTorrent client version ${rtorrent_version}." >/dev/null \
  || fail "rTorrent $rtorrent_version version"
docker exec "$container" sh -c "strings /usr/local/lib/libtorrent.so | grep -F 'libTorrent ${libtorrent_version}' >/dev/null" \
  || fail "libtorrent $libtorrent_version version"

docker exec --user 991:991 --workdir /rutorrent/app/php "$container" "$php_binary" -r '
  require "settings.php";
  $settings = rTorrentSettings::get(true);
  if (!$settings->linkExist || $settings->version !== $argv[1] || $settings->libVersion !== $argv[2]) {
    fwrite(STDERR, "Unexpected internal SCGI versions\n");
    exit(1);
  }
  printf("%s/%s\n", $settings->version, $settings->libVersion);
' "$rtorrent_version" "$libtorrent_version" || fail 'ruTorrent internal PHP-to-Unix-SCGI settings probe'

docker exec "$container" "$php_binary" -r 'echo PHP_VERSION, "\n";' >/dev/null \
  || fail 'php85 execution'
docker exec --user 991:991 "$container" nginx -t \
  || fail 'Alpine nginx runtime configuration and modules'
# The local FFmpeg backport installs exactly the CLI and its seven split libraries,
# each named <package>-<version>-r<N> with the literal expected version, and no
# other ffmpeg package.
docker exec "$container" apk info -v > "$workspace/apk-packages" 2>/dev/null \
  || fail 'installed FFmpeg packages'
ffmpeg_expected=
for ffmpeg_name in ffmpeg ffmpeg-libavcodec ffmpeg-libavdevice ffmpeg-libavfilter \
  ffmpeg-libavformat ffmpeg-libavutil ffmpeg-libswresample ffmpeg-libswscale; do
  ffmpeg_expected="$ffmpeg_expected$ffmpeg_name-$ffmpeg_package_version
"
done
ffmpeg_installed=
while IFS= read -r ffmpeg_package; do
  case $ffmpeg_package in ffmpeg*) ;; *) continue ;; esac
  ffmpeg_release=${ffmpeg_package##*-r}
  case $ffmpeg_release in ''|*[!0-9]*) fail "FFmpeg package without an APK release: $ffmpeg_package" ;; esac
  ffmpeg_installed="$ffmpeg_installed${ffmpeg_package%-r"$ffmpeg_release"}
"
done < "$workspace/apk-packages"
[ "$(printf '%s' "$ffmpeg_installed" | LC_ALL=C sort)" = "$(printf '%s' "$ffmpeg_expected" | LC_ALL=C sort)" ] \
  || fail "FFmpeg packages are not exactly the eight $ffmpeg_package_version packages"
docker exec --user 991:991 "$container" sh -eu -c 'command -v mktorrent >/dev/null; mktorrent -h 2>&1 | grep -F "Emil Renner Berthing" >/dev/null' \
  || fail 'mktorrent execution'
"$smoke_dir/mktorrent.sh" "$image"
docker exec --user 991:991 "$container" sh -eu -c 'command -v dumptorrent >/dev/null; dumptorrent -h 2>&1 | grep -F "Dump Torrent v" >/dev/null' \
  || fail 'dumptorrent execution'
docker exec "$container" sh -c 'unrar 2>&1 | grep -F UNRAR >/dev/null' \
  || fail 'unrar execution'

curl_version=$(docker exec "$container" curl -V)
printf '%s\n' "$curl_version" | grep -F AsynchDNS >/dev/null || fail 'curl AsynchDNS feature'
printf '%s\n' "$curl_version" | grep -F 'c-ares/' >/dev/null || fail 'curl c-ares version'
printf '%s\n' "$curl_version" | grep -F "libcurl/$curl_package_version " >/dev/null || fail "curl uses libcurl $curl_package_version"

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

docker exec "$container" test -d '/rutorrent/app/plugins/plugin with space' \
  || fail 'plugin name with spaces'
docker exec "$container" test ! -L '/rutorrent/app/plugins/plugin with space' \
  || fail 'custom plugin directory is a symlink'
docker exec "$container" grep -Fqx smoke-marker '/rutorrent/app/plugins/plugin with space/marker.txt' \
  || fail 'custom plugin regular-file copy'
[ "$(docker exec --user 991:991 "$container" "$php_binary" '/rutorrent/app/plugins/plugin with space/action.php')" = plugin-core-ok ] \
  || fail 'installed custom plugin cannot locate the core by its directory'
docker exec "$container" test -L '/rutorrent/app/plugins/theme/themes/theme with space' \
  || fail 'theme name with spaces'

write_password() (
  target=$1
  username=$2
  password=$3
  confirmation=${4:-$password}
  printf '%s\n' "$username" "$password" "$confirmation" \
    | docker exec -i "$target" /usr/local/bin/gen-http-passwd
)

assert_password_cleanup() {
  leftovers=$(docker exec "$container" find /config/nginx/passwd -name 'rutorrent_passwd.tmp.*' -print) \
    || fail 'cannot inspect password temporary files'
  [ -z "$leftovers" ] || fail "password temporary file cleanup: $leftovers"
}

# A fresh auth installation must support provisioning the first password file.
docker exec "$container" test ! -e /config/nginx/passwd/rutorrent_passwd \
  || fail 'new-install password file unexpectedly exists'
write_password "$container" user.name first-secret >/dev/null || fail 'first password record creation'
[ "$(http_status "$container" / -u user.name:first-secret)" = 200 ] || fail 'first provisioned account opens UI'
docker exec "$container" sh -eu -c '
  printf "%s\n" "userXname:keep" "1:keep" >> /config/nginx/passwd/rutorrent_passwd
'
write_password "$container" 01 numeric-secret >/dev/null || fail 'numeric-looking password record creation'
docker exec "$container" grep -Fqx '1:keep' /config/nginx/passwd/rutorrent_passwd \
  || fail 'numeric-looking username preservation'
[ "$(docker exec "$container" grep -Ec '^01:' /config/nginx/passwd/rutorrent_passwd)" = 1 ] \
  || fail 'distinct numeric-looking username record'
before_inode=$(docker exec "$container" stat -c '%i' /config/nginx/passwd/rutorrent_passwd)
write_password "$container" user.name second-secret >/dev/null || fail 'password record update'
after_inode=$(docker exec "$container" stat -c '%i' /config/nginx/passwd/rutorrent_passwd)
[ "$before_inode" != "$after_inode" ] || fail 'atomic password file replacement'
docker exec "$container" grep -Fqx 'userXname:keep' /config/nginx/passwd/rutorrent_passwd \
  || fail 'fixed-string username matching'
[ "$(docker exec "$container" awk -F: '$1 == "user.name" { count++ } END { print count + 0 }' /config/nginx/passwd/rutorrent_passwd)" = 1 ] \
  || fail 'single updated password record'
[ "$(docker exec "$container" stat -c '%a %u:%g' /config/nginx/passwd/rutorrent_passwd)" = '640 0:991' ] \
  || fail 'password file mode and owner'
before_hash=$(docker exec "$container" sha256sum /config/nginx/passwd/rutorrent_passwd) \
  || fail 'password checksum before refusals'
status=0
write_password "$container" bad:name secret >/dev/null 2>&1 || status=$?
[ "$status" = 64 ] || fail "invalid username status: $status"
status=0
write_password "$container" mismatch first-secret different-secret >/dev/null 2>&1 || status=$?
[ "$status" = 1 ] || fail "mismatched password status: $status"

# Force a failure after mktemp, then require both trap cleanup and file preservation.
docker exec -i "$container" sh -eu -c 'mkdir -p /tmp/password-failure; cat > /tmp/password-failure/chmod; chmod 755 /tmp/password-failure/chmod' <<'SH'
#!/bin/sh
exit 1
SH
status=0
printf '%s\n' injected secret secret | docker exec -i "$container" sh -eu -c '
  PATH=/tmp/password-failure:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
  export PATH
  exec /usr/local/bin/gen-http-passwd
' >/dev/null 2>&1 || status=$?
[ "$status" = 1 ] || fail "post-mktemp failure status: $status"
after_hash=$(docker exec "$container" sha256sum /config/nginx/passwd/rutorrent_passwd) \
  || fail 'password checksum after refusals'
[ "$before_hash" = "$after_hash" ] || fail 'rejected password update changed file'
assert_password_cleanup
[ "$(http_status "$container" / -u user.name:second-secret)" = 200 ] || fail 'provisioned account opens UI'
[ "$(http_status "$container" /php/getplugins.php -u user.name:second-secret)" = 403 ] \
  || fail 'plain client reached plugin bootstrap'
[ "$(http_status "$container" /php/getplugins.php -u user.name:second-secret -H 'X-Requested-With: XMLHttpRequest')" = 200 ] \
  || fail 'provisioned AJAX client loads plugins'

assert_plugin_health() (
  target=$1
  shift
  docker exec "$target" curl --noproxy '*' -fsS --max-time 30 "$@" \
    -o /tmp/smoke-plugin-bootstrap.js || fail 'plugin bootstrap body fetch'
  docker exec "$target" sh -eu -c '
    test -s /tmp/smoke-plugin-bootstrap.js
    if grep -E "pluginCantStart|retrackers: shared-daemon" /tmp/smoke-plugin-bootstrap.js; then exit 1; fi
    if [ -s /tmp/errors.log ]; then cat /tmp/errors.log >&2; exit 1; fi
  ' || fail 'plugin bootstrap reported disabled or failed plugins'
)
assert_plugin_health "$container" -u user.name:second-secret -H 'X-Requested-With: XMLHttpRequest' \
  http://127.0.0.1:8080/php/getplugins.php
assert_plugin_health "$container" --unix-socket /run/nginx/plugin-health.sock http://localhost/healthz-plugins
# Authorization supplied to the internal socket must not create a second profile.
assert_plugin_health "$container" --unix-socket /run/nginx/plugin-health.sock \
  -u arbitrary-profile:unused http://localhost/healthz-plugins
docker exec --user 991:991 --workdir /rutorrent/app/php "$container" "$php_binary" -r '
  $_SERVER["REMOTE_USER"] = "different-http-account";
  require "settings.php";
  if (!$forbidUserSettings) { fwrite(STDERR, "Server profile is not enforced\n"); exit(1); }
' || fail 'single server profile contract'

assert_origin_guard() (
  target=$1
  shift
  for origin in missing foreign same; do
    case "$origin" in
      missing) status=$(http_status "$target" /php/getsettings.php "$@" --data '') ; expected=403 ;;
      foreign) status=$(http_status "$target" /php/getsettings.php "$@" --data '' -H 'Origin: http://evil.example') ; expected=403 ;;
      same) status=$(http_status "$target" /php/getsettings.php "$@" --data '' -H 'Origin: http://127.0.0.1:8080') ; expected=200 ;;
    esac
    [ "$status" = "$expected" ] || fail "$origin Origin POST: expected $expected, got $status"
  done
)
assert_origin_guard "$container" -u user.name:second-secret

# Refusals require the intended exit code and diagnostic, without services.
smoke_run -d --network none --name "$invalid_container" -e ENABLE_RPC2=1 "$image" >/dev/null
smoke_expect_exit "$invalid_container" 64 'ENABLE_RPC2 must be exactly true or false'
smoke_remove_container "$invalid_container"
smoke_run -d --network none --name "$invalid_container" -e HTTP_AUTH=1 "$image" >/dev/null
smoke_expect_exit "$invalid_container" 64 'HTTP_AUTH must be exactly true or false'
smoke_remove_container "$invalid_container"
smoke_run -d --network none --name "$invalid_container" \
  --mount "type=bind,src=$smoke_dir/../update-config/fixtures/fail-update-config,dst=/usr/local/bin/update-config,readonly" \
  "$image" >/dev/null
smoke_expect_exit "$invalid_container" 42 'Unable to update old configurations (exit 42)'

# Use a writable include so a later chown cannot imitate nginx's refusal.
printf 'this is not valid nginx configuration;\n' > "$workspace/rpc2-disabled.conf"
smoke_run -d --network none --name "$invalid_nginx_container" \
  -v "$workspace/rpc2-disabled.conf:/etc/nginx/rpc2-disabled.conf" "$image" >/dev/null
smoke_expect_exit "$invalid_nginx_container" 1 'Invalid nginx configuration'

# Disabling auth requires a deliberate opt-out; an old explicit false fails closed.
smoke_run -d --name "$noauth_container" -e HTTP_AUTH=false "$image" >/dev/null
smoke_expect_exit "$noauth_container" 64 'requires ALLOW_UNAUTHENTICATED_HTTP=true'
smoke_remove_container "$noauth_container"
smoke_run -d --name "$noauth_container" -e HTTP_AUTH=false \
  -e ALLOW_UNAUTHENTICATED_HTTP=true -e HTTP_ALLOWED_HOSTS=smoke-allowed.example "$image" >/dev/null
wait_for_health "$noauth_container" || fail 'explicit unauthenticated opt-out did not become healthy'
[ "$(http_status "$noauth_container" /)" = 200 ] || fail 'explicit unauthenticated opt-out status'
[ "$(http_status "$noauth_container" / -H 'Host: smoke-allowed.example')" = 200 ] || fail 'configured unauthenticated hostname'
[ "$(http_status "$noauth_container" / -H 'Host: smoke-forbidden.example')" = 421 ] || fail 'unconfigured unauthenticated hostname was accepted'

docker logs "$noauth_container" 2>&1 | grep -F 'disabled by explicit opt-out' >/dev/null \
  || fail 'explicit unauthenticated opt-out warning'
# Repository and persisted configuration paths must be denied over HTTP.
for repository_path in /conf/config.php /share/settings/x /tasks/retrackers-clear-markers.php /tests/plugins/_task/TaskPidfdImageSmoke.php /tools/retrackers-clear-markers.php; do
  [ "$(http_status "$noauth_container" "$repository_path")" = 403 ] \
    || fail "repository-only path reachable over HTTP: $repository_path"
done

assert_origin_guard "$noauth_container"

# Standalone integrations send raw XML without browser-origin headers. Keep
# this path paired with a browser cross-site refusal and ordinary form polling.
assert_xmlrpc_clients() (
  target=$1
  shift
  request='<?xml version="1.0"?><methodCall><methodName>system.client_version</methodName><params></params></methodCall>'
  for endpoint in /RPC2 /plugins/httprpc/action.php; do
    [ "$(http_status "$target" "$endpoint" "$@" --data "$request" -H 'Content-Type: text/xml')" = 200 ] \
      || fail "headerless XML-RPC client at $endpoint"
    response=$(docker exec "$target" curl --noproxy '*' -fsS --max-time 10 "$@" \
      -H 'Content-Type: text/xml' --data "$request" "http://127.0.0.1:8080$endpoint") \
      || fail "headerless XML-RPC body at $endpoint"
    printf '%s\n' "$response" | grep -F "$rtorrent_version" >/dev/null \
      || fail "headerless XML-RPC version at $endpoint"
    if printf '%s\n' "$response" | grep -F '<fault>' >/dev/null; then fail "headerless XML-RPC fault at $endpoint"; fi
    [ "$(http_status "$target" "$endpoint" "$@" --data "$request" -H 'Content-Type: text/xml' -H 'Sec-Fetch-Site: cross-site')" = 403 ] \
      || fail "cross-site XML-RPC at $endpoint"
  done
  [ "$(http_status "$target" /plugins/httprpc/action.php "$@" --data 'mode=list' -H 'Origin: http://127.0.0.1:8080')" = 200 ] \
    || fail 'same-origin httprpc form polling'
  docker exec "$target" curl --noproxy '*' -fsS --max-time 10 "$@" \
    -H 'Origin: http://127.0.0.1:8080' --data 'mode=list' http://127.0.0.1:8080/plugins/httprpc/action.php \
    | python3 -c 'import json,sys; assert isinstance(json.load(sys.stdin), (dict,list))' \
    || fail 'httprpc form poll returned invalid JSON'
)

# Authenticated RPC2 shares the UI boundary and rejects cross-origin requests.
smoke_run -d --network none --name "$auth_container" -e ENABLE_RPC2=true "$image" >/dev/null
wait_for_health "$auth_container" || fail 'authenticated RPC2 container readiness'
write_password "$auth_container" rpc-user rpc-secret >/dev/null || fail 'RPC2 user provisioning'
rpc_request='<?xml version="1.0"?><methodCall><methodName>system.library_version</methodName><params></params></methodCall>'
[ "$(http_status "$auth_container" /RPC2 --data "$rpc_request" -H 'Content-Type: text/xml')" = 401 ] \
  || fail 'anonymous RPC2 is not authenticated'
[ "$(http_status "$auth_container" /RPC2 -u rpc-user:rpc-secret --data "$rpc_request" -H 'Content-Type: text/xml' -H 'Origin: http://evil.example')" = 403 ] \
  || fail 'authenticated RPC2 accepts foreign Origin'
[ "$(http_status "$auth_container" /RPC2 -u rpc-user:rpc-secret --data "$rpc_request" -H 'Content-Type: text/xml' -H 'Origin: http://127.0.0.1:8080')" = 200 ] \
  || fail 'authenticated RPC2 same-Origin version status'
auth_rpc_body=$(docker exec "$auth_container" curl --noproxy '*' -fsS --max-time 10 \
  -u rpc-user:rpc-secret -H 'Content-Type: text/xml' -H 'Origin: http://127.0.0.1:8080' \
  --data "$rpc_request" http://127.0.0.1:8080/RPC2) || fail 'authenticated RPC2 version body'
printf '%s\n' "$auth_rpc_body" | grep -F "$libtorrent_version" >/dev/null || fail 'authenticated RPC2 library version'
assert_xmlrpc_clients "$auth_container" -u rpc-user:rpc-secret

# RPC2 is opt-in and exposes a harmless library-version request when enabled.
smoke_run -d --name "$rpc2_container" -e ENABLE_RPC2=true -e HTTP_AUTH=false \
  -e ALLOW_UNAUTHENTICATED_HTTP=true "$image" >/dev/null
wait_for_health "$rpc2_container" || fail 'RPC2-enabled container did not become healthy'
rpc_request='<?xml version="1.0"?><methodCall><methodName>system.library_version</methodName><params></params></methodCall>'
rpc_response="$workspace/rpc2-response.xml"
rpc_status=$(docker exec -i "$rpc2_container" curl -sS -o /tmp/rpc2-response.xml -w '%{http_code}' \
  -H 'Content-Type: text/xml' -H 'Origin: http://127.0.0.1:8080' --max-time 10 --data-binary @- http://127.0.0.1:8080/RPC2 <<EOF
$rpc_request
EOF
)
docker cp "$rpc2_container:/tmp/rpc2-response.xml" "$rpc_response" >/dev/null
[ "$rpc_status" = 200 ] || fail "RPC2-enabled version request status: $rpc_status"
grep -F "$libtorrent_version" "$rpc_response" >/dev/null || fail 'RPC2 library version response'
docker logs "$rpc2_container" 2>&1 | grep -F 'RPC2 is enabled without HTTP authentication' >/dev/null \
  || fail 'RPC2 without-auth warning'
assert_xmlrpc_clients "$rpc2_container"

docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'password restart readiness'
[ "$(http_status "$container" / -u user.name:second-secret)" = 200 ] || fail 'password lost after restart'
[ "$(http_status "$container" /)" = 401 ] || fail 'anonymous UI after restart'
smoke_remove_container "$container"
smoke_run -d --name "$container" --health-interval=2s --health-start-period=0s \
  -v "$config_volume:/config" -v "$data_volume:/data" "$image" >/dev/null
wait_for_health "$container" || fail 'password volume recreate did not become healthy'
[ "$(http_status "$container" / -u user.name:second-secret)" = 200 ] || fail 'provisioned password lost after recreate'
[ "$(http_status "$container" /)" = 401 ] || fail 'anonymous request opened UI after recreate'

printf 'ok - default runtime, secure RPC2 defaults, auth-independent health, and toolchain smoke\n'

"$(dirname -- "$0")/mediainfo.sh" "$image"
"$smoke_dir/screenshots.sh" "$image"
"$(dirname -- "$0")/identity.sh" "$image"

# Use the loaded image's native platform for helper checks.
"$smoke_dir/pidfd.sh" "$image"
"$smoke_dir/erasedata-rename.sh" "$image"
"$smoke_dir/erasedata-queue-bootstrap.sh" "$image"
"$smoke_dir/stop-intent-bootstrap.sh" "$image"
"$smoke_dir/rtorrent-log-config-migration.sh" "$image" current
"$smoke_dir/conf-persistence.sh" "$image"
"$smoke_dir/service-lifecycle.sh" "$image"
"$smoke_dir/service-runtime.sh" "$image"
"$smoke_dir/schedule-errors.sh" "$image"
"$smoke_dir/erasedata-attestation.sh" "$image"
"$smoke_dir/network-regressions.sh" "$image"
"$smoke_dir/health-capacity.sh" "$image"
"$smoke_dir/public-url.sh" "$image"
"$smoke_dir/ownership-remaining.sh" "$image"
"$smoke_dir/native-worker-startup-guard.sh" "$image"
"$smoke_dir/http-passwd-path.sh" "$image"
