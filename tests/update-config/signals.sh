#!/bin/sh
# Host entry point: signal the actual running harness during its first migration.
set -eu
[ "$#" -eq 1 ] || { printf 'Usage: %s IMAGE\n' "$0" >&2; exit 64; }
image=$1
test_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# The shared smoke launchers label each container with this run's owner token.
. "$test_root/../smoke/lib.sh"
workspace=$(mktemp -d)
name="smoke-migration-signals-$$"
cleanup() { smoke_remove_run_containers || :; rm -rf "$workspace"; }
smoke_install_traps
# The fake signals its parent harness in the first migration: run.sh (and so
# legacy-098.sh) migrates through update-config, policy-blocks.sh through PHP_BIN.
for entrypoint in run.sh legacy-098.sh policy-blocks.sh; do
  for signal_spec in HUP:129 INT:130 TERM:143; do
    signal_name=${signal_spec%:*}
    signal_status=${signal_spec#*:}
    printf '#!/bin/sh\nkill -%s "$PPID"\nsleep 0.1\nexit 1\n' "$signal_name" >"$workspace/update-config"
    chmod 0755 "$workspace/update-config"
    status=0
    smoke_run --rm --network none --name "$name" --entrypoint /bin/sh \
      --mount "type=bind,src=$test_root,dst=/tests/update-config,readonly" \
      --mount "type=bind,src=$workspace/update-config,dst=/usr/local/bin/update-config,readonly" \
      --env PHP_BIN=/usr/local/bin/update-config \
      "$image" -eu -c 'touch /etc/rtorrent-legacy; exec /tests/update-config/"$1"' sh "$entrypoint" >"$workspace/output" 2>&1 || status=$?
    [ "$status" -eq "$signal_status" ] || { cat "$workspace/output" >&2; printf 'not ok - %s %s status %s\n' "$entrypoint" "$signal_name" "$status" >&2; exit 1; }
    if grep -Fq 'not ok -' "$workspace/output"; then cat "$workspace/output" >&2; exit 1; fi
    printf 'ok - %s terminates on %s with %s\n' "$entrypoint" "$signal_name" "$signal_status"
  done
done
