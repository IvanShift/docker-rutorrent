#!/bin/sh

set -eu

fixtures=/tests/update-config/fixtures
workspace=$(mktemp -d)

cleanup() {
  rm -rf "$workspace"
}

trap cleanup EXIT HUP INT TERM

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

run_case() {
  case_name=$1
  actual="$workspace/${case_name}.rc"
  missing_php="$workspace/${case_name}.php"
  expected="$fixtures/${case_name}.expected.rc"

  cp "$fixtures/${case_name}.input.rc" "$actual"
  chown 991:991 "$actual"
  chmod 0640 "$actual"

  /usr/local/bin/update-config "$actual" "$missing_php" || fail "$case_name migration"
  cmp -s "$expected" "$actual" || fail "$case_name content"
  [ "$(stat -c '%u:%g:%a' "$actual")" = 991:991:640 ] \
    || fail "$case_name first migration ownership and mode"

  first_hash=$(sha256sum "$actual" | awk '{print $1}')
  /usr/local/bin/update-config "$actual" "$missing_php" || fail "$case_name second migration"
  second_hash=$(sha256sum "$actual" | awk '{print $1}')
  [ "$first_hash" = "$second_hash" ] || fail "$case_name idempotency"
  [ "$(stat -c '%u:%g:%a' "$actual")" = 991:991:640 ] \
    || fail "$case_name second migration ownership and mode"

  if [ "$case_name" = socket-allocation ]; then
    [ "$(grep -Ec '^[[:space:]]*system\.sockets\.adjust_alloc[[:space:]]*=' "$actual")" = 1 ] \
      || fail "$case_name adjust allocation count"
  fi

  printf 'ok - %s\n' "$case_name"
}

run_missing_optional_files() {
  output="$workspace/missing-optional-files.output"
  missing_rc="$workspace/missing-optional-files.rc"
  missing_php="$workspace/missing-optional-files.php"

  if ! /usr/local/bin/update-config "$missing_rc" "$missing_php" >"$output" 2>&1; then
    fail 'missing optional files exit status'
  fi
  grep -Fq 'Skip rTorrent configuration' "$output" || fail 'missing rTorrent diagnostic'
  grep -Fq 'Skip ruTorrent PHP configuration' "$output" || fail 'missing ruTorrent PHP diagnostic'

  printf 'ok - missing optional files\n'
}

run_write_failure() {
  readonly_dir="$workspace/write-failure"
  actual="$readonly_dir/config.rc"
  missing_php="$workspace/write-failure.php"
  stderr="$workspace/write-failure.stderr"

  mkdir "$readonly_dir"
  cp "$fixtures/bare-aliases.input.rc" "$actual"
  chmod 0644 "$actual"
  chmod 0755 "$workspace"
  before_hash=$(sha256sum "$actual" | awk '{print $1}')
  chmod 0555 "$readonly_dir"

  if su-exec 65534:65534 /usr/local/bin/update-config "$actual" "$missing_php" 2>"$stderr"; then
    fail 'write failure exit status'
  fi
  after_hash=$(sha256sum "$actual" | awk '{print $1}')
  [ "$before_hash" = "$after_hash" ] || fail 'write failure atomicity'
  grep -Fq 'Unable to create temporary file' "$stderr" || fail 'write failure diagnostic'

  printf 'ok - write failure\n'
}

run_grep_failure() {
  actual="$workspace/grep-failure.rc"
  missing_php="$workspace/grep-failure.php"
  shadow_bin="$workspace/grep-failure-bin"

  cp "$fixtures/socket-allocation.input.rc" "$actual"
  before_hash=$(sha256sum "$actual" | awk '{print $1}')
  mkdir "$shadow_bin"
  printf '#!/bin/sh\nexit 2\n' > "$shadow_bin/grep"
  chmod 0755 "$shadow_bin/grep"

  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$missing_php"; then
    fail 'grep failure exit status'
  fi
  after_hash=$(sha256sum "$actual" | awk '{print $1}')
  [ "$before_hash" = "$after_hash" ] || fail 'grep failure atomicity'
  [ -z "$(find "$workspace" -name 'grep-failure.rc.tmp.*' -print -quit)" ] \
    || fail 'grep failure cleanup'

  printf 'ok - grep failure\n'
}

run_cmp_failure() {
  actual="$workspace/cmp-failure.rc"
  missing_php="$workspace/cmp-failure.php"
  shadow_bin="$workspace/cmp-failure-bin"

  cp "$fixtures/bare-aliases.input.rc" "$actual"
  before_hash=$(sha256sum "$actual" | awk '{print $1}')
  mkdir "$shadow_bin"
  printf '#!/bin/sh\nexit 2\n' > "$shadow_bin/cmp"
  chmod 0755 "$shadow_bin/cmp"

  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$missing_php"; then
    fail 'cmp failure exit status'
  fi
  after_hash=$(sha256sum "$actual" | awk '{print $1}')
  [ "$before_hash" = "$after_hash" ] || fail 'cmp failure atomicity'
  [ -z "$(find "$workspace" -name 'cmp-failure.rc.tmp.*' -print -quit)" ] \
    || fail 'cmp failure cleanup'

  printf 'ok - cmp failure\n'
}

run_chown_failure() {
  actual="$workspace/chown-failure.rc"
  missing_php="$workspace/chown-failure.php"
  shadow_bin="$workspace/chown-failure-bin"

  cp "$fixtures/bare-aliases.input.rc" "$actual"
  chown 991:991 "$actual"
  chmod 0640 "$actual"
  before_hash=$(sha256sum "$actual" | awk '{print $1}')
  before_metadata=$(stat -c '%u:%g:%a' "$actual")
  mkdir "$shadow_bin"
  printf '#!/bin/sh\nexit 1\n' > "$shadow_bin/chown"
  chmod 0755 "$shadow_bin/chown"

  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$missing_php"; then
    fail 'chown failure exit status'
  fi
  after_hash=$(sha256sum "$actual" | awk '{print $1}')
  after_metadata=$(stat -c '%u:%g:%a' "$actual")
  [ "$before_hash" = "$after_hash" ] || fail 'chown failure atomicity'
  [ "$before_metadata" = "$after_metadata" ] || fail 'chown failure ownership and mode'
  [ -z "$(find "$workspace" -name 'chown-failure.rc.tmp.*' -print -quit)" ] \
    || fail 'chown failure cleanup'

  printf 'ok - chown failure\n'
}

run_case bare-aliases
run_case network-port-setters
run_case default-encryption
run_case custom-encryption
run_case socket-allocation
run_missing_optional_files
run_write_failure
run_grep_failure
run_cmp_failure
run_chown_failure
