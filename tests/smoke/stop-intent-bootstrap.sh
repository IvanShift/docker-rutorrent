#!/bin/sh
# Candidate startup/helper/template binds are documented in bootstrap-lib.sh.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
. "$smoke_dir/bootstrap-lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
if smoke_run --rm --network none --entrypoint sh "$image" -c 'test -f /etc/rtorrent-legacy'; then
  printf 'ok - stop-intent bootstrap # SKIP explicit legacy daemon contract\n'
  exit 0
fi
run_case() (
  case_perm=$1
  case_name=$2
  case_body=$3
  shift 3
  smoke_startup_case "$image" "$case_body" -e CHECK_PERM_DATA="$case_perm" "$@" \
    || fail "stop-intent bootstrap: $case_name ($case_perm)"
)
for check_perm in true false; do
  run_case "$check_perm" 'fresh default and umask 0002' '
    umask 0002
    startup
    test "$(stat -c %u:%g:%a /data/.session)" = 991:991:755
    test ! -e /data/.session/.rutorrent-stop-intents
    strings /usr/local/bin/rtorrent | grep -F .rutorrent-stop-intents >/dev/null
  '
  for ids in 991:991 1000:1000 1000:100; do
    run_case "$check_perm" "trusted existing journal and restart $ids" '
      dir=/data/.session/.rutorrent-stop-intents
      hash=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
      mkdir -p "$dir"
      chmod 0700 "$dir"
      printf "0000000000000001\n" > "$dir/$hash"
      printf "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb\n" > "$dir/$hash.claim"
      chmod 0600 "$dir"/*
      before=$(sha256sum "$dir"/*)
      startup
      test "$(stat -c %u:%g:%a "$dir")" = "$UID:$GID:700"
      for file in "$dir"/*; do test "$(stat -c %u:%g:%a "$file")" = "$UID:$GID:600"; done
      test "$(sha256sum "$dir"/*)" = "$before"
      # A second start must accept the durable service-owned data, even when
      # CHECK_PERM_DATA=false and the requested numeric IDs are nondefault.
      startup
      test "$(sha256sum "$dir"/*)" = "$before"
      test "$(stat -c %u:%g:%a "$dir")" = "$UID:$GID:700"
    ' -e UID="${ids%:*}" -e GID="${ids#*:}"
    run_case "$check_perm" "trusted setgid session and private journal special bits, restart $ids" '
      stop=/data/.session/.rutorrent-stop-intents
      retrackers=/data/.session/.retrackers-migration
      jobs=/data/.session/.autotools-file-jobs
      hash=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
      mkdir -p "$stop" "$retrackers" "$jobs"
      chmod 2755 /data/.session
      chmod 2700 "$stop"; chmod 1700 "$retrackers"; chmod 3700 "$jobs"
      for dir in "$stop" "$retrackers" "$jobs"; do
        printf "0000000000000001\n" > "$dir/$hash"
        chmod 0600 "$dir/$hash"
      done
      before=$(sha256sum "$stop"/* "$retrackers"/* "$jobs"/*)
      for boot in first repeated; do
        startup
        test "$(stat -c %u:%g:%a /data/.session)" = "$UID:$GID:755"
        test "$(stat -c %u:%g:%a "$stop")" = "$UID:$GID:700"
        test "$(stat -c %u:%g:%a "$retrackers")" = "$UID:$GID:700"
        test "$(stat -c %u:%g:%a "$jobs")" = "$UID:$GID:700"
        for dir in "$stop" "$retrackers" "$jobs"; do
          test "$(stat -c %u:%g:%a:%h "$dir/$hash")" = "$UID:$GID:600:1"
        done
        test "$(sha256sum "$stop"/* "$retrackers"/* "$jobs"/*)" = "$before"
      done
    ' -e UID="${ids%:*}" -e GID="${ids#*:}"
  done
  for case_name in data-owner data-mode session-owner session-mode session-setuid session-file session-link journal-owner journal-mode journal-setuid journal-file journal-link entry-owner entry-mode entry-link entry-hardlink entry-directory hidden-owner; do
    run_case "$check_perm" "$case_name" '
      dir=/data/.session/.rutorrent-stop-intents
      hash=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
      mkdir -p "$dir"
      chmod 0700 "$dir"
      file=$dir/$hash
      printf "0000000000000001\n" > "$file"
      chmod 0600 "$file"
      # Recursive trust diagnostics come from descriptor admission; root type
      # refusals still come from PHP directory creation.
      expected=""
      case "$CASE_NAME" in
        data-owner) chown 1001:1001 /data; expected="Cannot attest ownership /data/.session: unsafe session ancestor" ;;
        data-mode) chmod 0775 /data; expected="Cannot attest ownership /data/.session: unsafe session ancestor" ;;
        session-owner) chown 1001:1001 /data/.session; expected="Cannot attest ownership /data/.session: foreign session owner; repair ownership while stopped" ;;
        session-mode) chmod 0775 /data/.session; expected="Cannot attest ownership /data/.session: unsafe session directory" ;;
        session-setuid) chmod 4755 /data/.session; expected="Cannot attest ownership /data/.session: unsafe session directory" ;;
        session-file) rm -rf /data/.session; : > /data/.session; expected="Cannot attest volume path: /data/.session is not a directory" ;;
        session-link) rm -rf /data/.session; mkdir /tmp/session-target; ln -s /tmp/session-target /data/.session; expected="Cannot attest volume path: /data/.session is not a directory" ;;
        journal-owner) chown 1001:1001 "$dir"; expected="Cannot attest ownership $dir: foreign session owner; repair ownership while stopped" ;;
        journal-mode) chmod 0750 "$dir"; expected="Cannot attest ownership $dir: unsafe private journal directory" ;;
        journal-setuid) chmod 4700 "$dir"; expected="Cannot attest ownership $dir: unsafe private journal directory" ;;
        journal-file) rm -rf "$dir"; : > "$dir"; chmod 0600 "$dir"; expected="Cannot attest ownership $dir: unsafe private journal directory" ;;
        journal-link) rm -rf "$dir"; mkdir -m 0700 /tmp/journal-target; ln -s /tmp/journal-target "$dir"; expected="Cannot attest ownership $dir: unsafe private journal directory" ;;
        entry-owner) chown 1001:1001 "$file"; expected="Cannot attest ownership $file: foreign session owner; repair ownership while stopped" ;;
        entry-mode) chmod 0640 "$file"; expected="Cannot attest ownership $file: private journal record requires mode0600" ;;
        entry-link) rm "$file"; printf "target\n" > /tmp/entry-target; chmod 0600 /tmp/entry-target; ln -s /tmp/entry-target "$file"; expected="Cannot attest ownership $file: session entries must be regular data, without symlinks" ;;
        entry-hardlink) ln "$file" /data/entry-link; expected="Cannot attest ownership $file: private record is not single-linked or an exact historical claim pair" ;;
        entry-directory) rm "$file"; mkdir -m 0700 "$file"; expected="Cannot attest ownership $file: private journal contains a nested directory" ;;
        hidden-owner) file=$dir/.hidden; : > "$file"; chmod 0600 "$file"; chown 1001:1001 "$file"; expected="Cannot attest ownership $file: foreign session owner; repair ownership while stopped" ;;
      esac
      before=$(stat -c %u:%g:%a:%h /data/.session "$dir" 2>/dev/null || true)
      refused "$expected"
      test "$(stat -c %u:%g:%a:%h /data/.session "$dir" 2>/dev/null || true)" = "$before"
    ' -e CASE_NAME="$case_name"
  done
  run_case "$check_perm" 'sticky data ancestor remains accepted' '
    chmod 1777 /data
    startup
    test "$(stat -c %a /data)" = 1777
  '
  run_case "$check_perm" 'safe foreign GID is normalized and hidden regular entries retained' '
    dir=/data/.session/.rutorrent-stop-intents
    mkdir -p "$dir"
    : > "$dir/.hidden"
    chmod 0700 "$dir"; chmod 0600 "$dir/.hidden"
    chown 991:1001 "$dir" "$dir/.hidden"
    startup
    test "$(stat -c %u:%g:%a "$dir")" = 991:991:700
    test "$(stat -c %u:%g:%a "$dir/.hidden")" = 991:991:600
  '
done
# This gate verifies startup attestation and ownership, not daemon torrent state.
# Known native-fork erase/magnet stop-intent behavior is tracked separately; see
# the remediation report. Do not turn a successful bootstrap into a state claim.
printf 'ok - stop-intent attestation, safe setgid/sticky journal modes, setuid refusals, nondefault identities and second startup\n'
