#!/bin/sh
set -eu
if [ "$#" -ne 1 ]; then
  printf "Usage: %s IMAGE\n" "$0" >&2
  exit 64
fi
image=$1
if [ -n "${STARTUP_SOURCE:-}" ]; then
  set -- -v "$STARTUP_SOURCE:/usr/local/bin/startup:ro"
else
  set --
fi
# Keep the optional startup bind argument in the function's positional parameters.
run_case() {
  check_perm=$1
  script=$2
  shift 2
  docker run --rm --network none --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
    "$@" --entrypoint sh -e CHECK_PERM_DATA="$check_perm" "$image" -ec "$script"
}
fail() { printf 'not ok - stop-intent bootstrap: %s\n' "$1" >&2; exit 1; }
for check_perm in true false; do
  run_case "$check_perm" '
    test "$(stat -c "%u:%g:%a" /data)" = 0:0:755
    sh /usr/local/bin/startup true > /tmp/startup.log 2>&1
    test ! -e /data/.session/.rutorrent-stop-intents
    test "$(stat -c "%u:%a" /data/.session)" = 991:755
  ' "$@" || fail "fresh private profile ($check_perm)"
  run_case "$check_perm" '
    # Keep the unrelated /config first-use guard green while testing /data umask.
    mkdir -m 0755 /config/rutorrent
    umask 0002
    sh /usr/local/bin/startup true > /tmp/startup.log 2>&1
    test "$(stat -c "%u:%a" /data/.session)" = 991:755
  ' "$@" || fail "fresh profile under group-writable umask ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session
    chown 1001:1001 /data/.session
    chmod 755 /data/.session
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: /data/.session is writable or foreign-owned" /tmp/startup.log
    test "$(stat -c "%u:%a" /data/.session)" = 1001:755
  ' "$@" || fail "old UID session without journal ($check_perm)"
  run_case "$check_perm" '
    chmod 775 /data
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: /data is writable or foreign-owned" /tmp/startup.log
  ' "$@" || fail "group-writable data parent ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    printf "0000000000000001\n" > "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    printf "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb\n" > "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim"
    chown 991:991 "$dir" "$dir"/*
    chmod 700 "$dir"; chmod 600 "$dir"/*
    sh /usr/local/bin/startup true > /tmp/startup.log 2>&1
    test "$(stat -c "%u:%a" "$dir")" = 991:700
    test "$(stat -c "%u:%a" "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")" = 991:600
    test "$(stat -c "%u:%a" "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim")" = 991:600
    test "$(cat "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")" = 0000000000000001
  ' "$@" || fail "existing service-owned journal and claim ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    printf "0000000000000001\n" > "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    chmod 700 "$dir"; chmod 600 "$dir"/*
    sh /usr/local/bin/startup true > /tmp/startup.log 2>&1
    test "$(stat -c "%u:%a" "$dir")" = 991:700
    test "$(stat -c "%u:%a" "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")" = 991:600
  ' "$@" || fail "root-provisioned safe journal ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    claim=$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim
    printf "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:bbbbbbbbbbbbbbbb\n" > "$claim"
    chown 1001:1001 "$dir" "$claim"; chmod 700 "$dir"; chmod 600 "$claim"
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: $dir has unsafe ownership or mode" /tmp/startup.log
    test "$(stat -c "%u:%a" "$dir")" = 1001:700
    test "$(stat -c "%u:%a" "$claim")" = 1001:600
  ' "$@" || fail "foreign preplanted directory ($check_perm)"
  for kind in journal claim; do
    run_case "$check_perm" '
      mkdir -p /data/.session/.rutorrent-stop-intents
      dir=/data/.session/.rutorrent-stop-intents
      file=$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA
      if [ "$STOP_KIND" = claim ]; then file=$file.claim; fi
      printf "0000000000000001\n" > "$file"
      chown 991:991 "$dir"; chown 1001:1001 "$file"
      chmod 700 "$dir"; chmod 600 "$file"
      if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
      test "$result" -eq 78
      grep -Fq "Cannot attest stop intent: $file has unsafe ownership or mode" /tmp/startup.log
      test "$(stat -c "%u:%a" "$file")" = 1001:600
    ' "$@" -e STOP_KIND="$kind" || fail "foreign $kind ($check_perm)"
  done
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    chown 991:991 "$dir"; chmod 777 "$dir"
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: $dir has unsafe ownership or mode" /tmp/startup.log
  ' "$@" || fail "writable journal directory ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    file=$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim
    : > "$file"; chown 991:991 "$dir" "$file"
    chmod 700 "$dir"; chmod 666 "$file"
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: $file has unsafe ownership or mode" /tmp/startup.log
  ' "$@" || fail "writable claim ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session
    ln -s /tmp/missing /data/.session/.rutorrent-stop-intents
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: /data/.session/.rutorrent-stop-intents is not a directory" /tmp/startup.log
  ' "$@" || fail "symlink journal directory ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session/.rutorrent-stop-intents
    dir=/data/.session/.rutorrent-stop-intents
    chown 991:991 "$dir"; chmod 700 "$dir"
    ln -s /tmp/missing "$dir/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim"
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "is not a regular file" /tmp/startup.log
  ' "$@" || fail "symlink claim ($check_perm)"
  run_case "$check_perm" '
    mkdir -p /data/.session
    chmod 777 /data/.session
    if sh /usr/local/bin/startup true > /tmp/startup.log 2>&1; then exit 1; else result=$?; fi
    test "$result" -eq 78
    grep -Fq "Cannot attest stop intent: /data/.session is writable or foreign-owned" /tmp/startup.log
  ' "$@" || fail "writable session parent ($check_perm)"
done
printf 'ok - stop-intent pre-chown bootstrap trust (both CHECK_PERM_DATA modes)\n'
