#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
repo_root=$(CDPATH= cd -- "$smoke_dir/../.." && pwd)
# Execute the documented command, so a missing parent cannot be hidden by a copy.
repair_command=$(sed -n 's/^docker run .* alpine:3\.24\.2 sh -c "\(chmod 0755 \/config .*\)"$/\1/p' "$repo_root/README.md")
[ -n "$repair_command" ] || fail 'README offline permissions command not found'
owner="upgrade-permissions-$$-$(date +%s)"
container=''
cleanup() {
  [ -n "$container" ] || return 0
  actual_owner=$(docker inspect -f '{{index .Config.Labels "io.ivanshift.smoke.owner"}}' "$container" 2>/dev/null) || return 0
  [ "$actual_owner" = "$owner" ] || fail 'refusing cleanup of an unowned container'
  docker rm -f -v "$container" >/dev/null
  container=''
}
smoke_install_traps
for variant in normal all777; do
  set -- -d --network none --stop-timeout 60 --label "io.ivanshift.smoke.owner=$owner" \
    --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
    --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    -e HTTP_AUTH=false -e ALLOW_UNAUTHENTICATED_HTTP=true \
    -e "REPAIR_COMMAND=$repair_command" -e "PROFILE_VARIANT=$variant"
  if [ -n "${ROOTFS_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$ROOTFS_SOURCE,dst=/candidate-rootfs,readonly"
  fi
  if [ -n "${RENAME_SECCOMP:-}" ]; then set -- "$@" --security-opt "seccomp=$RENAME_SECCOMP"; fi
  container=$(smoke_run "$@" --entrypoint sh "$image" -ec '
    if [ -d /candidate-rootfs ]; then
      cp /candidate-rootfs/usr/local/bin/startup /usr/local/bin/startup
      cp /candidate-rootfs/usr/local/lib/rutorrent/startup.php /usr/local/lib/rutorrent/startup.php
      cp /candidate-rootfs/usr/local/libexec/migrate-rtorrent-config.php /usr/local/libexec/migrate-rtorrent-config.php
      cp /candidate-rootfs/home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc
    fi
    mkdir -p /config/rtorrent /config/custom_plugins /config/custom_themes \
      /config/rutorrent/conf/users/alice /config/rutorrent/share/users/alice/settings/erasedata \
      /config/rutorrent/share/settings/erasedata /data/.session/.rutorrent-stop-intents /data/downloads/nested
    printf "%s\n" "session.path.set = /data/.session" "directory.default.set = /data/downloads" \
      "network.scgi.open_local = /run/rtorrent/rtorrent.sock" "system.daemon.set = true" > /config/rtorrent/.rtorrent.rc
    chmod 02700 /data/.session/.rutorrent-stop-intents
    if [ "$PROFILE_VARIANT" = all777 ]; then find /config /data -exec chmod 0777 {} +; fi
    sh -c "$REPAIR_COMMAND"
    exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d
  ')
  if [ -n "${SMOKE_RESOURCE_LEDGER:-}" ]; then
    printf 'container %s owner %s image %s variant %s\n' "$container" "$owner" "$image" "$variant" >> "$SMOKE_RESOURCE_LEDGER"
  fi
  wait_for_health "$container" 30 || fail "repaired $variant profile did not start"
  [ "$(docker inspect -f '{{.RestartCount}}' "$container")" = 0 ] || fail 'repaired profile restarted'
  docker exec "$container" sh -ec '
    test "$(stat -c %a /config/rtorrent)" = 755
    test "$(stat -c %a /config/custom_plugins)" = 755
    test "$(stat -c %a /config/custom_themes)" = 755
    test "$(stat -c %a /config/rutorrent/share/users/alice/settings/erasedata)" = 700
    test "$(stat -c %a /data/.session/.rutorrent-stop-intents)" = 700
  ' || fail 'repair did not preserve admitted directory modes'
  logs=$(docker logs "$container" 2>&1)
  if printf '%s' "$logs" | grep -q 'Cannot attest'; then fail 'repair left a startup admission refusal'; fi
  printf 'ok - documented %s profile repair starts healthy without a restart\n' "$variant"
  cleanup
done
