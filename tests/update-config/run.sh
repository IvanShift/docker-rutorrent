#!/bin/sh
set -eu
umask 0022
fixtures=/tests/update-config/fixtures
workspace=$(mktemp -d)
php_bin=php85
php_path=$(command -v "$php_bin")
marker_existed=false
[ ! -f /etc/rtorrent-legacy ] || marker_existed=true
. /tests/update-config/common.sh
cleanup() {
  if [ "$marker_existed" = true ]; then touch /etc/rtorrent-legacy; else rm -f /etc/rtorrent-legacy; fi
  rm -rf "$workspace"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

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
  label="$tool-failure"
  actual="$workspace/$label.rc"
  shadow_bin="$workspace/$label-bin"
  cp "$fixtures/bare-aliases.input.rc" "$actual"
  chown 991:991 "$actual"
  chmod 0660 "$actual"
  before_hash=$(hash_of "$actual")
  before_metadata=$(stat -c '%u:%g:%a:%i' "$actual")
  mkdir "$shadow_bin"
  printf '#!/bin/sh\nexit %s\n' "$tool_status" >"$shadow_bin/$tool"
  chmod 0755 "$shadow_bin/$tool"
  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$actual" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail "$label exit status"; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail "$label original content"
  [ "$before_metadata" = "$(stat -c '%u:%g:%a:%i' "$actual")" ] || fail "$label original metadata"
  [ -s "$workspace/error" ] || fail "$label diagnostic"
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
  chmod 0755 "$workspace"
  chmod 0000 "$workspace/real.rc"
  if su-exec 65534:65534 /usr/local/bin/update-config "$workspace/real.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error"; then fail 'unreadable target accepted'; fi
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
}

run_signals() {
  for signal_spec in HUP:129 INT:130 TERM:143; do
    signal_name=${signal_spec%:*}
    signal_status=${signal_spec#*:}
    shadow_bin="$workspace/signal-$signal_name"
    mkdir "$shadow_bin"
    printf '#!/bin/sh\nkill -%s "$PPID"\nsleep 1\nexit 1\n' "$signal_name" >"$shadow_bin/mktemp"
    chmod 0755 "$shadow_bin/mktemp"
    cp "$fixtures/bare-aliases.input.rc" "$workspace/signal.rc"
    status=0
    PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$workspace/signal.rc" "$workspace/missing.php" >"$workspace/output" 2>"$workspace/error" || status=$?
    [ "$status" -eq "$signal_status" ] || fail "$signal_name exit status $status"
    assert_clean "$signal_name"
  done
  printf 'ok - migration signals terminate\n'
}

rm -f /etc/rtorrent-legacy
for case_name in bare-aliases network-port-setters default-encryption custom-encryption socket-allocation all-aliases syntax-traps log-stock log-custom log-multiple log-comment log-inline legacy-rollback; do run_case "$case_name"; done
run_targets
run_readonly_canonical
for tool_spec in mktemp:1 cmp:2 chown:1 chmod:1 mv:1 stat:1 cp:1; do run_tool_failure "${tool_spec%:*}" "${tool_spec#*:}"; done
run_case multiple-adjust
run_case adjustment-order
expect_rc_failure small-socket 'port_range = 45000-45000
network.max_open_sockets.set = 256' 'Socket'
expect_rc_failure small-memory 'port_range = 45000-45000
max_memory_usage = 256M' 'Memory'
run_signals
. /tests/update-config/php-cases.sh

touch /etc/rtorrent-legacy
for case_name in legacy-stock legacy-limits legacy-spacing; do run_case "$case_name"; done
run_case legacy-ipv6
run_case legacy-custom-log
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

# Custom IPv6 values and custom flush log destinations must survive a mode switch.
for roundtrip_case in legacy-ipv6 legacy-custom-log; do
  label="$roundtrip_case native round-trip"
  actual="$workspace/$roundtrip_case-roundtrip.rc"
  cp "$fixtures/$roundtrip_case.expected.rc" "$actual"
  migrate "$actual" "$workspace/missing.php"
  cmp -s "$fixtures/$roundtrip_case.input.rc" "$actual" || fail "$label original operator settings"
  assert_idempotent "$label" "$actual" "$workspace/missing.php" "$actual"
  printf 'ok - %s\n' "$label"
done
