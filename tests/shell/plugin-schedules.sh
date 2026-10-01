#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
mkdir "$workspace/bin"
export schedule_fixture=$workspace
printf '1000\n' > "$workspace/clock"
printf '0\n' > "$workspace/scgi-count"
printf '0\n' > "$workspace/curl-count"
cat > "$workspace/bin/id" <<'MOCK'
#!/bin/sh
printf '991\n'
MOCK
cat > "$workspace/bin/date" <<'MOCK'
#!/bin/sh
if [ "$1" = +%s ]; then cat "$schedule_fixture/clock"; else printf 'fixture-time\n'; fi
MOCK
cat > "$workspace/bin/scgi" <<'MOCK'
#!/bin/sh
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
# Fail the harness if initialization is attempted before the daemon answers.
[ "$(cat "$schedule_fixture/scgi-count")" -ge 3 ] || exit 70
while [ "$#" -gt 0 ]; do
  if [ "$1" = -o ]; then response=$2; shift; fi
  shift
done
count=$(cat "$schedule_fixture/curl-count")
count=$((count + 1))
printf '%s\n' "$count" > "$schedule_fixture/curl-count"
if [ "$count" = 1 ]; then
  printf 'theWebUI.systemInfo.rTorrent={started:false};\n' > "$response"
else
  printf 'theWebUI.systemInfo.rTorrent={started:true};\n' > "$response"
fi
MOCK
cat > "$workspace/bin/sleep" <<'MOCK'
#!/bin/sh
if [ "$1" = 86400 ]; then kill -TERM "$PPID"; fi
MOCK
chmod 755 "$workspace/bin/"*
sed "s|/usr/bin/php85|$workspace/bin/scgi|g" "$repo/rootfs/etc/s6.d/plugin-schedules/run" > "$workspace/service"
PATH="$workspace/bin:$PATH" timeout 10 sh "$workspace/service" > "$workspace/output" 2>&1 \
  || { cat "$workspace/output" >&2; fail 'plugin registration harness did not terminate cleanly'; }
[ "$(cat "$workspace/scgi-count")" = 3 ] || fail 'plugin registration did not wait for slow SCGI'
[ "$(cat "$workspace/curl-count")" = 2 ] || fail 'HTTP 200 with started:false was accepted'
grep -F 'plugin schedules registered' "$workspace/output" >/dev/null \
  || { cat "$workspace/output" >&2; fail 'slow readiness consumed the registration deadline'; }
printf 'ok - plugin schedules wait for SCGI and retry HTTP 200 with started:false\n'
