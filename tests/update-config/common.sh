# Sourced helpers for the migration test entry points.

fail() {
  printf 'not ok - %s\n' "$1" >&2
  exit 1
}

hash_of() {
  hash_value=$(sha256sum "$1") || fail "cannot hash $1"
  printf '%s\n' "${hash_value%% *}"
}

assert_clean() {
  [ -z "$(find "$workspace" -name '*.tmp.*' -print -quit)" ] || fail "$1 sibling temporary file cleanup"
  [ -z "$(find /tmp -maxdepth 1 -name 'update-config.*' -print -quit)" ] || fail "$1 migration temporary directory cleanup"
}

migrate() {
  /usr/local/bin/update-config "$@" >"$workspace/migration.stdout" 2>"$workspace/migration.stderr" \
    || { cat "$workspace/migration.stderr" >&2; fail "$label migration"; }
}

assert_idempotent() {
  idempotent_label=$1
  idempotent_rc=$2
  idempotent_php=$3
  idempotent_file=$4
  idempotent_hash=$(hash_of "$idempotent_file")
  idempotent_inode=$(stat -c %i "$idempotent_file")
  migrate "$idempotent_rc" "$idempotent_php"
  [ "$idempotent_hash" = "$(hash_of "$idempotent_file")" ] || fail "$idempotent_label idempotency"
  [ "$idempotent_inode" = "$(stat -c %i "$idempotent_file")" ] || fail "$idempotent_label unchanged inode"
  assert_clean "$idempotent_label"
}

external() {
  "$php_bin" -r 'include $argv[2]; echo $pathToExternals[$argv[1]] ?? "<absent>";' "$1" "$2" \
    || fail "cannot evaluate external $1"
}

php_value() {
  "$php_bin" -r 'include $argv[2]; echo json_encode(${$argv[1]}, JSON_UNESCAPED_SLASHES);' "$1" "$2" \
    || fail "cannot evaluate PHP variable $1"
}
