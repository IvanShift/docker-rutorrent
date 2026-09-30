#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s IMAGE\n' "$0" >&2
  exit 64
fi

image=$1
suffix="conf-$$-$(date +%s)"
container="rutorrent-${suffix}"
volume="rutorrent-${suffix}"

cleanup() {
  docker rm -f -v "$container" >/dev/null 2>&1 || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
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
  attempt=0
  while [ "$attempt" -lt 90 ]; do
    status=$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container")
    case "$status" in
      healthy) return 0 ;;
      exited|dead) docker logs "$container" >&2; return 1 ;;
    esac
    attempt=$((attempt + 1))
    sleep 1
  done
  docker logs "$container" >&2
  return 1
}

docker image inspect "$image" >/dev/null 2>&1 || fail "image not found: $image"
docker volume create "$volume" >/dev/null
docker run -d --name "$container" -v "$volume:/config" "$image" >/dev/null
wait_for_health || fail 'first boot'
docker exec "$container" test ! -e /config/rutorrent/conf/xmlrpc_proxy.php \
  || fail 'first boot unexpectedly persisted xmlrpc_proxy.php'
docker exec "$container" sh -eu -c '
  test -f /config/rutorrent/conf/plugins.ini
  test -f /rutorrent/conf-dist/xmlrpc_proxy.php
  printf "%s\n" "// operator marker" >> /config/rutorrent/conf/config.php
  cp /rutorrent/conf-dist/xmlrpc_proxy.php /config/rutorrent/conf/xmlrpc_proxy.php
  printf "%s\n" "// proxy override" >> /config/rutorrent/conf/xmlrpc_proxy.php
  rm /config/rutorrent/conf/plugins.ini
  rm /config/rutorrent/conf/users/.gitignore
  ln -s /config/operator-missing /config/rutorrent/conf/users/.gitignore
' || fail 'prepare persisted configuration'

docker restart "$container" >/dev/null
wait_for_health || fail 'restart'
docker exec "$container" test -f /config/rutorrent/conf/plugins.ini \
  || fail 'missing plugins.ini was not seeded'
docker exec "$container" sh -eu -c '
  grep -Fqx "// proxy override" /config/rutorrent/conf/xmlrpc_proxy.php
  grep -Fqx "// operator marker" /config/rutorrent/conf/config.php
  test -L /config/rutorrent/conf/users/.gitignore
  test "$(readlink /config/rutorrent/conf/users/.gitignore)" = /config/operator-missing
  test ! -e /config/operator-missing
' || fail 'operator files or symlinks were not preserved'
logs=$(docker logs "$container" 2>&1)
printf '%s\n' "$logs" | grep -F 'seeded missing conf/plugins.ini' >/dev/null \
  || fail 'missing-file seed not logged'
printf '%s\n' "$logs" | grep -F 'conf/config.php differs from image or is unreadable; keeping existing file' >/dev/null \
  || fail 'operator config difference not logged'
printf '%s\n' "$logs" | grep -F 'conf/users/.gitignore differs from image or is unreadable; keeping existing file' >/dev/null \
  || fail 'dangling symlink difference not logged'
printf '%s\n' "$logs" | grep -F 'conf/xmlrpc_proxy.php differs from image or is unreadable; keeping existing file' >/dev/null \
  || fail 'proxy override difference not logged'
docker exec "$container" sh -eu -c '
  rm /config/rutorrent/conf/xmlrpc_proxy.php
  rm /config/rutorrent/conf/users/.gitignore
  rmdir /config/rutorrent/conf/users
  mkdir /config/operator-users
  ln -s /config/operator-users /config/rutorrent/conf/users
' || fail 'prepare missing policy and symlinked directory'
docker restart "$container" >/dev/null
wait_for_health || fail 'second restart'
docker exec "$container" sh -eu -c '
  test ! -e /config/rutorrent/conf/xmlrpc_proxy.php
  test ! -L /config/rutorrent/conf/xmlrpc_proxy.php
  test -L /config/rutorrent/conf/users
  test ! -e /config/operator-users/.gitignore
' || fail 'missing proxy policy or operator directory symlink changed'
logs=$(docker logs "$container" 2>&1)
printf '%s\n' "$logs" | grep -F 'conf/users/.gitignore cannot be compared or seeded: parent is a symlink or not a directory' >/dev/null \
  || fail 'symlinked parent warning missing'
if printf '%s\n' "$logs" | grep -F 'seeded missing conf/xmlrpc_proxy.php' >/dev/null; then
  fail 'proxy policy was unexpectedly persisted'
fi

docker exec "$container" sh -eu -c '
  rm /config/rutorrent/conf/plugins.ini
  grep -Fq /usr/bin/php85 /config/rutorrent/conf/config.php
  sed -i "s@/usr/bin/php85@/usr/bin/php84@g" /config/rutorrent/conf/config.php
  mv /config/rutorrent/conf /config/operator-conf
  ln -s /config/operator-conf /config/rutorrent/conf
' || fail 'prepare operator-owned conf directory'
docker restart "$container" >/dev/null
wait_for_health || fail 'third restart'
docker exec "$container" sh -eu -c '
  test -L /config/rutorrent/conf
  test ! -e /config/operator-conf/plugins.ini
' || fail 'operator-owned conf directory received an image-seeded file'
docker exec "$container" sh -eu -c '
  grep -Fq /usr/bin/php85 /config/rutorrent/conf/config.php
  ! grep -Fq /usr/bin/php84 /config/rutorrent/conf/config.php
' || fail 'required update-config migration did not run through root conf symlink'
docker logs "$container" 2>&1 | grep -F 'conf is an operator symlink; skipping image comparison and seeding (update-config still runs)' >/dev/null \
  || fail 'operator conf symlink warning missing'

printf 'ok - image conf seeding preserves overrides and symlinks; update-config still migrates\n'
