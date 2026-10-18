#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { echo 'Usage: ownership-remaining.sh IMAGE [OWNERSHIP_HELPER_BINARY]' >&2; exit 64; }
image=$1
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
helper=${2:-}
count=0
for scenario in fresh filebot-uid filebot-media filebot-data-link filebot-license-link filebot-license-hardlink filebot-license-missing filebot-license-unreadable filebot-cache-link filebot-custom-fallback filebot-manual-root filebot-named-group filebot-root-zero-refused filebot-defaults-missing rc-default-missing env-auth-drift env-rpc-drift groupmod-failure usermod-failure claim-pair claim-pair-uppercase claim-wrong-token claim-different-inode claim-third-link claim-malformed claim-foreign claim-mode claim-parent-mode private-hardlink preowned-private-access deep-data deep-data-root-owned; do
  expected=0
  case "$scenario" in *-link|*-hardlink|*-missing|*-unreadable|*-drift|*-failure|claim-wrong-token|claim-different-inode|claim-third-link|claim-malformed|claim-foreign|claim-mode|claim-parent-mode|filebot-root-zero-refused) expected=78 ;; esac
  count=$((count+1))
  output=$(mktemp)
  status=0
  set -- --rm --network none --name "ownership-remaining-${$}-${count}" --entrypoint /bin/sh \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/startup,dst=/usr/local/bin/startup,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/postdl,dst=/usr/local/bin/postdl,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/postrm,dst=/usr/local/bin/postrm,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/lib/rutorrent,dst=/usr/local/lib/rutorrent,readonly" \
    --mount "type=bind,src=$repo/rootfs/etc/nginx,dst=/candidate-nginx,readonly" \
    --mount "type=bind,src=$repo/tests/smoke/fixtures/ownership-remaining,dst=/ownership-fixtures,readonly"
  if [ -n "$helper" ]; then set -- "$@" --mount "type=bind,src=$helper,dst=/usr/local/bin/rutorrent-own-tree,readonly"; fi
  if [ "$scenario" = preowned-private-access ]; then set -- "$@" --cap-drop DAC_OVERRIDE --cap-drop DAC_READ_SEARCH; fi
  # Overlay source consumers individually; retain the image's compiled helpers.
  smoke_run_with_libexec "$repo/rootfs/usr/local/libexec" "$@" "$image" /ownership-fixtures/run.sh "$scenario" > "$output" 2>&1 || status=$?
  if [ "$status" -ne "$expected" ]; then cat "$output" >&2; rm -f "$output"; echo "FAIL $scenario expected $expected got $status" >&2; exit 1; fi
  if [ "$expected" -eq 78 ]; then grep -Eq 'Cannot attest|Cannot configure|recreate the container|requires a configured non-root service identity' "$output" || { cat "$output"; exit 1; }; fi
  case "$scenario" in
    filebot-license-missing) grep -F /config/filebot/license.psm "$output" >/dev/null ;;
    filebot-defaults-missing) grep -F 'Cannot attest image FileBot defaults: /usr/local/share/rutorrent-defaults/args_amc.txt is missing' "$output" >/dev/null ;;
    rc-default-missing) grep -F 'Image rc default is missing' "$output" >/dev/null ;;
    groupmod-failure|usermod-failure) ! grep -F raw- "$output" >/dev/null ;;
  esac
  rm -f "$output"
  printf 'PASS ownership startup %s (%s)\n' "$scenario" "$status"
done
printf 'PASS ownership/FileBot focused cases: %s\n' "$count"
