#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
cat > "$workspace/helper" <<'MOCK'
#!/bin/sh
[ "$1" = reopen ] || exit 70
case "$2" in nginx|php85) ;; *) exit 71 ;; esac
printf '%s\n' "$2" >> "$fixture/signals"
exit 1
MOCK
chmod 755 "$workspace/helper"
export fixture="$workspace"
awk '/^reopen_log\(\)/ {copy=1} /^LOG_FILES=/ {copy=0} copy {print}' "$repo/rootfs/etc/s6.d/logrotate/run" \
  | sed "s|/usr/local/libexec/service-runtime|$workspace/helper|g" > "$workspace/reopen"
. "$workspace/reopen"
reopen_log nginx
reopen_log php85
[ "$(cat "$workspace/signals")" = "$(printf 'nginx\nphp85')" ] || fail 'logrotate delegated incorrect service identities'
printf 'ok - log rotation delegates fixed service names and survives safe pidfd refusal\n'
