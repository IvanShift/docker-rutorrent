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

# Use the exact fork source embedded in this image to decide whether the
# native helper is required; older pinned fork commits do not call it.
docker run --rm "$@" --network none --read-only --tmpfs /tmp:rw,nosuid,size=16m \
  --user torrent --entrypoint /bin/sh "$image" -eu -c '
    fail() {
      printf "not ok - erasedata rename: %s\n" "$1" >&2
      exit 1
    }
    source_file=/rutorrent/app/plugins/erasedata/filesystem.php
    if grep -Fq "rutorrent-erasedata-rename-noreplace" "$source_file"; then
      helper=/usr/local/bin/rutorrent-erasedata-rename-noreplace
      [ -x "$helper" ] || fail "helper missing"
      test_dir=$(mktemp -d /tmp/rutorrent-rename.XXXXXX)

      printf "captured\n" > "$test_dir/captured"
      "$helper" "$test_dir/captured" "$test_dir/restored" || fail "restore failed"
      [ ! -e "$test_dir/captured" ] || fail "source remains after restore"
      [ "$(cat "$test_dir/restored")" = captured ] || fail "restored content changed"

      printf "new capture\n" > "$test_dir/captured"
      printf "new occupant\n" > "$test_dir/restored"
      source_inode=$(stat -c %i "$test_dir/captured")
      destination_inode=$(stat -c %i "$test_dir/restored")
      if "$helper" "$test_dir/captured" "$test_dir/restored"; then
        fail "replaced an occupied name"
      fi
      [ "$(cat "$test_dir/captured")" = "new capture" ] || fail "occupied restore changed source content"
      [ "$(cat "$test_dir/restored")" = "new occupant" ] || fail "occupied restore changed destination content"
      [ "$(stat -c %i "$test_dir/captured")" = "$source_inode" ] || fail "occupied restore replaced source inode"
      [ "$(stat -c %i "$test_dir/restored")" = "$destination_inode" ] || fail "occupied restore replaced destination inode"
      printf "ok - erasedata rename restores and preserves occupied destination as torrent\n"
    else
      grep_status=$?
      [ "$grep_status" -eq 1 ] || exit "$grep_status"
      printf "ok - pinned fork does not require erasedata rename helper\n"
    fi
  '
