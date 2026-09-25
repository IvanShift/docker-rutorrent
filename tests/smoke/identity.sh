#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s IMAGE\n' "$0" >&2
  exit 64
fi

image=$1
suffix="identity-$$-$(date +%s)"
containers=""

cleanup() {
  for container in $containers; do
    docker rm -f -v "$container" >/dev/null 2>&1 || true
  done
}

trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

wait_for_health() {
  container=$1
  attempt=0
  while [ "$attempt" -lt 120 ]; do
    status=$(docker inspect --format '{{.State.Status}}' "$container")
    case "$status" in
      exited|dead)
        docker logs "$container" >&2 || true
        return 1
      ;;
    esac
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    case "$status" in
      healthy) return 0 ;;
    esac
    attempt=$((attempt + 1))
    sleep 1
  done
  docker logs "$container" >&2 || true
  return 1
}

check_identity() {
  label=$1
  uid=$2
  gid=$3
  group_name=$4
  container="rutorrent-${label}-${suffix}"
  containers="$containers $container"
  docker run -d --name "$container" -e "UID=$uid" -e "GID=$gid" "$image" >/dev/null
  wait_for_health "$container" || fail "$label container did not become healthy"
  [ "$(docker exec "$container" id -u torrent)" = "$uid" ] || fail "$label torrent UID"
  [ "$(docker exec "$container" id -g torrent)" = "$gid" ] || fail "$label torrent primary GID"
  [ "$(docker exec "$container" getent group "$gid" | cut -d: -f1)" = "$group_name" ] \
    || fail "$label group reuse/remap"
  printf 'ok - %s uses %s:%s (%s)\n' "$label" "$uid" "$gid" "$group_name"
}

docker image inspect "$image" >/dev/null 2>&1 || fail "image not found: $image"
check_identity default 991 991 torrent
check_identity custom 1000 1000 torrent
check_identity existing_group 1000 100 users
check_identity gid_only 991 100 users

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
containers="$containers $occupied_container"
docker run -d --name "$occupied_container" -e UID=65534 -e GID=100 "$image" >/dev/null
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
printf 'ok - occupied UID 65534 reuses nobody with GID 100\n'
