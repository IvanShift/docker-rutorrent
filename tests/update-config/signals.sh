#!/bin/sh
# Host entry point: signal the actual running harness during its first migration.
set -eu
[ "$#" -eq 1 ] || { printf 'Usage: %s IMAGE\n' "$0" >&2; exit 64; }
image=$1
test_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
workspace=$(mktemp -d)
name="rem4-migtests-signals-$$"
cleanup() { docker rm -f -v "$name" >/dev/null 2>&1 || :; rm -rf "$workspace"; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
for entrypoint in run.sh legacy-098.sh; do
  for signal_spec in HUP:129 INT:130 TERM:143; do
    signal_name=${signal_spec%:*}
    signal_status=${signal_spec#*:}
    printf '#!/bin/sh\nkill -%s "$PPID"\nsleep 0.1\nexit 1\n' "$signal_name" >"$workspace/update-config"
    chmod 0755 "$workspace/update-config"
    status=0
    docker run --rm --pull=never --name "$name" --entrypoint /bin/sh \
      --mount "type=bind,src=$test_root,dst=/tests/update-config,readonly" \
      --mount "type=bind,src=$workspace/update-config,dst=/usr/local/bin/update-config,readonly" \
      "$image" -eu -c 'touch /etc/rtorrent-legacy; exec /tests/update-config/"$1"' sh "$entrypoint" >"$workspace/output" 2>&1 || status=$?
    [ "$status" -eq "$signal_status" ] || { cat "$workspace/output" >&2; printf 'not ok - %s %s status %s\n' "$entrypoint" "$signal_name" "$status" >&2; exit 1; }
    if grep -Fq 'not ok -' "$workspace/output"; then cat "$workspace/output" >&2; exit 1; fi
    printf 'ok - %s terminates on %s with %s\n' "$entrypoint" "$signal_name" "$signal_status"
  done
done
