#!/bin/sh

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

# A failing check fails the suite explicitly, not only through errexit, so it
# cannot pass where errexit does not apply.
run_check() {
  status=0
  "$@" || status=$?
  [ "$status" -eq 0 ] || fail "shell check exited with status $status: $*"
}

checked=0
non_executable=0
while IFS= read -r file; do
  first_line=$(sed -n '1p' "$file")
  case "$first_line" in
    '#!/bin/sh'|'#!/usr/bin/env sh')
      sh -n "$file" || fail "shell syntax: ${file#"$repo_root"/}"
      mode=$(stat -c '%a' "$file")
      [ -x "$file" ] || fail "non-executable shell entry point ${file#"$repo_root"/}: mode $mode"
      checked=$((checked + 1))
    ;;
    *)
      case "$file" in
        "$repo_root/tests/smoke/lib.sh")
          sh -n "$file" || fail 'shared smoke library shell syntax'
          [ ! -x "$file" ] || fail 'sourced smoke library must not be executable'
          checked=$((checked + 1))
        ;;
      esac
      case "$file" in
        "$repo_root/rootfs/etc/"*|"$repo_root/rootfs/filebot/"*)
          [ ! -x "$file" ] || fail "non-shell configuration is executable: ${file#"$repo_root"/}"
          non_executable=$((non_executable + 1))
        ;;
      esac
    ;;
  esac
done <<EOF
$(find "$repo_root/rootfs" "$repo_root/tests" -type f -print | sort)
EOF

[ "$checked" -gt 0 ] || fail 'no POSIX shell entry points found'

printf 'ok - %s POSIX shell entry points and shared smoke library have the expected executable bit; %s configuration files are non-executable\n' \
  "$checked" "$non_executable"

run_check "$repo_root/tests/shell/smoke-lib.sh"

run_check "$repo_root/tests/shell/plugin-schedules.sh"

run_check "$repo_root/tests/shell/service-finish.sh"

run_check "$repo_root/tests/shell/logrotate.sh"

run_check "$repo_root/tests/shell/logrotate-rearm.sh"

run_check python3 "$repo_root/tests/shell/service-runtime-test.py"

run_check python3 "$repo_root/tests/shell/service-version-gates.py"

run_check "$repo_root/tests/shell/ownership-remaining.sh"
