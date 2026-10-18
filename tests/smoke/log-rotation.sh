#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || { echo "Usage: $0 IMAGE [LOGROTATE_RUN_SOURCE]" >&2; exit 64; }
image=$1
source=${2:-}
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$image"
# Fixture containers use --rm; a trap after a failed scenario removes a remainder.
cleanup() { smoke_remove_run_containers force 2>/dev/null || true; }
smoke_install_traps
for scenario in ${LOGROTATE_SCENARIOS:-growth nondefault other-logs reopen-retry symlink hardlink foreign fifo parent parent-owner archive-symlink archive-owner archive-leaf repair}; do
  set -- --rm --name "smoke-log-rotation-$$-$scenario" --network none \
    --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
    --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    --mount "type=bind,src=$smoke_dir/fixtures/log-rotation,dst=/tests/log-rotation,readonly" --entrypoint /bin/sh
  if [ -n "$source" ]; then set -- "$@" --mount "type=bind,src=$source,dst=/etc/s6.d/logrotate/run,readonly"; fi
  owned_id=$(smoke_create "$@" "$image" /tests/log-rotation/run.sh "$scenario")
  printf 'fixture-container=%s scenario=%s\n' "$owned_id" "$scenario"
  docker start --attach "$owned_id"
done
