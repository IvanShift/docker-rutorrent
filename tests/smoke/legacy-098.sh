#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
name="smoke-native-legacy-$$-$(date +%s)"
cleanup() { docker rm -f -v "$name" >/dev/null 2>&1 || true; }
smoke_install_traps
version=$(smoke_run --rm --network none --entrypoint rtorrent "$image" -h 2>&1 | sed -n '1p')
[ "$version" = "Rakshasa's BitTorrent client version 0.9.8." ] || fail "unexpected rTorrent version: $version"
start_container() (
  fixture=$1
  set -- -d --name "$name" --network none \
    --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
    --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly"
  if [ -n "${STARTUP_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$STARTUP_SOURCE,dst=/usr/local/bin/startup,readonly"; fi
  if [ -n "${UPDATE_CONFIG_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$UPDATE_CONFIG_SOURCE,dst=/usr/local/bin/update-config,readonly"; fi
  if [ -n "${RUNTIME_LIB_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$RUNTIME_LIB_SOURCE,dst=/usr/local/lib/rutorrent,readonly"; fi
  if [ -n "${RTORRENT_RC_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$RTORRENT_RC_SOURCE,dst=/usr/local/share/rutorrent-defaults/rtorrent.rc,readonly"; fi
  if [ -n "${SERVICES_SOURCE:-}" ]; then
    for service_file in "$SERVICES_SOURCE"/*/run "$SERVICES_SOURCE"/*/finish "$SERVICES_SOURCE"/*/crash "$SERVICES_SOURCE"/*/timeout-kill "$SERVICES_SOURCE"/.s6-svscan/*; do
      [ -f "$service_file" ] || continue
      set -- "$@" --mount "type=bind,src=$service_file,dst=/etc/s6.d/${service_file#"$SERVICES_SOURCE"/},readonly"
    done
  fi
  if [ -n "${HEALTHCHECK_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$HEALTHCHECK_SOURCE,dst=/usr/local/bin/rutorrent-healthcheck,readonly"; fi
  if [ -n "${LIBEXEC_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$LIBEXEC_SOURCE,dst=/usr/local/libexec,readonly"; fi
  if [ -n "${ROOTFS_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$ROOTFS_SOURCE,dst=/candidate-rootfs,readonly"; fi
  if [ -n "${TOKENIZER_APK_SOURCE:-}" ]; then set -- "$@" --mount "type=bind,src=$TOKENIZER_APK_SOURCE,dst=/candidate-apks,readonly"; fi
  smoke_run "$@" -e SMOKE_FIXTURE="$fixture" --entrypoint sh "$image" -ec '
    if [ -d /candidate-rootfs ]; then
      cp -R /candidate-rootfs/. /
      mkdir -p /usr/local/share/rutorrent-defaults
      cp /candidate-rootfs/home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc
    fi
    if [ -d /candidate-apks ]; then apk add --no-network /candidate-apks/php85-tokenizer-*.apk >/dev/null; fi
    if [ "$SMOKE_FIXTURE" = migrated ]; then
      mkdir -p /config/rtorrent
      cp /tests/smoke/legacy-working.rc /config/rtorrent/.rtorrent.rc
    fi
    exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d
  ' >/dev/null
)
probe() {
  [ "$(docker inspect --format '{{.State.Running}}' "$name")" = true ] || { smoke_diagnostics "$name"; fail 'legacy daemon stopped'; }
  docker exec --user 991:991 --workdir /rutorrent/app/php "$name" php85 /tests/smoke/daemon-settings.php 0.9.8 \
    || { smoke_diagnostics "$name"; fail 'legacy internal PHP-to-SCGI reply'; }
}
for fixture in stock migrated; do
  start_container "$fixture"
  wait_for_health "$name" 120 || fail "legacy $fixture startup"
  probe
  if [ "$fixture" = stock ]; then
    "$smoke_dir/rtorrent-log-config-migration.sh" "$image" legacy
  fi
  # Readiness is not a one-time success: nested checks must not hide a daemon
  # that stopped while they ran. Repeat the actual SCGI request afterwards.
  probe
  cleanup
done
printf 'ok - legacy stock and migrated persisted rc accepted by live daemon 0.9.8\n'
