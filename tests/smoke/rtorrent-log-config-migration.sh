#!/bin/sh
# Text migration cases live in tests/update-config/fixtures. This checks a real
# fresh template and migrated historical spellings across persisted boots; legacy-098.sh also loads migrated rc.
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
# libtorrent a755a7a renamed the log groups; 0.9.8 keeps the cascading names.
case "$variant" in
  current) expected='log.open_file.flush = "rtorrent", "/tmp/rtorrent.log"'; group='log.add_output = "errors", "rtorrent"' ;;
  legacy) expected='log.open_file = "rtorrent", "/tmp/rtorrent.log"'; group='log.add_output = "error", "rtorrent"' ;;
  *) printf 'Unknown variant: %s\n' "$variant" >&2; exit 64 ;;
esac
smoke_check_image "$image"
volume="smoke-native-log-$$-$(date +%s)"
container="smoke-native-migrated-$$-$(date +%s)"
cleanup() {
  smoke_remove_run_containers || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
smoke_install_traps
docker volume create "$volume" >/dev/null
smoke_startup_case "$image" '
  startup
  grep -Fx "$EXPECTED_LOG" /config/rtorrent/.rtorrent.rc >/dev/null
  test "$(grep "^log\.add_output" /config/rtorrent/.rtorrent.rc)" = "$EXPECTED_GROUP"
  # Explicit if makes an obsolete opener a failing assertion under sh -e.
  case "$VARIANT" in
    current) obsolete="log.open_file = \"rtorrent\", \"/tmp/rtorrent.log\"" ;;
    legacy) obsolete="log.open_file.flush = \"rtorrent\", \"/tmp/rtorrent.log\"" ;;
  esac
  if grep -Fx "$obsolete" /config/rtorrent/.rtorrent.rc >/dev/null; then exit 1; fi
  snapshot /config/rtorrent/.rtorrent.rc > /config/rtorrent/fresh-rc.snapshot
' -v "$volume:/config" -e CHECK_PERM_DATA=false -e EXPECTED_LOG="$expected" -e EXPECTED_GROUP="$group" -e VARIANT="$variant" \
  || fail "$variant log config: fresh template"
smoke_startup_case "$image" '
  before=$(cat /config/rtorrent/fresh-rc.snapshot)
  startup
  unchanged /config/rtorrent/.rtorrent.rc "$before"
' -v "$volume:/config" -e CHECK_PERM_DATA=false \
  || fail "$variant log config: persisted fresh template idempotence and metadata"
printf 'ok - %s fresh log template and persisted metadata/idempotence\n' "$variant"

# The current native daemon must interpret the output, rather than only compare
# text goldens. Legacy daemon acceptance already lives in legacy-098.sh.
if [ "$variant" = current ]; then
  smoke_startup_case "$image" '
    sed -i \
      -e "s/network.listen.port.range.set/port_range/g" \
      -e "s/network.listen.port.random.set/port_random/g" \
      -e "s/dht.override_port.set/dht_port/g" \
      -e "s/protocol.encryption.set = allow/encryption = allow_incoming,try_outgoing,enable_retry/g" \
      -e "s/method.set_key/system.method.set_key/g" \
      -e "s/execute.nothrow=/execute2=/g" \
      -e "s/execute.nothrow =/execute2 =/g" \
      -e "s/^log.add_output = \"errors\", \"rtorrent\"\$/log.add_output = \"error\", \"rtorrent\"/" /config/rtorrent/.rtorrent.rc
    printf "%s\n" "system.method.insert = smoke.migrated.simple,simple,\"execute2=echo,native-migration-ok\"" >> /config/rtorrent/.rtorrent.rc
    # An operator line with a removed cascading group expands to current groups.
    printf "%s\n" "log.add_output = \"info\", \"rtorrent\"" >> /config/rtorrent/.rtorrent.rc
    grep -Fx "log.add_output = \"error\", \"rtorrent\"" /config/rtorrent/.rtorrent.rc >/dev/null
  ' -v "$volume:/config" -e CHECK_PERM_DATA=false || fail 'historical native rc seed'
  smoke_run -d --name "$container" --network none --stop-timeout 60 \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    -v "$volume:/config" "$image" >/dev/null
  wait_for_health "$container" || fail 'native daemon rejected migrated historical rc'
  expected_version=${SMOKE_EXPECTED_RTORRENT_VERSION:-$(sed -n 's/^ARG RTORRENT_BRANCH=v//p' "$smoke_dir/../../Dockerfile")}
  docker exec --user 991:991 --workdir /rutorrent/app/php "$container" php85 /tests/smoke/daemon-settings.php "$expected_version" migrated-hook \
    || fail 'migrated native daemon application SCGI reply'
  docker exec "$container" sh -eu -c '
    test "$(sed -n "1p" /config/rtorrent/.rtorrent.rc)" = '\''catch = {"system.file_name.allow_legacy_utf8.set=1"}'\''
    grep -F "method.insert = smoke.migrated.simple,simple,\"execute.nothrow=echo,native-migration-ok\"" /config/rtorrent/.rtorrent.rc >/dev/null
    test "$(grep "^log\.add_output" /config/rtorrent/.rtorrent.rc)" = "$(printf "%s\n" \
      "log.add_output = \"errors\", \"rtorrent\"" \
      "log.add_output = \"events\", \"rtorrent\"" \
      "log.add_output = \"storage_errors\", \"rtorrent\"")"
  ' || fail 'migrated native hook, log groups and first-load guard'
  docker stop --timeout 60 "$container" >/dev/null
  [ "$(docker inspect --format '{{.State.ExitCode}}' "$container")" = 0 ] || fail 'migrated native graceful stop'
  printf 'ok - live native daemon accepts migrated historical rc, log groups and nested method alias\n'
fi
