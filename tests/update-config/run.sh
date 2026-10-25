#!/bin/sh
set -eu
umask 0022
fixtures=/tests/update-config/fixtures
workspace=$(mktemp -d)
php_bin=php85
php_path=$(command -v "$php_bin")
marker_existed=false
[ ! -f /etc/rtorrent-legacy ] || marker_existed=true
cleanup() {
  if [ "$marker_existed" = true ]; then touch /etc/rtorrent-legacy; else rm -f /etc/rtorrent-legacy; fi
  rm -rf "$workspace"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# Sourced under the traps: its private-/tmp precondition can end the run.
. /tests/update-config/common.sh
# Only the FileBot cases below ask update-config for the FileBot hooks.
unset UPDATE_FILEBOT_HOOKS

run_case() {
  label=$1
  actual="$workspace/$label.rc"
  missing_php="$workspace/missing.php"
  cp "$fixtures/$label.input.rc" "$actual"
  chown 991:991 "$actual"
  chmod 0660 "$actual"
  before_hash=$(hash_of "$actual")
  before_inode=$(stat -c %i "$actual")
  migrate "$actual" "$missing_php"
  cmp -s "$fixtures/$label.expected.rc" "$actual" || { diff -u "$fixtures/$label.expected.rc" "$actual" >&2 || :; fail "$label content"; }
  [ "$(stat -c '%u:%g:%a' "$actual")" = 991:991:660 ] || fail "$label ownership and mode"
  if [ "$before_hash" != "$(hash_of "$actual")" ]; then
    [ "$before_inode" != "$(stat -c %i "$actual")" ] || fail "$label atomic replacement inode"
  else
    [ "$before_inode" = "$(stat -c %i "$actual")" ] || fail "$label unchanged inode"
  fi
  assert_clean "$label"
  assert_idempotent "$label" "$actual" "$missing_php" "$actual"
  [ "$(stat -c '%u:%g:%a' "$actual")" = 991:991:660 ] || fail "$label idempotent ownership and mode"
  printf 'ok - %s\n' "$label"
}

expect_rc_failure() {
  label=$1
  content=$2
  diagnostic=$3
  actual="$workspace/$label.rc"
  printf '%s\n' "$content" >"$actual"
  before_hash=$(hash_of "$actual")
  if /usr/local/bin/update-config "$actual" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail "$label exit status"; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail "$label atomic refusal"
  grep -Fq "$diagnostic" "$workspace/error" || { cat "$workspace/error" >&2; fail "$label diagnostic"; }
  assert_clean "$label"
  printf 'ok - %s\n' "$label"
}

run_tool_failure() {
  tool=$1
  tool_status=$2
  fail_invocation=${3:-1}
  label="$tool-$fail_invocation-failure"
  actual="$workspace/$label.rc"
  shadow_bin="$workspace/$label-bin"
  cp "$fixtures/bare-aliases.input.rc" "$actual"
  chown 991:991 "$actual"
  chmod 0660 "$actual"
  before_hash=$(hash_of "$actual")
  before_metadata=$(stat -c '%u:%g:%a:%i' "$actual")
  real_tool=$(command -v "$tool")
  mkdir "$shadow_bin"
  cat >"$shadow_bin/$tool" <<WRAPPER
#!/bin/sh
count=0
[ ! -f "$shadow_bin/count" ] || read -r count <"$shadow_bin/count"
count=\$((count + 1))
printf '%s\n' "\$count" >"$shadow_bin/count"
[ "\$count" -ne "$fail_invocation" ] || exit "$tool_status"
exec "$real_tool" "\$@"
WRAPPER
  chmod 0755 "$shadow_bin/$tool"
  case "$tool:$fail_invocation" in
    mktemp:1) diagnostic='Unable to create temporary directory';;
    mktemp:2) diagnostic='Configuration requires migration but its directory is not writable';;
    cmp:1) diagnostic='Unable to compare transformed rTorrent configuration';;
    cmp:2|stat:2) diagnostic='Configuration changed during migration';;
    cp:1|cp:2) diagnostic='Unable to copy rTorrent configuration for migration';;
    cp:3) diagnostic='Unable to stage transformed rTorrent configuration';;
    stat:1) diagnostic='Unable to read mode and ownership';;
    chown:1|chmod:1) diagnostic='Unable to restore mode and ownership';;
    mv:1) diagnostic='Unable to replace rTorrent configuration';;
    *) fail "unsupported fault injection $tool:$fail_invocation";;
  esac
  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail "$label exit status"; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail "$label original content"
  [ "$before_metadata" = "$(stat -c '%u:%g:%a:%i' "$actual")" ] || fail "$label original metadata"
  grep -Fq "$diagnostic" "$workspace/error" || { cat "$workspace/error" >&2; fail "$label classified diagnostic"; }
  assert_clean "$label"
  printf 'ok - %s\n' "$label"
}

run_targets() {
  label='target validation'
  printf '%s\n' 'port_range = 45000-45000' >"$workspace/real.rc"
  ln -s "$workspace/real.rc" "$workspace/link.rc"
  if /usr/local/bin/update-config "$workspace/link.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail 'symlink target accepted'; fi
  grep -Fq 'symbolic link' "$workspace/error" || fail 'symlink diagnostic'
  mkdir "$workspace/directory.rc"
  if /usr/local/bin/update-config "$workspace/directory.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail 'directory target accepted'; fi
  grep -Fq 'is not a regular file' "$workspace/error" || fail 'directory classified diagnostic'
  chmod 0755 "$workspace"
  chmod 0000 "$workspace/real.rc"
  if su-exec 65534:65534 /usr/local/bin/update-config "$workspace/real.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail 'unreadable target accepted'; fi
  grep -Fq 'Unable to copy rTorrent configuration for migration' "$workspace/error" || fail 'unreadable classified diagnostic'
  chmod 0644 "$workspace/real.rc"
  status=0
  /usr/local/bin/update-config a b c >"$workspace/output" 2>"$workspace/error" || status=$?
  [ "$status" -eq 64 ] || fail 'three arguments exit64'
  grep -Fq Usage "$workspace/error" || fail 'three arguments diagnostic'
  /usr/local/bin/update-config "$workspace/absent.rc" "$workspace/absent.php" >"$workspace/output" 2>"$workspace/error" || fail 'missing optional files status'
  grep -Fq 'Skip rTorrent configuration' "$workspace/output" || fail 'missing rc diagnostic'
  grep -Fq 'Skip ruTorrent PHP configuration' "$workspace/output" || fail 'missing PHP diagnostic'
  assert_clean "$label"
  printf 'ok - target validation and arguments\n'
}

run_readonly_canonical() {
  label='canonical rc in read-only directory'
  mkdir "$workspace/readonly"
  cp "$fixtures/bare-aliases.expected.rc" "$workspace/readonly/config.rc"
  chmod 0755 "$workspace"
  chmod 0555 "$workspace/readonly"
  before_hash=$(hash_of "$workspace/readonly/config.rc")
  su-exec 65534:65534 /usr/local/bin/update-config "$workspace/readonly/config.rc" "$workspace/absent.php" >"$workspace/output" 2>"$workspace/error" || { cat "$workspace/error" >&2; fail "$label"; }
  [ "$before_hash" = "$(hash_of "$workspace/readonly/config.rc")" ] || fail "$label content"
  assert_clean "$label"
  printf 'ok - %s\n' "$label"
  label='migration required in read-only directory'
  chmod 0755 "$workspace/readonly"
  cp "$fixtures/bare-aliases.input.rc" "$workspace/readonly/config.rc"
  chmod 0555 "$workspace/readonly"
  before_hash=$(hash_of "$workspace/readonly/config.rc")
  if su-exec 65534:65534 /usr/local/bin/update-config "$workspace/readonly/config.rc" "$workspace/absent.php" >"$workspace/output" 2>"$workspace/error"; then fail "$label exit status"; fi
  grep -Fq 'Configuration requires migration but its directory is not writable' "$workspace/error" || fail "$label diagnostic"
  [ "$before_hash" = "$(hash_of "$workspace/readonly/config.rc")" ] || fail "$label original content"
  assert_clean "$label"
  printf 'ok - %s\n' "$label"
}

run_signals() {
  # mv is reached after both the inspection directory and sibling candidate exist.
  for signal_spec in HUP:129 INT:130 TERM:143; do
    signal_name=${signal_spec%:*}
    signal_status=${signal_spec#*:}
    shadow_bin="$workspace/signal-$signal_name"
    mkdir "$shadow_bin"
    printf '#!/bin/sh\nkill -%s "$PPID"\nsleep 1\nexit 1\n' "$signal_name" >"$shadow_bin/mv"
    chmod 0755 "$shadow_bin/mv"
    cp "$fixtures/bare-aliases.input.rc" "$workspace/signal.rc"
    before_hash=$(hash_of "$workspace/signal.rc")
    status=0
    PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$workspace/signal.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error" || status=$?
    [ "$status" -eq "$signal_status" ] || fail "$signal_name exit status $status"
    [ "$before_hash" = "$(hash_of "$workspace/signal.rc")" ] || fail "$signal_name original content"
    assert_clean "$signal_name"
  done
  printf 'ok - migration signals terminate and remove allocated temporaries\n'
}

rm -f /etc/rtorrent-legacy
for case_name in bare-aliases network-port-setters default-encryption custom-encryption socket-allocation all-aliases syntax-traps log-stock log-custom log-multiple log-comment log-inline legacy-rollback shipped-untied nested-methods small-allocations utf8-bare proxy-setters old-shipped-defaults log-groups crlf-indent flush-marker-reset shutdown-no-newline shutdown-opt-out shutdown-erase-opt-out shutdown-commented-operator continuation log-close-reopen log-method-simple crlf-paths; do run_case "$case_name"; done
# startup asks for the FileBot hooks in an image with FileBot; the wrapper needs nothing else.
UPDATE_FILEBOT_HOOKS=true
export UPDATE_FILEBOT_HOOKS
for case_name in filebot-no-final-newline filebot-final-comment filebot-crlf filebot-existing filebot-literal-forms; do run_case "$case_name"; done
unset UPDATE_FILEBOT_HOOKS
run_targets
run_readonly_canonical
for tool_spec in mktemp:1 cmp:2 chown:1 chmod:1 mv:1 stat:1 cp:1; do run_tool_failure "${tool_spec%:*}" "${tool_spec#*:}"; done
for tool_spec in mktemp:1:2 cp:1:2 cp:1:3 stat:1:2 cmp:2:2; do
  tool=${tool_spec%%:*}; remaining=${tool_spec#*:}
  run_tool_failure "$tool" "${remaining%:*}" "${remaining#*:}"
done
# The migrator is the one source of the early save hook: the image template
# carries no copy, and migrating it adds exactly one hook.
label='image template receives the early save hook only from migration'
actual="$workspace/template.rc"
cp /usr/local/share/rutorrent-defaults/rtorrent.rc "$actual"
! grep -F 'docker_rutorrent_early_save' "$actual" >/dev/null || fail "$label template copy"
migrate "$actual" "$workspace/missing.php"
[ "$(grep -c '^method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="$' "$actual")" = 1 ] || fail "$label hook"
assert_idempotent "$label" "$actual" "$workspace/missing.php" "$actual"
printf 'ok - %s\n' "$label"
template_log_group() {
  # The template names the current daemon's group; a 0.9.8 image needs its own.
  label="image template log group in $1 mode"
  actual="$workspace/template-$1.rc"
  cp /usr/local/share/rutorrent-defaults/rtorrent.rc "$actual"
  migrate "$actual" "$workspace/missing.php"
  [ "$(grep -c '^log\.add_output' "$actual")" = 1 ] || fail "$label count"
  grep -Fx "log.add_output = \"$2\", \"rtorrent\"" "$actual" >/dev/null || fail "$label"
  assert_idempotent "$label" "$actual" "$workspace/missing.php" "$actual"
  printf 'ok - %s\n' "$label"
}
template_log_group native errors
run_case multiple-adjust
run_case adjustment-order
expect_rc_failure small-socket 'port_range = 45000-45000
network.max_open_sockets.set = 256' 'Socket'
expect_rc_failure zero-file-allocation 'system.sockets.files.max_alloc.set = 0' 'Socket allocation for files'
expect_rc_failure small-memory 'port_range = 45000-45000
max_memory_usage = 256M' 'Memory'
run_signals
# The first migration must release its scratch before the second file starts.
label='two-file changed and unchanged scratch cleanup'
actual="$workspace/both.rc"
actual_php="$workspace/both.php"
cp "$fixtures/bare-aliases.input.rc" "$actual"
cp "$fixtures/php-config.input.php" "$actual_php"
migrate "$actual" "$actual_php"
assert_clean "$label changed"
migrate "$actual" "$actual_php"
assert_clean "$label unchanged"
printf 'ok - %s\n' "$label"
expect_rc_failure nonliteral-proxy 'network.proxy_address.set = (cat,"host",":8080")' 'Proxy migration requires a literal'
# PHP prints an uncaught error on standard output; a failed transform shows it first.
label='transform failure output'
shadow_bin="$workspace/transform-failure-bin"
mkdir "$shadow_bin"
printf '#!/bin/sh\necho "PHP Fatal error: injected transform failure"\nexit 255\n' >"$shadow_bin/$php_bin"
chmod 0755 "$shadow_bin/$php_bin"
actual="$workspace/transform-failure.rc"
cp "$fixtures/bare-aliases.input.rc" "$actual"
before_hash=$(hash_of "$actual")
status=0
PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error" || status=$?
[ "$status" -eq 255 ] || fail "$label exit status $status"
[ "$(sed -n '1p' "$workspace/error")" = 'PHP Fatal error: injected transform failure' ] || { cat "$workspace/error" >&2; fail "$label PHP output"; }
grep -Fq 'Unable to transform rTorrent configuration' "$workspace/error" || fail "$label classified diagnostic"
[ "$before_hash" = "$(hash_of "$actual")" ] || fail "$label original content"
assert_clean "$label"
printf 'ok - %s\n' "$label"
expect_rc_failure small-file-allocation 'system.sockets.files.max_alloc.set = 3' 'Socket allocation for files'
. /tests/update-config/php-cases.sh
/tests/update-config/policy-blocks.sh

touch /etc/rtorrent-legacy
for case_name in legacy-stock legacy-limits legacy-spacing legacy-utf8 legacy-native-controls legacy-shutdown-no-newline legacy-shutdown-opt-out legacy-shutdown-erase-opt-out legacy-continuation; do run_case "$case_name"; done
UPDATE_FILEBOT_HOOKS=true
export UPDATE_FILEBOT_HOOKS
for case_name in legacy-filebot legacy-filebot-literal-forms; do run_case "$case_name"; done
unset UPDATE_FILEBOT_HOOKS
run_case legacy-ipv6
run_case legacy-custom-log
run_case legacy-log-groups
template_log_group legacy error
expect_rc_failure legacy-unsupported-socket 'network.listen.port.range.set = 45000-45000
system.sockets.unknown.max_alloc.set = 100' 'Unsupported native socket'
rm -f /etc/rtorrent-legacy
# Round-trip the actual legacy golden output through the native rules.
label='legacy-stock native round-trip'
actual="$workspace/roundtrip.rc"
cp "$fixtures/legacy-stock.expected.rc" "$actual"
migrate "$actual" "$workspace/missing.php"
cmp -s "$fixtures/native-stock.expected.rc" "$actual" || { diff -u "$fixtures/native-stock.expected.rc" "$actual" >&2 || :; fail "$label"; }
assert_idempotent "$label" "$actual" "$workspace/missing.php" "$actual"
printf 'ok - %s\n' "$label"

# Custom IPv6 values, custom flush log destinations and continued lines must survive a mode switch.
for roundtrip_case in legacy-ipv6 legacy-custom-log legacy-continuation; do
  label="$roundtrip_case native round-trip"
  actual="$workspace/$roundtrip_case-roundtrip.rc"
  cp "$fixtures/$roundtrip_case.expected.rc" "$actual"
  migrate "$actual" "$workspace/missing.php"
  expected="$workspace/$roundtrip_case-native-expected.rc"
  { printf '%s\n' 'catch = {"system.file_name.allow_legacy_utf8.set=1"}'; cat "$fixtures/$roundtrip_case.input.rc"; } >"$expected"
  cmp -s "$expected" "$actual" || fail "$label original operator settings"
  assert_idempotent "$label" "$actual" "$workspace/missing.php" "$actual"
  printf 'ok - %s\n' "$label"
done
