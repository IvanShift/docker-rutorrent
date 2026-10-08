#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { echo "Usage: $0 IMAGE [LOGROTATE_RUN_SOURCE]" >&2; exit 64; }
image=$1
source=${2:-}
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$image"
owned_id=''
owner="log-rotation-$$"
cleanup() {
  [ -n "$owned_id" ] || return 0
  current=$(docker inspect "$owned_id" --format '{{.Id}} {{index .Config.Labels "smoke.log-rotation.owner"}}' 2>/dev/null || true)
  [ "$current" = "$owned_id $owner" ] || return 0
  docker rm -f -v "$owned_id" >/dev/null 2>&1 || true
}
smoke_install_traps
for scenario in ${LOGROTATE_SCENARIOS:-growth nondefault other-logs symlink hardlink foreign fifo parent parent-owner archive-symlink archive-owner archive-leaf repair}; do
  set -- --rm --pull=never --name "smoke-$owner-$scenario" --label "smoke.log-rotation.owner=$owner" --network none \
    --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
    --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    --mount "type=bind,src=$smoke_dir/fixtures/log-rotation,dst=/tests/log-rotation,readonly" --entrypoint /bin/sh
  if [ -n "$source" ]; then set -- "$@" --mount "type=bind,src=$source,dst=/etc/s6.d/logrotate/run,readonly"; fi
  owned_id=$(docker create "$@" "$image" /tests/log-rotation/run.sh "$scenario")
  printf 'fixture-container=%s scenario=%s\n' "$owned_id" "$scenario"
  docker start --attach "$owned_id"
  owned_id=''
done
