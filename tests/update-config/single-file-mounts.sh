#!/bin/sh
# Host entry point: validates both writable and canonical read-only bind mounts.
set -eu
[ "$#" -ge 1 ] || { printf 'Usage: %s IMAGE [CANDIDATE_ROOTFS]\n' "$0" >&2; exit 64; }
image=$1
candidate_rootfs=${2:-}
test_root=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
# The shared smoke launchers label each container with this run's owner token.
. "$test_root/../smoke/lib.sh"
workspace=$(mktemp -d)
name="smoke-migration-mounts-$$"
cleanup() { smoke_remove_run_containers || :; rm -rf "$workspace"; }
smoke_install_traps
cp "$test_root/fixtures/bare-aliases.input.rc" "$workspace/config.rc"
cp "$test_root/fixtures/php-config.input.php" "$workspace/config.php"
chmod 0660 "$workspace/config.rc" "$workspace/config.php"
run_mount_case() {
  readonly=$1
  migration_signal=${2:-}
  migration_refusal=${3:-false}
  set -- --rm --name "$name" --entrypoint /bin/sh \
    --mount "type=bind,src=$workspace/config.rc,dst=/mounted/config.rc$readonly" \
    --mount "type=bind,src=$workspace/config.php,dst=/mounted/config.php$readonly" \
    --mount "type=bind,src=$test_root,dst=/tests/update-config,readonly"
  if [ -n "$candidate_rootfs" ]; then
    set -- "$@" --mount "type=bind,src=$candidate_rootfs/usr/local/bin/update-config,dst=/usr/local/bin/update-config,readonly"
    for helper in atomic-file.sh migrate-rutorrent-config.php migrate-rtorrent-config.php config-file-commit.php; do
      helper_source="$candidate_rootfs/usr/local/libexec/$helper"
      [ "$helper" != config-file-commit.php ] || [ -z "$migration_signal" ] || helper_source="$workspace/signal-commit.php"
      set -- "$@" --mount "type=bind,src=$helper_source,dst=/usr/local/libexec/$helper,readonly"
    done
  fi
  if [ -z "$candidate_rootfs" ] && [ -n "$migration_signal" ]; then
    set -- "$@" --mount "type=bind,src=$workspace/signal-commit.php,dst=/usr/local/libexec/config-file-commit.php,readonly"
  fi
  if [ -n "${UPDATE_CONFIG_PCNTL:-}" ]; then
    set -- "$@" --mount "type=bind,src=$UPDATE_CONFIG_PCNTL,dst=/usr/lib/php85/modules/pcntl.so,readonly"
  fi
  set -- "$@" --env "MIGRATION_SIGNAL=$migration_signal" --env "MIGRATION_REFUSAL=$migration_refusal"
  if [ -n "${UPDATE_CONFIG_TOKENIZER:-}" ]; then
    set -- "$@" --mount "type=bind,src=$UPDATE_CONFIG_TOKENIZER,dst=/usr/lib/php85/modules/tokenizer.so,readonly"
  fi
  smoke_run "$@" "$image" -eu -c '
    workspace=/mounted
    . /tests/update-config/common.sh
    before_rc=$(hash_of /mounted/config.rc)
    before_php=$(hash_of /mounted/config.php)
    status=0
    /usr/local/bin/update-config /mounted/config.rc /mounted/config.php >/tmp/migration.output 2>/tmp/migration.error || status=$?
    if [ "$MIGRATION_REFUSAL" = true ]; then
      [ "$status" -ne 0 ] || fail "read-only noncanonical mount accepted"
      grep -Fq "Configuration requires migration but mounted file is not writable" /tmp/migration.error || { cat /tmp/migration.error >&2; fail "read-only mounted refusal diagnostic"; }
      [ "$before_rc" = "$(hash_of /mounted/config.rc)" ] || fail "read-only mounted rc changed"
      [ "$before_php" = "$(hash_of /mounted/config.php)" ] || fail "read-only mounted PHP changed"
      assert_clean "read-only mounted refusal"
      exit 0
    fi
    if [ -n "$MIGRATION_SIGNAL" ]; then
      case "$MIGRATION_SIGNAL" in HUP) expected=129;; INT) expected=130;; TERM) expected=143;; esac
      [ "$status" -eq "$expected" ] || fail "mounted $MIGRATION_SIGNAL status $status"
      cmp -s /tests/update-config/fixtures/bare-aliases.expected.rc /mounted/config.rc || fail "mounted $MIGRATION_SIGNAL rc content"
      cmp -s /tests/update-config/fixtures/php-config.input.php /mounted/config.php || fail "mounted $MIGRATION_SIGNAL PHP preservation"
      [ "$(stat -c %a /mounted/config.rc)" = 660 ] || fail "mounted $MIGRATION_SIGNAL mode"
      assert_clean "mounted $MIGRATION_SIGNAL"
      exit 0
    fi
    [ "$status" -eq 0 ] || { cat /tmp/migration.error >&2; fail "mounted migration status $status"; }
    cmp -s /tests/update-config/fixtures/bare-aliases.expected.rc /mounted/config.rc || fail "mounted rc content"
    php85 -l /mounted/config.php >/dev/null || fail "mounted PHP syntax"
    [ "$(php85 -r '\''include $argv[1]; echo $topDirectory;'\'' /mounted/config.php)" = /data/downloads/ ] || fail "mounted download directory"
    [ "$(stat -c %a /mounted/config.rc)" = 660 ] || fail "mounted rc mode"
    [ "$(stat -c %a /mounted/config.php)" = 660 ] || fail "mounted PHP mode"
    /usr/local/bin/update-config /mounted/config.rc /mounted/config.php >/tmp/migration.output 2>/tmp/migration.error || { cat /tmp/migration.error >&2; fail "mounted second migration"; }
    assert_clean "mounted idempotent migration"
  '

}
run_mount_case ''
printf 'ok - writable single-file rc and PHP bind mounts\n'
run_mount_case ',readonly'
printf 'ok - canonical read-only single-file rc and PHP bind mounts\n'
cp "$test_root/fixtures/bare-aliases.input.rc" "$workspace/config.rc"
cp "$test_root/fixtures/php-config.input.php" "$workspace/config.php"
run_mount_case ',readonly' '' true
printf 'ok - migration-required read-only single-file mounts refuse unchanged\n'

# Inject a real ordinary shutdown signal after only the first 16 bytes are written.
# The mounted writer must defer delivery until the validated candidate is durable.
if [ -n "$candidate_rootfs" ]; then
  cp "$candidate_rootfs/usr/local/libexec/config-file-commit.php" "$workspace/signal-commit.php"
else
  smoke_run --rm --name "$name" --entrypoint cat "$image" /usr/local/libexec/config-file-commit.php >"$workspace/signal-commit.php"
fi
python3 - "$workspace/signal-commit.php" <<'PYTHON'
import pathlib, sys
path = pathlib.Path(sys.argv[1])
source = path.read_text()
original = '$written = fwrite($handle, substr($contents, $offset));'
replacement = "$written = fwrite($handle, substr($contents, $offset, 16)); if ($offset === 0) { exec('kill -' . getenv('MIGRATION_SIGNAL') . ' ' . getmypid()); }"
if source.count(original) != 1:
    raise SystemExit('mounted writer signal injection point changed')
path.write_text(source.replace(original, replacement))
PYTHON
for migration_signal in HUP INT TERM; do
  cp "$test_root/fixtures/bare-aliases.input.rc" "$workspace/config.rc"
  cp "$test_root/fixtures/php-config.input.php" "$workspace/config.php"
  chmod 0660 "$workspace/config.rc" "$workspace/config.php"
  run_mount_case '' "$migration_signal"
  printf 'ok - mounted partial-write %s defers until complete validated candidate\n' "$migration_signal"
done
