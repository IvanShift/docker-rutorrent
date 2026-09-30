#!/bin/sh
set -eu

if [ "$#" -ne 2 ]; then
  printf 'Usage: %s IMAGE current|legacy\n' "$0" >&2
  exit 64
fi
image=$1
variant=$2
case "$variant" in
  current)
    expected='log.open_file.flush = "rtorrent", "/tmp/rtorrent.log"'
    obsolete='log.open_file = "rtorrent", "/tmp/rtorrent.log"'
    ;;
  legacy)
    expected='log.open_file = "rtorrent", "/tmp/rtorrent.log"'
    obsolete='log.open_file.flush = "rtorrent", "/tmp/rtorrent.log"'
    ;;
  *)
    printf 'Unknown rTorrent version: %s\n' "$variant" >&2
    exit 64
    ;;
esac

volume="rutorrent-log-config-$$-$(date +%s)"
cleanup() { docker volume rm "$volume" >/dev/null 2>&1 || true; }
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
fail() { printf 'not ok - %s log config: %s\n' "$variant" "$1" >&2; exit 1; }
boot() {
  docker run --rm --network none --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    -v "$volume:/config" -e CHECK_PERM_DATA=false -e ALLOW_UNAUTHENTICATED_HTTP=true \
    -e EXPECTED_LOG="$expected" -e OBSOLETE_LOG="$obsolete" \
    --entrypoint sh "$image" -ec "$1"
}

docker image inspect "$image" >/dev/null 2>&1 || fail "image not found: $image"
docker volume create "$volume" >/dev/null
boot '
  sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
  grep -Fxq "$EXPECTED_LOG" /config/rtorrent/.rtorrent.rc
  ! grep -Fxq "$OBSOLETE_LOG" /config/rtorrent/.rtorrent.rc
' || fail 'fresh template did not select the supported log command'
boot '
  mkdir -p /config/rtorrent
  { printf "%s\n" "# owner note" "$OBSOLETE_LOG" "log.add_output = \"error\", \"rtorrent\""; } > /config/rtorrent/.rtorrent.rc
  sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
  grep -Fxq "$EXPECTED_LOG" /config/rtorrent/.rtorrent.rc
  ! grep -Fxq "$OBSOLETE_LOG" /config/rtorrent/.rtorrent.rc
  grep -Fxq "# owner note" /config/rtorrent/.rtorrent.rc
  grep -Fxq "log.add_output = \"error\", \"rtorrent\"" /config/rtorrent/.rtorrent.rc
' || fail 'persisted shipped default was not migrated'
boot '
  before=$(sha256sum /config/rtorrent/.rtorrent.rc)
  sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
  test "$(sha256sum /config/rtorrent/.rtorrent.rc)" = "$before"
' || fail 'second boot changed migrated config'
boot '
  printf "%s\n" "log.open_file = \"rtorrent\", \"/custom/log.txt\"" "# custom path" > /config/rtorrent/.rtorrent.rc
  before=$(sha256sum /config/rtorrent/.rtorrent.rc)
  sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
  test "$(sha256sum /config/rtorrent/.rtorrent.rc)" = "$before"
' || fail 'custom log path was changed'
if [ "$variant" = legacy ]; then
  boot '
    printf "%s\n" "  log.open_file.flush  = \"rtorrent\", \"/tmp/operator.log\"" "# log.open_file.flush is mentioned, not active" > /config/rtorrent/.rtorrent.rc
    sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
    grep -Fxq "  log.open_file  = \"rtorrent\", \"/tmp/operator.log\"" /config/rtorrent/.rtorrent.rc
    grep -Fxq "# log.open_file.flush is mentioned, not active" /config/rtorrent/.rtorrent.rc
  ' || fail 'persisted custom flush opener was not made legacy-compatible'
fi
if [ "$variant" = current ]; then
  boot '
    printf "%s\n" "$OBSOLETE_LOG" "log.open_file.flush = \"rtorrent\", \"/custom/log.txt\"" > /config/rtorrent/.rtorrent.rc
    before=$(sha256sum /config/rtorrent/.rtorrent.rc)
    sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
    test "$(sha256sum /config/rtorrent/.rtorrent.rc)" = "$before"
  ' || fail 'competing custom logging was changed'
  boot '
    printf "%s\n" "$OBSOLETE_LOG # operator note" > /config/rtorrent/.rtorrent.rc
    before=$(sha256sum /config/rtorrent/.rtorrent.rc)
    sh /usr/local/bin/startup true >/tmp/startup.log 2>&1
    test "$(sha256sum /config/rtorrent/.rtorrent.rc)" = "$before"
  ' || fail 'operator comment was changed'
fi
printf 'ok - %s fresh and persisted log config migration\n' "$variant"
