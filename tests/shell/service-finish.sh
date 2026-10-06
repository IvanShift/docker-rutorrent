#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
mkdir "$workspace/state" "$workspace/bin"
printf '#!/bin/sh\nexit 0\n' > "$workspace/bin/s6-svc"
chmod 755 "$workspace/bin/s6-svc"
PATH="$workspace/bin:$PATH"
export PATH
printf '#!/bin/sh\nexit 0\n' > "$workspace/stop"
chmod 755 "$workspace/stop"
sed -e "s|state=/etc/s6.d/.s6-svscan|state=$workspace/state|" \
    -e "s|/usr/local/libexec/stop-services|$workspace/stop|" \
    "$repo/rootfs/usr/local/libexec/service-finish" > "$workspace/finish"
for spec in requested:0:0:clean requested:256:15:clean requested:256:6:failed requested:256:9:failed requested:1:0:failed unrequested:0:0:restart unrequested:256:15:restart unrequested:256:2:restart unrequested:256:9:failed; do
  stop=${spec%%:*}; rest=${spec#*:}; status=${rest%%:*}; rest=${rest#*:}; signal=${rest%%:*}; expected=${rest#*:}
  rm -f "$workspace/state/shutdown-requested" "$workspace/state/service-failed"
  [ "$stop" != requested ] || : > "$workspace/state/shutdown-requested"
  sh "$workspace/finish" "$status" "$signal" rtorrent > "$workspace/output" 2>&1
  if [ "$expected" = failed ]; then
    [ -f "$workspace/state/service-failed" ] || fail "finish missed $spec"
    grep -q 'exited unexpectedly' "$workspace/output" || fail "finish missing failure diagnostic $spec"
  else
    [ ! -e "$workspace/state/service-failed" ] || fail "finish rejected $spec"
    if [ "$expected" = clean ]; then [ ! -s "$workspace/output" ] || fail "finish logged clean shutdown $spec"; else grep -q "stopped cleanly" "$workspace/output" || fail "finish missed restart diagnostic $spec"; fi
  fi
  if [ "$status" != 256 ]; then
    if grep -q 'signal' "$workspace/output"; then fail 'finish printed undefined signal for ordinary exit'; fi
  fi
done
for service in logrotate service-watchdog; do
  rm -f "$workspace/state/shutdown-requested" "$workspace/state/service-failed"
  sh "$workspace/finish" 256 9 "$service" > "$workspace/output" 2>&1
  [ ! -e "$workspace/state/service-failed" ] || fail "$service failure stopped stack"
  grep -q restarting "$workspace/output" || fail "$service failure is invisible"
done
status=0
sh "$workspace/finish" 0 0 plugin-schedules || status=$?
[ "$status" = 125 ] || fail 'successful initialization must remain one-shot'
printf 'ok - service finish separates shutdown, daemon restart, helper retry and one-shot initialization\n'
