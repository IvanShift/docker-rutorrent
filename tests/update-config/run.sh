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

run_forbid_user_settings() {
  missing_rc="$workspace/forbid-user-settings.rc"

  # Authentication off means nothing vouches for a user name: nginx checks
  # none, and php-fpm still fills PHP_AUTH_USER from a header the client chose
  # for itself. Profiles must therefore be forced off whatever the file said.
  actual="$workspace/forbid-auth-off.php"
  cp "$fixtures/php-config.input.php" "$actual"
  HTTP_AUTH=false /usr/local/bin/update-config "$missing_rc" "$actual" \
    || fail 'forbid user settings auth off migration'
  grep -Eq '^[[:space:]]*\$forbidUserSettings = true;$' "$actual" \
    || fail 'forbid user settings auth off content'

  first_hash=$(sha256sum "$actual" | awk '{print $1}')
  HTTP_AUTH=false /usr/local/bin/update-config "$missing_rc" "$actual" \
    || fail 'forbid user settings second migration'
  [ "$first_hash" = "$(sha256sum "$actual" | awk '{print $1}')" ] \
    || fail 'forbid user settings idempotency'

  # With authentication on the name is vouched for and separate profiles are a
  # legitimate choice, so the operator's own value stands.
  actual="$workspace/forbid-auth-on.php"
  cp "$fixtures/php-config.input.php" "$actual"
  HTTP_AUTH=true /usr/local/bin/update-config "$missing_rc" "$actual" \
    || fail 'forbid user settings auth on migration'
  grep -Eq '^[[:space:]]*\$forbidUserSettings = false;$' "$actual" \
    || fail 'forbid user settings auth on content'

  # A configuration that never carried the setting still ends up with it.
  actual="$workspace/forbid-absent.php"
  grep -v forbidUserSettings "$fixtures/php-config.input.php" > "$actual"
  HTTP_AUTH=false /usr/local/bin/update-config "$missing_rc" "$actual" \
    || fail 'forbid user settings absent migration'
  grep -Eq '^[[:space:]]*\$forbidUserSettings = true;$' "$actual" \
    || fail 'forbid user settings absent content'

  printf 'ok - forbid user settings\n'
}

check_php_external() {
  label=$1
  expected=$2
  entries=$3
  cp "$fixtures/php-config.input.php" "$actual"
  printf '\n$pathToExternals = array(%s\n);\n' "$entries" >> "$actual"
  php85 -l "$actual" >/dev/null || fail "$label fixture syntax"
  /usr/local/bin/update-config "$missing_rc" "$actual" || fail "$label migration"
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["php"];' "$actual")" = "$expected" ] \
    || fail "$label"
  first_hash=$(sha256sum "$actual" | awk '{print $1}')
  /usr/local/bin/update-config "$missing_rc" "$actual" || fail "$label second migration"
  [ "$first_hash" = "$(sha256sum "$actual" | awk '{print $1}')" ] \
    || fail "$label idempotency"
}

run_php_external_override() {
  missing_rc="$workspace/php-external.rc"
  volume_conf="$workspace/php-external-volume"
  app_conf="$workspace/php-external-app-conf"
  wrapper="$workspace/php-wrapper"
  mkdir "$volume_conf"
  ln -s "$volume_conf" "$app_conf"
  printf '#!/bin/sh\nexec /usr/bin/php85 "$@"\n' > "$wrapper"
  chmod 0755 "$wrapper"
  "$wrapper" -r 'echo "OK";' >/dev/null || fail 'operator PHP wrapper is executable'
  actual="$app_conf/config.php"

  check_php_external 'valid PHP override survives restart' "$wrapper" "\"php\" => '$wrapper',"
  check_php_external 'concatenated PHP wrapper preserved' "$wrapper" "\"php\" => '' . '$wrapper',"
  check_php_external 'old-version PHP expression preserved' "$wrapper" "\"php\" => false ? '/usr/bin/php84' : '$wrapper',"
  grep -Fq "'/usr/bin/php84'" "$actual" || fail 'old-version PHP expression unchanged'
  check_php_external 'double-quoted PHP override survives restart' "$wrapper" "\"php\" => \"$wrapper\","
  check_php_external 'last PHP override without comma survives restart' "$wrapper" "\"php\" => '$wrapper'"
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["curl"];' "$actual")" = /usr/bin/curl ] \
    || fail 'missing curl appended after comma-free PHP override'
  check_php_external 'trailing PHP comment without comma remains valid' /usr/bin/php85 \
    '"php" => "/usr/bin/php" // operator note'
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["curl"];' "$actual")" = /usr/bin/curl ] \
    || fail 'missing curl appended before trailing PHP comment'
  slash_wrapper="/$wrapper"
  "$slash_wrapper" -r 'echo "OK";' >/dev/null || fail 'double-slash PHP wrapper is executable'
  check_php_external 'quoted double-slash PHP path preserved' "$slash_wrapper" "\"php\" => '$slash_wrapper',"
  check_php_external 'single-quoted PHP key override survives restart' "$wrapper" "'php' => '$wrapper',"
  check_php_external 'double-quoted old PHP default updated to php85' /usr/bin/php85 '"php" => "/usr/bin/php84",'
  check_php_external 'double-quoted escaped curl remains valid' /usr/bin/php85 "\"php\" => '/usr/bin/php', \"curl\" => \"/tmp/o\\\"brien/curl\","
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["curl"];' "$actual")" = /usr/bin/curl ] \
    || fail 'double-quoted escaped curl updated'
  check_php_external 'single-quoted escaped curl remains valid' /usr/bin/php85 "\"php\" => '/usr/bin/php', \"curl\" => '/tmp/o\\'brien/curl',"
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["curl"];' "$actual")" = /usr/bin/curl ] \
    || fail 'single-quoted escaped curl updated'
  check_php_external 'future PHP override preserved' /usr/bin/php86 '"php" => "/usr/bin/php86",'
  check_php_external 'commented PHP key ignored' /usr/bin/php85 "
  \"php\" => '/usr/bin/php',
  // 'php' => '$wrapper',"
  check_php_external 'hash-commented PHP key ignored' /usr/bin/php85 "
  \"php\" => '/usr/bin/php',
  # 'php' => '$wrapper',"
  check_php_external 'comma in slash comment ignored' /usr/bin/php85 "
  \"php\" => '/usr/bin/php',
  // Example custom path, 'php' => '$wrapper',"
  check_php_external 'comma in hash comment ignored' /usr/bin/php85 "
  \"php\" => '/usr/bin/php',
  # Example custom path, 'php' => '$wrapper',"
  check_php_external 'multiline PHP key updated' /usr/bin/php85 "\"php\"
  =>
  '/usr/bin/php',"
  check_php_external 'quoted note PHP key ignored' /usr/bin/php85 "\"php\" => '/usr/bin/php', \"note\" => \"Example, 'php' => '$wrapper'\","
  check_php_external 'nested PHP key ignored' /usr/bin/php85 "\"php\" => '/usr/bin/php', \"note\" => array('x' => 1, 'php' => '$wrapper'),"
  check_php_external 'last active PHP key updated' /usr/bin/php85 "\"php\" => '$wrapper', \"php\" => '/usr/bin/php',"
  check_php_external 'last active PHP override preserved' "$wrapper" "\"php\" => '/usr/bin/php', \"php\" => '$wrapper',"
  check_php_external 'block-commented PHP key ignored' /usr/bin/php85 "
  \"php\" => '/usr/bin/php',
  /*
  'php' => '$wrapper',
  */"
  for kind in slash block literal; do
    cp "$fixtures/php-config.input.php" "$actual"
    if [ "$kind" = slash ]; then
      printf '%s\n' '// $pathToExternals = array("php" => "/tmp/old-wrapper",);' >> "$actual"
    elif [ "$kind" = block ]; then
      printf '%s\n' '/* $pathToExternals = array("php" => "/tmp/old-wrapper",); */' >> "$actual"
    else
      cat >> "$actual" <<'PHP'
$example = '$pathToExternals = array("php" => "/tmp/old-wrapper",);';
PHP
    fi
    printf '%s\n' '$pathToExternals = array("php" => "/usr/bin/php",);' >> "$actual"
    /usr/local/bin/update-config "$missing_rc" "$actual" || fail "$kind commented assignment migration"
    [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["php"];' "$actual")" = /usr/bin/php85 ] \
      || fail "$kind commented assignment ignored"
  done
  check_php_external 'missing PHP key added' /usr/bin/php85 ''
  check_php_external 'unrelated PHP version literal untouched' /usr/bin/php85 '"php" => "/usr/bin/php", "note" => "/usr/bin/php84",'
  [ "$(php85 -r 'include $argv[1]; echo $pathToExternals["note"];' "$actual")" = /usr/bin/php84 ] \
    || fail 'unrelated PHP version literal preserved'
  for old in '' /usr/bin/php /usr/bin/php81 /usr/bin/php82 /usr/bin/php83 /usr/bin/php84; do
    check_php_external "old single-quoted PHP default $old updated" /usr/bin/php85 "\"php\" => '$old',"
    check_php_external "old double-quoted PHP default $old updated" /usr/bin/php85 "\"php\" => \"$old\","
  done
  printf 'ok - PHP external override through operator conf link\n'
}

run_legacy_rollback() {
  actual="$workspace/legacy-rollback.rc"
  missing_php="$workspace/legacy-rollback.php"
  cat > "$actual" <<'RC'
encryption = allow_incoming,try_outgoing,enable_retry
port_range = 45000-45000
port_random = no
dht_port = 6881
RC
  /usr/local/bin/update-config "$actual" "$missing_php" >/dev/null || fail 'legacy rollback migration'
  grep -Fxq 'protocol.encryption.set = allow' "$actual" || fail 'legacy rollback encryption'
  grep -Fxq 'network.listen.port.range.set = 45000-45000' "$actual" || fail 'legacy rollback port range'
  grep -Fxq 'network.listen.port.random.set = no' "$actual" || fail 'legacy rollback port random'
  grep -Fxq 'dht.override_port.set = 6881' "$actual" || fail 'legacy rollback DHT port'
  first_hash=$(sha256sum "$actual" | awk '{print $1}')
  /usr/local/bin/update-config "$actual" "$missing_php" >/dev/null || fail 'legacy rollback second migration'
  [ "$first_hash" = "$(sha256sum "$actual" | awk '{print $1}')" ] || fail 'legacy rollback idempotency'
  printf 'ok - legacy rollback\n'
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
run_forbid_user_settings
run_php_external_override
run_legacy_rollback
