#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"

image=$1
suffix="identity-$$-$(date +%s)"

cleanup() { smoke_remove_run_containers || true; }

smoke_install_traps

check_identity() {
  label=$1
  requested_uid=$2
  uid=$(printf '%s' "$requested_uid" | sed 's/^0*//')
  requested_gid=$3
  case "$requested_gid" in users) gid=100 ;; *) gid=$requested_gid ;; esac
  group_name=$4
  container="rutorrent-${label}-${suffix}"
  smoke_run -d --network none --name "$container" -e "UID=$requested_uid" -e "GID=$requested_gid" "$image" >/dev/null
  wait_for_health "$container" || fail "$label container did not become healthy"
  [ "$(docker exec "$container" id -u torrent)" = "$uid" ] || fail "$label torrent UID"
  [ "$(docker exec "$container" id -g torrent)" = "$gid" ] || fail "$label torrent primary GID"
  [ "$(docker exec "$container" getent group "$gid" | cut -d: -f1)" = "$group_name" ] \
    || fail "$label group reuse/remap"
  docker restart "$container" >/dev/null
  wait_for_health "$container" || fail "$label repeat boot did not become ready"
  [ "$(docker exec "$container" stat -c '%u:%g' /data/.session)" = "$uid:$gid" ] \
    || fail "$label repeat boot persisted session identity"
  [ "$(docker exec "$container" stat -c '%u:%g' /config/rutorrent/erasedata-jobs)" = "$uid:$gid" ] \
    || fail "$label repeat boot erasedata identity"
  rtorrent_pid=$(docker exec "$container" pidof rtorrent) || fail "$label process lookup"
  case "$rtorrent_pid" in *' '*) fail "$label has multiple rTorrent processes" ;; esac
  process_ids=$(docker exec "$container" awk '/^Uid:/ {u=$2} /^Gid:/ {g=$2} END {print u ":" g}' "/proc/$rtorrent_pid/status") \
    || fail "$label process identity read"
  [ "$process_ids" = "$uid:$gid" ] || fail "$label process identity: $process_ids"
  printf 'ok - %s first and repeat boot use %s:%s (%s)\n' "$label" "$uid" "$gid" "$group_name"
}

smoke_check_image "$image"
check_identity default 991 991 torrent
check_identity custom 1000 1000 torrent
check_identity existing_group 1000 100 users
check_identity gid_only 991 100 users
# Canonicalize decimal UID spelling before comparing persisted ownership.
check_identity leading_zero 0991 100 users
check_identity named_group 1000 users users
check_identity root_group 1000 0 root

# A persisted file with the same UID and old GID must be corrected on restart.
gid_container="rutorrent-gid_only-${suffix}"
docker exec --user 0:0 "$gid_container" sh -ec '
  touch /config/legacy-group /data/legacy-group
  chown 991:991 /config/legacy-group /data/legacy-group
'
docker restart "$gid_container" >/dev/null
wait_for_health "$gid_container" || fail 'GID-only restart did not become healthy'
[ "$(docker exec "$gid_container" stat -c '%u:%g' /config/legacy-group)" = '991:100' ] \
  || fail 'GID-only restart did not repair config group'
[ "$(docker exec "$gid_container" stat -c '%u:%g' /data/legacy-group)" = '991:100' ] \
  || fail 'GID-only restart did not repair data group'
printf 'ok - GID-only restart repairs persisted ownership\n'

# An occupied UID must keep its existing account and still run under the
# requested numeric identity.
occupied_container="rutorrent-existing-uid-${suffix}"
smoke_run -d --network none --name "$occupied_container" -e UID=65534 -e GID=100 "$image" >/dev/null
wait_for_health "$occupied_container" || fail 'existing-UID container did not become healthy'
[ "$(docker exec "$occupied_container" getent passwd 65534 | cut -d: -f1)" = nobody ] \
  || fail 'existing UID account changed'
rtorrent_pid=$(docker exec "$occupied_container" pidof rtorrent | awk '{print $1}')
[ -n "$rtorrent_pid" ] || fail 'existing-UID rTorrent process missing'
process_ids=$(docker exec "$occupied_container" awk '
  /^Uid:/ { uid = $2 }
  /^Gid:/ { gid = $2 }
  END { print uid ":" gid }
' "/proc/$rtorrent_pid/status")
[ "$process_ids" = '65534:100' ] || fail 'existing-UID process identity'
docker logs "$occupied_container" 2>&1 | grep -F 'UID 65534 already assigned to nobody' >/dev/null \
  || fail 'existing-UID warning'
docker restart "$occupied_container" >/dev/null
wait_for_health "$occupied_container" || fail 'occupied UID repeat boot readiness'
[ "$(docker exec "$occupied_container" stat -c '%u:%g' /data/.session)" = '65534:100' ] \
  || fail 'occupied UID persisted session ownership'
printf 'ok - occupied UID 65534 first and repeat boot reuse nobody with GID 100\n'

# Invalid account identities must fail at configuration validation, not later services.
for invalid_uid in torrent root 0 -1 991x; do
  invalid_container="rutorrent-invalid-uid-${invalid_uid#-}-${suffix}"
  smoke_run -d --network none --name "$invalid_container" -e "UID=$invalid_uid" "$image" >/dev/null
  wait_for_exit "$invalid_container" || fail "invalid UID $invalid_uid did not stop startup"
  [ "$(docker inspect --format '{{.State.ExitCode}}' "$invalid_container")" = 64 ] \
    || fail "invalid UID $invalid_uid status"
  logs=$(docker logs "$invalid_container" 2>&1) || fail 'invalid UID logs'
  printf '%s\n' "$logs" | grep -F UID >/dev/null || fail "invalid UID $invalid_uid diagnostic"
  smoke_remove_container "$invalid_container"
done
printf 'ok - malformed and root UID settings fail before services start\n'
