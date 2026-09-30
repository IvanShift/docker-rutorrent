#!/bin/sh

set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  printf 'Usage: %s IMAGE [PLATFORM]\n' "$0" >&2
  exit 64
fi

image=$1
if [ "$#" -eq 2 ]; then
  set -- --platform "$2"
else
  set --
fi

# The fork's test comes from the image's exact RUTORRENT_REF checkout. An old
# checkout has no pidfd helper or corresponding test, so it has nothing to run.
docker run --rm "$@" --network none --read-only --tmpfs /tmp:rw,nosuid,size=16m \
  --user torrent --entrypoint /bin/sh "$image" -eu -c '
    task_source=/rutorrent/app/plugins/_task/task.php
    if grep -Fq "rutorrent-task-kill-pidfd" "$task_source"; then
      php85 /rutorrent/app/tests/plugins/_task/TaskPidfdImageSmoke.php
    else
      grep_status=$?
      [ "$grep_status" -eq 1 ] || exit "$grep_status"
      printf "ok - pinned fork does not require pidfd task killer\n"
    fi
  '
