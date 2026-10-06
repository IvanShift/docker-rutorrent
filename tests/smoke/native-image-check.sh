#!/bin/sh
# Runs only in a disposable container; /data and /config use its actual volumes.
set -eu
scenario=$1
fail() { printf 'not ok - native %s: %s\n' "$scenario" "$1" >&2; exit 1; }
manifest=/usr/local/share/rutorrent-native-helpers
if [ -f "$manifest" ]; then
  contract=$(sed -n '1p' "$manifest")
  case "$contract" in
    contract=legacy)
      # An explicit build record permits old helper-free fork revisions. A
      # partial current source tree cannot silently become a legacy contract.
      [ -d /rutorrent/app/plugins ] || fail 'fork plugins are missing'
      if find /rutorrent/app -type f \( -name '*kill-verified.c' -o -name '*supervise.c' -o -name '*rename-noreplace.c' \) | grep . >/dev/null \
        || grep -r -E 'rutorrent-task-(kill-pidfd|supervise)|rutorrent-erasedata-rename-noreplace' /rutorrent/app/plugins >/dev/null; then
        fail 'legacy helper contract contradicts embedded fork source'
      fi
      printf 'ok - native %s # SKIP explicit legacy helper-free fork contract\n' "$scenario"
      exit 0
    ;;
    contract=native) ;;
    *) fail 'unknown native helper build contract' ;;
  esac
fi
if [ -f "$manifest" ]; then
  # Every recorded helper is a build promise, including future additions.
  sed '1d' "$manifest" | while IFS= read -r recorded || [ -n "$recorded" ]; do
    case "$recorded" in ''|*[!a-z0-9-]*|rutorrent-) fail 'invalid helper manifest entry' ;; rutorrent-*) ;; *) fail 'invalid helper manifest entry' ;; esac
    [ -x "/usr/local/bin/$recorded" ] || fail "manifest helper missing: $recorded"
  done
fi
for helper in rutorrent-task-kill-pidfd rutorrent-task-supervise rutorrent-erasedata-rename-noreplace; do
  [ -x "/usr/local/bin/$helper" ] || fail "required helper missing: $helper"
  if [ -f "$manifest" ]; then
    grep -Fx "$helper" "$manifest" >/dev/null || fail "helper absent from build manifest: $helper"
  fi
done
if [ "$(id -u)" -eq 0 ]; then
  if [ "$scenario" = rename ]; then
    RENAME_DATA_ROOT=$(mktemp -d /data/rename-smoke.XXXXXX)
    RENAME_CONFIG_ROOT=$(mktemp -d /config/rename-smoke.XXXXXX)
    export RENAME_DATA_ROOT RENAME_CONFIG_ROOT
    chown torrent:torrent "$RENAME_DATA_ROOT" "$RENAME_CONFIG_ROOT"
  fi
  exec su-exec torrent /bin/sh -eu "$0" "$scenario"
fi
case "$scenario" in
  pidfd)
    [ -f /rutorrent/app/tests/plugins/_task/TaskPidfdImageSmoke.php ] || fail 'required fork task image test is missing'
    exec php85 /rutorrent/app/tests/plugins/_task/TaskPidfdImageSmoke.php
  ;;
  rename)
    helper=/usr/local/bin/rutorrent-erasedata-rename-noreplace
    canonical=/rutorrent/app/tests/plugins/erasedata/rename-noreplace-test.sh
    capabilities=/usr/local/share/rutorrent-native-capabilities
    if [ -f "$capabilities" ] && grep -Fx 'rename-probe=1' "$capabilities" >/dev/null; then
      [ -f "$canonical" ] || fail 'canonical packaged-binary rename test is missing'
      /bin/bash "$canonical" "$helper" || fail 'canonical packaged-binary rename test failed'
    else
      # Published pre-manifest sources compile their canonical test themselves.
      # The filesystem probes below still exercise the installed binary.
      printf 'ok - canonical packaged-binary rename test # SKIP pre-manifest source contract\n'
    fi
    expect_status() {
      expected=$1
      shift
      result=0
      "$@" || result=$?
      [ "$result" -eq "$expected" ] || fail "rename exit: expected $expected, got $result"
    }
    # Exercise the backing filesystems operators use, in addition to tmpfs.
    tmp_root=$(mktemp -d /tmp/rename-smoke.XXXXXX)
    for test_dir in "$tmp_root" "$RENAME_DATA_ROOT" "$RENAME_CONFIG_ROOT"; do
      mkdir "$test_dir/source-dir"
      printf 'child\n' > "$test_dir/source-dir/child"
      original=$(stat -c %d:%i "$test_dir/source-dir")
      expect_status 0 "$helper" "$test_dir/source-dir" "$test_dir/restored-dir"
      [ ! -e "$test_dir/source-dir" ] || fail 'source directory remains'
      [ "$(stat -c %d:%i "$test_dir/restored-dir")" = "$original" ] || fail 'directory inode changed'
      [ "$(cat "$test_dir/restored-dir/child")" = child ] || fail 'directory content changed'
      for kind in file directory; do
        source=$test_dir/source-$kind
        destination=$test_dir/occupied-$kind
        if [ "$kind" = directory ]; then
          mkdir "$source" "$destination"
          printf 'capture\n' > "$source/content"
          printf 'occupant\n' > "$destination/content"
        else
          printf 'capture\n' > "$source"
          printf 'occupant\n' > "$destination"
        fi
        before=$(stat -c %d:%i "$source" "$destination")
        expect_status 1 "$helper" "$source" "$destination"
        [ "$(stat -c %d:%i "$source" "$destination")" = "$before" ] || fail 'occupied destination changed inode'
        if [ "$kind" = directory ]; then source=$source/content; destination=$destination/content; fi
        [ "$(cat "$source")" = capture ] || fail 'occupied restore changed source'
        [ "$(cat "$destination")" = occupant ] || fail 'occupied restore changed destination'
      done
      printf 'file\n' > "$test_dir/source"
      original=$(stat -c %d:%i "$test_dir/source")
      expect_status 0 "$helper" "$test_dir/source" "$test_dir/restored"
      [ ! -e "$test_dir/source" ] || fail 'source file remains'
      [ "$(stat -c %d:%i "$test_dir/restored")" = "$original" ] || fail 'file inode changed'
      ln -s missing-target "$test_dir/source-link"
      expect_status 0 "$helper" "$test_dir/source-link" "$test_dir/restored-link"
      [ ! -L "$test_dir/source-link" ] || fail 'source link remains'
      [ "$(readlink "$test_dir/restored-link")" = missing-target ] || fail 'link target changed'
      expect_status 2 "$helper" relative-source "$test_dir/invalid"
      expect_status 2 "$helper" "$test_dir/restored" "$test_dir/restored"
      [ "$(cat "$test_dir/restored")" = file ] || fail 'invalid call changed file'
    done
    printf 'ok - native rename files, directories, links, occupied names and argument status on /tmp /data /config\n'
  ;;
  *) fail 'unknown scenario' ;;
esac
