#!/bin/sh
# Execute the documented offline permissions repair and attestation on old,
# metadata-less and startup-generated profiles, then start without a restart.
# UPGRADE_VARIANTS selects cases; RENAME_SECCOMP models 9p rename refusal.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
repo_root=$(CDPATH= cd -- "$smoke_dir/../.." && pwd)
readme=$repo_root/README.md
# Execute the documented commands, so a missing path cannot be hidden by a copy.
repair_command=$(sed -n 's/^docker run .* alpine:3\.24\.2 sh -c "\(chmod 0755 \/config .*\)"$/\1/p' "$readme")
[ -n "$repair_command" ] && [ "$(printf '%s\n' "$repair_command" | wc -l)" -eq 1 ] || fail 'README offline permissions command not found'
# The root-owner command for a restore that also lost owners (drvfs uid=0;gid=0).
root_owner_command=$(sed -n 's/^docker run .* alpine:3\.24\.2 -ec "\(find \/config \/data -user 0 .*\)"$/\1/p' "$readme")
[ -n "$root_owner_command" ] && [ "$(printf '%s\n' "$root_owner_command" | wc -l)" -eq 1 ] || fail 'README root-owner command not found'
container=''
# smoke_run labels every container with this run's token; its cleanup removes them.
cleanup() {
  smoke_remove_run_containers force || fail 'refusing or failing cleanup of the owned container'
  container=''
}
smoke_install_traps
for variant in ${UPGRADE_VARIANTS:-readme normal all777 partial restored restored-root checkperm-false}; do
  if [ "$variant" = readme ]; then
    # Linux users run the same shell bodies under POSIX quoting.
    posix_command=$(sed -n "s/^  alpine:3\\.24\\.2 sh -c '\\(chmod 0755 \\/config .*\\)'\$/\\1/p" "$readme")
    [ "$posix_command" = "$repair_command" ] || fail 'README POSIX and cmd.exe permission bodies differ'
    grep -E '^docker run .* -e UID=[0-9]+ -e CHECK_PERM_DATA=(true|false) .* alpine:3\.24\.2 sh -c "chmod 0755 /config ' "$readme" >/dev/null \
      || fail 'README permissions command does not pass UID and CHECK_PERM_DATA'
    posix_owner_command=$(sed -n "s/^  alpine:3\\.24\\.2 -ec '\\(find \\/config \\/data -user 0 .*\\)'\$/\\1/p" "$readme")
    [ "$posix_owner_command" = "$(printf '%s' "$root_owner_command" | sed 's/991/1000/g')" ] \
      || fail 'README POSIX and cmd.exe root-owner bodies differ beyond the example identity'
    # cmd.exe expands % and, with delayed expansion, !; neither quote may nest.
    for body in "$repair_command" "$root_owner_command"; do
      case "$body" in *\'*|*\"*|*%*|*!*|*\`*) fail 'README repair bodies are not portable between cmd.exe and POSIX quoting' ;; esac
    done
    printf 'ok - README cmd.exe and POSIX permission and root-owner commands share portable bodies\n'
    continue
  fi
  check_perm=true auth=false
  case "$variant" in
    restored|restored-root) auth=true ;;
    checkperm-false) check_perm=false ;;
  esac
  # A restart policy makes the RestartCount check below observe a startup loop.
  set -- -d --network none --stop-timeout 60 --restart unless-stopped \
    --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
    --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" \
    -e "HTTP_AUTH=$auth" -e ALLOW_UNAUTHENTICATED_HTTP=true -e "CHECK_PERM_DATA=$check_perm" \
    -e "REPAIR_COMMAND=$repair_command" -e "ROOT_OWNER_COMMAND=$root_owner_command" -e "PROFILE_VARIANT=$variant"
  if [ -n "${ROOTFS_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$ROOTFS_SOURCE,dst=/candidate-rootfs,readonly"
  fi
  if [ -n "${RENAME_SECCOMP:-}" ]; then set -- "$@" --security-opt "seccomp=$RENAME_SECCOMP"; fi
  container=$(smoke_run "$@" --entrypoint sh "$image" -ec '
    if [ -d /candidate-rootfs ]; then
      # Script overlay only: compiled helpers stay those of the image.
      cp -R /candidate-rootfs/. /
      cp /candidate-rootfs/home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc
    fi
    state="{\"version\":1,\"user\":\"\",\"generation\":\"0000000000000001\",\"acknowledged\":\"0000000000000001\",\"phase\":\"disarmed\",\"journal\":[],\"diagnostics\":[]}"
    controls() {
      : > "$1/scheduler.lock"
      printf "%s" "$state" > "$1/.drain-state"
      printf "%064d" 0 > "$1/.private-root-key"
      printf v1 > "$1/.private-root-key-state"
    }
    restored=false
    case "$PROFILE_VARIANT" in restored|restored-root) restored=true ;; esac
    if [ "$restored" = true ]; then
      # Let startup create its own profile, so every admitted path exists:
      # protected queue and witness, HTTP authentication and FileBot data.
      mkdir -p /filebot /config/filebot
      printf "#!/bin/sh\nexit 0\n" > /filebot/filebot.sh
      chmod 0755 /filebot/filebot.sh
      printf "music=y\n" > /filebot/args_amc.txt
      printf license > /config/filebot/license
      export FILEBOT_LICENSE=/config/filebot/license
      /usr/local/bin/startup /bin/true
      mkdir -p /config/rutorrent/conf/users/alice /config/rutorrent/share/users/alice/settings
      mkdir -m 0700 /config/rutorrent/erasedata-jobs/default
      controls /config/rutorrent/erasedata-jobs/default
      chown -R "$UID:$GID" /config/rutorrent/erasedata-jobs/default
      printf "user:hash\n" > /config/nginx/passwd/rutorrent_passwd
      mkdir -p /data/.session/.rutorrent-stop-intents
      printf record > /data/.session/.rutorrent-stop-intents/record
      # One saved torrent, so the restored session has ordinary daemon records.
      hash=$(php85 /tests/smoke/torrent-fixture.php restored.bin /tmp/restored.torrent /data/downloads restored-payload)
      cp /tmp/restored.torrent "/data/.session/$hash.torrent"
      printf de > "/data/.session/$hash.torrent.rtorrent"
      printf de > "/data/.session/$hash.torrent.libtorrent_resume"
      printf "%s\n" "$hash" > /tmp/restored-hash
      chown -R "$UID:$GID" /data/.session /data/downloads/restored.bin
      mkdir -p /data/downloads/WinCreated
      # Interrupted erasedata captures: private workspaces with their own
      # markers, and captured user data that keeps whatever modes it had.
      capture=/data/downloads/.erasedata-entry-lab
      mkdir -p "$capture/entry/sub" /data/downloads/.erasedata-rmdir-lab /data/downloads/.erasedata-recovery-lab
      printf signature > "$capture/.initialized"
      printf bmFtZQ== > "$capture/.name"
      printf user-data > "$capture/entry/sub/.initialized"
      chown -R "$UID:$GID" "$capture" /data/downloads/.erasedata-rmdir-lab /data/downloads/.erasedata-recovery-lab
    else
      mkdir -p /config/rtorrent /config/custom_plugins /config/custom_themes \
        /config/rutorrent/conf/users/alice /config/rutorrent/share/users/alice/settings/erasedata \
        /config/rutorrent/share/settings/erasedata /data/.session/.rutorrent-stop-intents /data/downloads/nested
      printf "%s\n" "session.path.set = /data/.session" "directory.default.set = /data/downloads" \
        "network.scgi.open_local = /run/rtorrent/rtorrent.sock" "system.daemon.set = true" > /config/rtorrent/.rtorrent.rc
      chmod 02700 /data/.session/.rutorrent-stop-intents
      controls /config/rutorrent/share/settings/erasedata
      if [ "$PROFILE_VARIANT" = checkperm-false ]; then
        mkdir -m 0777 /data/downloads/WinCreated /data/downloads/ServiceOwned
        chmod 0777 /data/downloads/WinCreated /data/downloads/ServiceOwned
        chown "$UID:$GID" /data/downloads/ServiceOwned
      fi
    fi
    # A missing optional path must not stop the remaining repairs.
    if [ "$PROFILE_VARIANT" = partial ]; then
      rm -r /config/rtorrent/.rtorrent.rc /config/rutorrent/share
    fi
    # A Windows copy restores entries without Linux metadata. On drvfs volumes
    # mounted with uid=0;gid=0 it also loses the owners: everything is root:root.
    case "$PROFILE_VARIANT" in
      all777|partial|restored) find /config /data -exec chmod 0777 {} + ;;
      restored-root) chown -R -h 0:0 /config /data; find /config /data -exec chmod 0777 {} + ;;
    esac
    # README order: root-owner command, permissions command, then attestation.
    if [ "$PROFILE_VARIANT" = restored-root ]; then sh -ec "$ROOT_OWNER_COMMAND"; fi
    sh -c "$REPAIR_COMMAND"
    if [ "$restored" = true ]; then
      # The restored witness is refused with the command that repairs it.
      if /usr/local/bin/startup /bin/true > /tmp/witness-refusal.log 2>&1; then
        echo "restored witness was admitted without attestation" >&2; exit 1
      fi
      grep -F "repair the root:root 0400 witness" /tmp/witness-refusal.log >/dev/null
      grep -F -- "--entrypoint attest-erasedata-queue" /tmp/witness-refusal.log >/dev/null
    fi
    case "$PROFILE_VARIANT" in all777|restored|restored-root) /usr/local/bin/attest-erasedata-queue --attest-clean-legacy-queue ;; esac
    exec /usr/local/bin/startup /usr/bin/s6-svscan /etc/s6.d
  ')
  if [ -n "${SMOKE_RESOURCE_LEDGER:-}" ]; then
    printf 'container %s owner %s image %s variant %s\n' "$container" "$smoke_owner" "$image" "$variant" >> "$SMOKE_RESOURCE_LEDGER"
  fi
  # Run-wide cleanup finds the container only by this run's owner label.
  [ "$(docker inspect -f "{{index .Config.Labels \"$SMOKE_OWNER_LABEL\"}}" "$container")" = "$smoke_owner" ] \
    || fail "$variant container lacks this run's owner label"
  wait_for_health "$container" 60 || fail "repaired $variant profile did not start"
  [ "$(docker inspect -f '{{.RestartCount}}' "$container")" = 0 ] || fail "repaired $variant profile restarted"
  docker exec -e "PROFILE_VARIANT=$variant" "$container" sh -ec '
    mode() { stat -c %a "$1"; }
    case "$PROFILE_VARIANT" in restored|restored-root) restored=true ;; *) restored=false ;; esac
    if [ "$restored" = true ]; then
      test "$(stat -c %u:%g:%a:%h:%s /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0
      test "$(stat -c %u:%g:%a /config/rutorrent/erasedata-jobs)" = "$UID:$GID:700"
      test "$(mode /config/rutorrent/erasedata-jobs/default)" = 700
      test "$(mode /config/rutorrent/erasedata-jobs/default/.private-root-key)" = 600
      test "$(stat -c %u:%g:%a /config/nginx/passwd)" = "0:$GID:750"
      test "$(stat -c %u:%g:%a /config/nginx/passwd/rutorrent_passwd)" = "0:$GID:640"
      test "$(mode /config/filebot)" = 755
      test "$(mode /config/rutorrent/share/users/alice/settings)" = 755
      test "$(mode /data/downloads/WinCreated)" = 755
      su-exec "$UID:$GID" touch /data/downloads/WinCreated/after
      capture=/data/downloads/.erasedata-entry-lab
      for expected in "$capture:700" /data/downloads/.erasedata-rmdir-lab:700 /data/downloads/.erasedata-recovery-lab:700 \
        "$capture/.initialized:600" "$capture/.name:600" "$capture/entry/sub/.initialized:777"; do
        [ "$(mode "${expected%:*}")" = "${expected##*:}" ] \
          || { echo "capture workspace ${expected%:*} has mode $(mode "${expected%:*}"), expected ${expected##*:}" >&2; exit 1; }
      done
      # The application admits the repaired protected queue.
      (cd /rutorrent/app/php && su-exec "$UID:$GID" php85 /tests/smoke/queue-admission.php >/dev/null)
      # The daemon loaded the restored session torrent.
      hash=$(cat /tmp/restored-hash)
      test "$(su-exec "$UID:$GID" php85 /tests/smoke/service-rpc.php d.name "$hash")" = restored.bin
      if [ "$PROFILE_VARIANT" = restored-root ]; then
        # The root-owner command returned the data to the service and /config to root.
        for owned in /data/.session "/data/.session/$hash.torrent" "/data/.session/$hash.torrent.rtorrent" \
          /data/.session/.rutorrent-stop-intents/record /config/rutorrent/erasedata-jobs/default/.drain-state \
          "$capture" "$capture/entry/sub/.initialized" /data/downloads/restored.bin; do
          [ "$(stat -c %u "$owned")" = "$UID" ] || { echo "$owned is owned by $(stat -c %u "$owned"), expected $UID" >&2; exit 1; }
        done
        test "$(stat -c %u:%g /config)" = "0:$GID"
        test "$(stat -c %u:%g /config/nginx)" = "0:$GID"
      fi
    else
      test "$(mode /config/rtorrent)" = 755
      test "$(mode /config/custom_plugins)" = 755
      test "$(mode /config/custom_themes)" = 755
      [ "$PROFILE_VARIANT" = partial ] || test "$(mode /config/rutorrent/share/users/alice/settings)" = 755
    fi
    test "$(mode /data/.session)" = 700
    test "$(mode /data/.session/.rutorrent-stop-intents)" = 700
    case "$PROFILE_VARIANT" in
      all777)
        # Attestation retired the repaired idle legacy queues.
        test ! -e /config/rutorrent/share/settings/erasedata
        test "$(stat -c %u:%g:%a:%h:%s /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0 ;;
      partial)
        # Startup seeded the deleted rc and share again after the full repair.
        test -f /config/rtorrent/.rtorrent.rc
        test -d /config/rutorrent/share/settings ;;
      normal)
        test "$(mode /config/rutorrent/share/users/alice/settings/erasedata)" = 700
        test "$(mode /config/rutorrent/share/settings/erasedata/.private-root-key)" = 600 ;;
      checkperm-false)
        # Windows-created directories keep their owner and stay writable.
        test "$(stat -c %u:%g:%a /data/downloads/WinCreated)" = 0:0:777
        su-exec "$UID:$GID" touch /data/downloads/WinCreated/after
        test "$(stat -c %u:%a /data/downloads/ServiceOwned)" = "$UID:755"
        su-exec "$UID:$GID" touch /data/downloads/ServiceOwned/after ;;
    esac
    # The image RU_PROFILE_MASK keeps files that ruTorrent writes, such as the
    # stored tracker credentials, private to the service UID.
    credentials=/config/rutorrent/share/settings/loginmgr.dat
    waited=0
    until [ -f "$credentials" ]; do
      waited=$((waited + 1))
      [ "$waited" -le 60 ] || { echo "$credentials was not written" >&2; exit 1; }
      sleep 1
    done
    [ "$(mode "$credentials")" = 600 ] || { echo "$credentials has mode $(mode "$credentials"), expected 600" >&2; exit 1; }
  ' || fail "repair did not leave the admitted $variant profile modes"
  logs=$(docker logs "$container" 2>&1)
  if printf '%s' "$logs" | grep -q 'Cannot attest'; then fail "repair left a $variant startup admission refusal"; fi
  printf 'ok - documented %s profile repair starts healthy without a restart\n' "$variant"
  cleanup
done
