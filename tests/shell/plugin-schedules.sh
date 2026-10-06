#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
mkdir "$workspace/bin"
export schedule_fixture=$workspace
outer_timeout=$(command -v timeout)
cat > "$workspace/bin/id" <<'MOCK'
#!/bin/sh
printf '991\n'
MOCK
cat > "$workspace/bin/date" <<'MOCK'
#!/bin/sh
if [ "$1" = +%s ]; then cat "$schedule_fixture/clock"; else printf 'fixture-time\n'; fi
MOCK
cat > "$workspace/bin/timeout" <<'MOCK'
#!/bin/sh
[ "$1" -gt 0 ] && [ "$1" -le 3 ] || exit 71
[ "$2" = "$schedule_fixture/bin/scgi" ] || exit 72
shift
export schedule_bounded=true
exec "$@"
MOCK
cat > "$workspace/bin/scgi" <<'MOCK'
#!/bin/sh
[ "${schedule_bounded:-false}" = true ] || exit 73
count=$(cat "$schedule_fixture/scgi-count")
count=$((count + 1))
printf '%s\n' "$count" > "$schedule_fixture/scgi-count"
if [ "$count" -lt 3 ]; then
  current=$(cat "$schedule_fixture/clock")
  printf '%s\n' "$((current + 200))" > "$schedule_fixture/clock"
  exit 1
fi
MOCK
cat > "$workspace/bin/curl" <<'MOCK'
#!/bin/sh
[ "$(cat "$schedule_fixture/scgi-count")" -ge 3 ] || exit 70
unix_socket=''
response=''
request_timeout=''
while [ "$#" -gt 0 ]; do
  case "$1" in
    --unix-socket) unix_socket=$2; shift ;;
    --max-time) request_timeout=$2; shift ;;
    -o) response=$2; shift ;;
  esac
  shift
done
[ "$unix_socket" = /run/nginx/plugin-health.sock ] || exit 74
[ -n "$response" ] && [ "$request_timeout" -gt 0 ] && [ "$request_timeout" -le 120 ] || exit 75
count=$(cat "$schedule_fixture/curl-count")
count=$((count + 1))
printf '%s\n' "$count" > "$schedule_fixture/curl-count"
if [ "$schedule_case" = persistent-failure ]; then
  printf "%s\n" "theWebUI.systemInfo.rTorrent={started:true}; plugin.disable(); noty('custom: '+theUILang.pluginCantStart,'error');" > "$response"
else
  case "$count" in
    1) printf 'theWebUI.systemInfo.rTorrent={started:false};\n' > "$response" ;;
    2) printf 'HTTP 200 without daemon startup state\n' > "$response" ;;
    3) printf 'theWebUI.systemInfo.rTorrent={started:true}; started:false;\n' > "$response" ;;
    *) printf 'theWebUI.systemInfo.rTorrent={started:true};\n' > "$response" ;;
  esac
fi
MOCK
cat > "$workspace/bin/sleep" <<'MOCK'
#!/bin/sh
if [ "$1" = 86400 ]; then kill -TERM "$PPID"; fi
MOCK
chmod 755 "$workspace/bin/"*
sed "s|/usr/bin/php85|$workspace/bin/scgi|g" "$repo/rootfs/etc/s6.d/plugin-schedules/run" > "$workspace/service"
for schedule_case in retry-state persistent-failure; do
  export schedule_case
  printf '1000\n' > "$workspace/clock"
  printf '0\n' > "$workspace/scgi-count"
  printf '0\n' > "$workspace/curl-count"
  PATH="$workspace/bin:$PATH" "$outer_timeout" 10 sh "$workspace/service" > "$workspace/output" 2>&1 \
    || { cat "$workspace/output" >&2; fail 'plugin registration harness did not terminate cleanly'; }
  [ "$(cat "$workspace/scgi-count")" = 3 ] || fail 'plugin registration did not wait for bounded SCGI readiness'
  if [ "$schedule_case" = retry-state ]; then
    [ "$(cat "$workspace/curl-count")" = 4 ] || fail 'incomplete or contradictory startup response was accepted'
  else
    [ "$(cat "$workspace/curl-count")" = 1 ] || fail 'persistent plugin refusal repeated full initialization'
    [ "$(grep -c 'failed requirements' "$workspace/output")" = 1 ] || fail 'plugin initialization refusal must be visible once'
    grep -q 'plugins=custom' "$workspace/output" || fail 'failed plugin name must be visible'
  fi
  grep -F 'plugin schedules registered' "$workspace/output" >/dev/null \
    || { cat "$workspace/output" >&2; fail 'slow readiness consumed the registration deadline'; }
  printf 'ok - plugin schedules %s over private Unix socket\n' "$schedule_case"
done
