#!/bin/sh

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

checked=0
non_executable=0
while IFS= read -r file; do
  first_line=$(sed -n '1p' "$file")
  case "$first_line" in
    '#!/bin/sh'|'#!/usr/bin/env sh')
      sh -n "$file" || fail "shell syntax: ${file#"$repo_root"/}"
      mode=$(stat -c '%a' "$file")
      [ "$mode" = 755 ] || fail "executable mode for ${file#"$repo_root"/}: expected 755, got $mode"
      checked=$((checked + 1))
    ;;
    *)
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

printf 'ok - %s POSIX shell entry points use mode 755; %s configuration files are non-executable\n' \
  "$checked" "$non_executable"
