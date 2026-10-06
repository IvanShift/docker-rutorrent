#!/bin/sh
# Candidate startup/helper binds are documented in bootstrap-lib.sh.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
. "$smoke_dir/bootstrap-lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
volume="smoke-native-erasedata-$$-$(date +%s)"
cleanup() { docker volume rm "$volume" >/dev/null 2>&1 || true; }
smoke_install_traps
run_case() (
  case_name=$1
  case_body=$2
  shift 2
  smoke_startup_case "$image" "$case_body" "$@" || fail "erasedata queue bootstrap: $case_name"
)
docker volume create "$volume" >/dev/null
for check_perm in true false; do
  for boot in 1 2; do
    run_case "persisted boot $boot ($check_perm)" '
      startup
      test ! -L /config/rutorrent/.erasedata-first-use
      test "$(stat -c %u:%g:%a:%h:%s /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0
      test "$(stat -c %u:%g:%a /config/rutorrent/erasedata-jobs)" = 991:991:700
      # Ask the exact application embedded in the image to admit its own queue.
      cd /rutorrent/app/php
      su-exec 991:991 php85 /tests/smoke/queue-admission.php
    ' -v "$volume:/config" -e CHECK_PERM_DATA="$check_perm"
  done
done
for check_perm in true false; do
  BOOTSTRAP_DATA_VOLUME=$volume run_case "shared /data and /config volume ($check_perm)" '
    startup
    test "$(stat -c %u:%g:%a:%h:%s /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0
    test "$(stat -c %u:%g:%a:%h:%s /data/rutorrent/.erasedata-first-use)" = 0:0:400:1:0
    cd /rutorrent/app/php
    su-exec 991:991 php85 /tests/smoke/queue-admission.php
  ' -v "$volume:/config" -e CHECK_PERM_DATA="$check_perm"
done
run_case 'old profile retains pending obligations and visible operator warning' '
  mkdir -p /config/rutorrent/share/settings/erasedata
  printf "legacy pending intent\n" > /config/rutorrent/share/settings/erasedata/old.list
  chmod 0700 /config/rutorrent/share/settings/erasedata
  chmod 0600 /config/rutorrent/share/settings/erasedata/old.list
  chown -R 991:991 /config/rutorrent
  before=$(sha256sum /config/rutorrent/share/settings/erasedata/old.list)
  startup
  test "$(sha256sum /config/rutorrent/share/settings/erasedata/old.list)" = "$before"
  test ! -e /config/rutorrent/erasedata-jobs
  test ! -e /config/rutorrent/.erasedata-first-use
  grep -F "attest-erasedata-queue" "$startup_log" >/dev/null
' -e CHECK_PERM_DATA=false
for check_perm in true false; do
  for case_name in config-owner config-mode root-owner root-mode root-file root-link sibling-owner sibling-mode sibling-file sibling-link missing-witness missing-sibling witness-owner witness-mode witness-filetype witness-link witness-hardlink witness-content; do
    run_case "$case_name ($check_perm)" '
      root=/config/rutorrent
      jobs=$root/erasedata-jobs
      witness=$root/.erasedata-first-use
      mkdir -m 0755 "$root"
      mkdir -m 0700 "$jobs"
      : > "$witness"; chmod 0400 "$witness"
      expected=""
      case "$CASE_NAME" in
        config-owner) chown 1001:1001 /config; expected="/config is writable or foreign-owned" ;;
        config-mode) chmod 0775 /config; expected="/config has unsafe ownership or mode" ;;
        root-owner) chown 1001:1001 "$root"; expected="$root is writable or foreign-owned" ;;
        root-mode) chmod 0775 "$root"; expected="$root has unsafe ownership or mode" ;;
        root-file) rm -rf "$root"; : > "$root"; expected="$root is not a directory" ;;
        root-link) mv "$root" /tmp/queue-root-target; ln -s /tmp/queue-root-target "$root"; expected="$root is not a directory" ;;
        sibling-owner) chown 1001:1001 "$jobs"; expected="$jobs is writable or foreign-owned" ;;
        sibling-mode) chmod 0750 "$jobs"; expected="$jobs has unsafe ownership or mode" ;;
        sibling-file) rmdir "$jobs"; : > "$jobs"; chmod 0700 "$jobs"; expected="$jobs is not a directory" ;;
        sibling-link) rmdir "$jobs"; mkdir -m 0700 /tmp/queue-jobs-target; ln -s /tmp/queue-jobs-target "$jobs"; expected="$jobs is not a directory" ;;
        missing-witness) rm "$witness"; expected="pre-existing sibling lacks first-use witness" ;;
        missing-sibling) rmdir "$jobs"; expected="protected queue is missing" ;;
        witness-owner) chown 991:991 "$witness"; expected="$witness has unsafe ownership or mode" ;;
        witness-mode) chmod 0440 "$witness"; expected="$witness has unsafe ownership or mode" ;;
        witness-filetype) rm "$witness"; mkdir -m 0400 "$witness"; expected="$witness is not a regular file" ;;
        witness-link) mv "$witness" /tmp/queue-witness-target; ln -s /tmp/queue-witness-target "$witness"; expected="$witness is not a regular file" ;;
        witness-hardlink) ln "$witness" /config/queue-witness-link; expected="$witness has unsafe ownership or mode" ;;
        witness-content) chmod 0600 "$witness"; printf x > "$witness"; chmod 0400 "$witness"; expected="$witness has unsafe ownership or mode" ;;
      esac
      before=$(stat -c %u:%g:%a:%h:%s /config "$root" "$jobs" "$witness" 2>/dev/null || true)
      refused "Cannot attest erasedata queue root: $expected"
      test "$(stat -c %u:%g:%a:%h:%s /config "$root" "$jobs" "$witness" 2>/dev/null || true)" = "$before"
    ' -e CHECK_PERM_DATA="$check_perm" -e CASE_NAME="$case_name"
  done
done
printf 'ok - erasedata queue application admission, persisted witness and all metadata refusal branches\n'
