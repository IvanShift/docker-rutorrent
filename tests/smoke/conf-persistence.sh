#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"

image=$1
suffix="conf-$$-$(date +%s)"
container="rutorrent-${suffix}"
volume="rutorrent-${suffix}"

cleanup() {
  smoke_remove_run_containers || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}

smoke_install_traps

smoke_check_image "$image"
php_binary=$(smoke_run --rm --network none --entrypoint /bin/sh "$image" -eu -c 'for candidate in /usr/bin/php[0-9][0-9]; do if [ -x "$candidate" ]; then basename "$candidate"; exit 0; fi; done; exit 1')
php_suffix=${php_binary#php}
previous_php="php$((php_suffix - 1))"
docker volume create "$volume" >/dev/null
smoke_run -d --network none --name "$container" -v "$volume:/config" "$image" >/dev/null
wait_for_health "$container" || fail 'first boot'
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

before_restart=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'restart'
docker exec "$container" test -f /config/rutorrent/conf/plugins.ini \
  || fail 'missing plugins.ini was not seeded'
docker exec "$container" sh -eu -c '
  grep -Fqx "// proxy override" /config/rutorrent/conf/xmlrpc_proxy.php
  grep -Fqx "// operator marker" /config/rutorrent/conf/config.php
  test -L /config/rutorrent/conf/users/.gitignore
  test "$(readlink /config/rutorrent/conf/users/.gitignore)" = /config/operator-missing
  test ! -e /config/operator-missing
' || fail 'operator files or symlinks were not preserved'
logs=$(docker logs --since "$before_restart" "$container" 2>&1)
printf '%s\n' "$logs" | grep -F 'Seeded missing conf/plugins.ini' >/dev/null \
  || fail 'missing-file seed not logged'
docker exec "$container" sh -eu -c '
  rm /config/rutorrent/conf/xmlrpc_proxy.php
  rm /config/rutorrent/conf/users/.gitignore
  rmdir /config/rutorrent/conf/users
  mkdir /config/operator-users
  ln -s /config/operator-users /config/rutorrent/conf/users
' || fail 'prepare missing policy and symlinked directory'
docker restart "$container" >/dev/null
smoke_expect_exit "$container" 78 '/config/rutorrent/conf/users is not a directory'
# Refusal must preserve the operator directory and its contents.
smoke_run --rm --network none --entrypoint /bin/sh -v "$volume:/config" "$image" -eu -c '
  test -L /config/rutorrent/conf/users
  test ! -e /config/operator-users/.gitignore
  rm /config/rutorrent/conf/users
  mkdir /config/rutorrent/conf/users
'
docker start "$container" >/dev/null
wait_for_health "$container" || fail 'recover after refusing symlinked parent'
docker exec "$container" sh -eu -c '
  test ! -e /config/rutorrent/conf/xmlrpc_proxy.php
  test ! -L /config/rutorrent/conf/xmlrpc_proxy.php
  grep -Fq "/usr/bin/$1" /config/rutorrent/conf/config.php
  sed -i "s@/usr/bin/$1@/usr/bin/$2@g" /config/rutorrent/conf/config.php
' sh "$php_binary" "$previous_php" || fail 'prepare preceding PHP version migration'
docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'PHP path migration restart'
docker exec "$container" sh -eu -c '
  grep -Fq "/usr/bin/$1" /config/rutorrent/conf/config.php
  if grep -Fq "/usr/bin/$2" /config/rutorrent/conf/config.php; then exit 1; fi
' sh "$php_binary" "$previous_php" || fail 'required PHP path migration'

# An entire operator conf symlink must be refused without touching its target.
docker exec "$container" sh -eu -c '
  mv /config/rutorrent/conf /config/operator-conf
  ln -s /config/operator-conf /config/rutorrent/conf
  sha256sum /config/operator-conf/config.php > /config/operator-config.sha256
'
docker restart "$container" >/dev/null
smoke_expect_exit "$container" 78 'operator symlink'
smoke_run --rm --network none --entrypoint /bin/sh -v "$volume:/config" "$image" -eu -c '
  sha256sum -c /config/operator-config.sha256
  test -L /config/rutorrent/conf
  rm /config/rutorrent/conf
  mv /config/operator-conf /config/rutorrent/conf
'
docker start "$container" >/dev/null
wait_for_health "$container" || fail 'recover after refusing operator conf symlink'

# Recover each completely removed externalized target on the same container.
for missing_path in /config/rutorrent/conf /config/rutorrent/share /config/rtorrent/.rtorrent.rc; do
  docker exec "$container" rm -rf -- "$missing_path" || fail "remove $missing_path"
  docker restart "$container" >/dev/null
  wait_for_health "$container" || fail "restart after deleting $missing_path"
  docker exec "$container" sh -eu -c '
    test -e "$1"
    test ! -L "$1"
    test -f /config/rutorrent/conf/config.php
    test -f /config/rutorrent/conf/plugins.ini
    test -d /config/rutorrent/share/settings
    test -f /config/rtorrent/.rtorrent.rc
  ' sh "$missing_path" || fail "restored target is missing or a self-reference: $missing_path"
  [ "$(http_status "$container" http://localhost/healthz-plugins --unix-socket /run/nginx/plugin-health.sock)" = 200 ] \
    || fail "PHP plugin initialization after restoring $missing_path"
done

# A required missing subdirectory must be recreated rather than relying on first boot.
docker exec "$container" rm -rf /config/rutorrent/conf/users
before_restart=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'restart after deleting conf/users'
docker exec "$container" test -d /config/rutorrent/conf/users || fail 'required subdirectory was not recreated'
docker exec "$container" test ! -L /config/rutorrent/conf/users || fail 'required subdirectory became a symlink'
logs=$(docker logs --since "$before_restart" "$container" 2>&1) || fail 'read latest restart logs'
printf '%s\n' "$logs" | grep -F 'Seeded missing conf/users/.gitignore' >/dev/null \
  || fail 'required subdirectory reseeding was not logged on this restart'

printf 'ok - conf seeding preserves overrides, refuses directory symlinks, migrates PHP, and restores missing targets\n'
