#!/bin/sh
# Check startup admission only; no daemon, network or operator payload runs.
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 3 ] || { echo 'Usage: native-worker-startup-guard.sh IMAGE [ACTOR_HELPER_BINARY [OWNERSHIP_HELPER_BINARY]]' >&2; exit 64; }
image=$1
helper=${2:-}
ownership_helper=${3:-}
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
count=0
for scenario in valid-root earlier-php-wrapper full-php-wrapper valid-service-actors named-group php-foreign-owner php-service-owner php-no-service-execute php-write-mode php-symlink php-hardlink php-setid app-parent-write update-write dispatch-symlink missing-update marker-missing-dispatch marker-empty-app marker-symlink marker-fifo marker-write marker-foreign-owner missing-checker override-php override-php-empty override-app override-app-empty old-published custom-delete custom-unsafe-replacement; do
  count=$((count+1))
  set -- docker run --rm --pull=never --network none --name "native-actor-admission-${$}-${count}" --entrypoint /bin/sh \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/startup,dst=/usr/local/bin/startup,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/lib/rutorrent,dst=/usr/local/lib/rutorrent,readonly" \
    --mount "type=bind,src=$repo/rootfs/etc/nginx,dst=/candidate-nginx,readonly" \
    --mount "type=bind,src=$repo/tests/smoke/fixtures/native-worker-startup-guard,dst=/actor-guard-fixtures,readonly"
  for source in "$repo"/rootfs/usr/local/libexec/*; do
    [ -f "$source" ] || continue
    case "$source" in *.c) continue ;; esac
    set -- "$@" --mount "type=bind,src=$source,dst=/usr/local/libexec/${source##*/},readonly"
  done
  if [ -n "$helper" ]; then set -- "$@" --mount "type=bind,src=$helper,dst=/actor-helper,readonly"; fi
  if [ -n "$ownership_helper" ]; then set -- "$@" --mount "type=bind,src=$ownership_helper,dst=/usr/local/bin/rutorrent-own-tree,readonly"; fi
  "$@" "$image" /actor-guard-fixtures/run.sh "$scenario"
done
printf 'PASS native actor startup admission cases: %s\n' "$count"
