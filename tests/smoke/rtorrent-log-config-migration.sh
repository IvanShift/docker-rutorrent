#!/bin/sh
# Text migration cases live in tests/update-config/fixtures. This checks a real
# fresh template across persisted boots; legacy-098.sh also loads migrated rc.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
. "$smoke_dir/bootstrap-lib.sh"
if [ "$#" -ne 2 ]; then
  printf 'Usage: %s IMAGE current|legacy\n' "$0" >&2
  exit 64
fi
image=$1
variant=$2
case "$variant" in
  current) expected='log.open_file.flush = "rtorrent", "/tmp/rtorrent.log"' ;;
  legacy) expected='log.open_file = "rtorrent", "/tmp/rtorrent.log"' ;;
  *) printf 'Unknown variant: %s\n' "$variant" >&2; exit 64 ;;
esac
smoke_check_image "$image"
volume="rem4-native-log-$$-$(date +%s)"
cleanup() { docker volume rm "$volume" >/dev/null 2>&1 || true; }
smoke_install_traps
docker volume create "$volume" >/dev/null
smoke_startup_case "$image" '
  startup
  grep -Fx "$EXPECTED_LOG" /config/rtorrent/.rtorrent.rc >/dev/null
  # Explicit if makes an obsolete opener a failing assertion under sh -e.
  case "$VARIANT" in
    current) obsolete="log.open_file = \"rtorrent\", \"/tmp/rtorrent.log\"" ;;
    legacy) obsolete="log.open_file.flush = \"rtorrent\", \"/tmp/rtorrent.log\"" ;;
  esac
  if grep -Fx "$obsolete" /config/rtorrent/.rtorrent.rc >/dev/null; then exit 1; fi
  snapshot /config/rtorrent/.rtorrent.rc > /config/rtorrent/fresh-rc.snapshot
' -v "$volume:/config" -e CHECK_PERM_DATA=false -e EXPECTED_LOG="$expected" -e VARIANT="$variant" \
  || fail "$variant log config: fresh template"
smoke_startup_case "$image" '
  before=$(cat /config/rtorrent/fresh-rc.snapshot)
  startup
  unchanged /config/rtorrent/.rtorrent.rc "$before"
' -v "$volume:/config" -e CHECK_PERM_DATA=false \
  || fail "$variant log config: persisted fresh template idempotence and metadata"
printf 'ok - %s fresh log template and persisted metadata/idempotence\n' "$variant"
