# Sourced by run.sh so all PHP cases share its cleanup and signal handlers.
image_external() {
  case "$1" in
    php) printf '%s\n' "$php_path" ;;
    unrar) printf '%s\n' /usr/local/bin/unrar ;;
    *) printf '/usr/bin/%s\n' "$1" ;;
  esac
}

assert_operator_externals() {
  externals_label=$1
  shift
  for key in php curl ffmpeg unrar sox mediainfo; do
    case " $* " in *" $key="*) continue ;; esac
    [ "$(external "$key" "$actual")" = "$(image_external "$key")" ] || fail "$externals_label default $key"
  done
  for pair in "$@"; do
    [ "$(external "${pair%%=*}" "$actual")" = "${pair#*=}" ] || fail "$externals_label operator ${pair%%=*}"
  done
}

# Append operator code from stdin to the scalar fixture, or with operator_base
# to a bare <?php or the image's stock config.php, beside an optional
# $operator_local config.local.php. KEY=VALUE arguments name the externals it
# must keep; every other image external must hold its default after the first
# and the repeated migration.
operator_case=0
check_operator_externals() {
  label=$1
  shift
  operator_case=$((operator_case + 1))
  mkdir "$workspace/operator-$operator_case"
  actual="$workspace/operator-$operator_case/config.php"
  case "${operator_base:-}" in
    bare) printf '<?php\n' >"$actual" ;;
    stock) cp /rutorrent/conf-dist/config.php "$actual" ;;
    *) cp "$fixtures/php-config.input.php" "$actual" ;;
  esac
  cat >>"$actual"
  [ -z "${operator_local:-}" ] || printf '%s\n' "$operator_local" >"$workspace/operator-$operator_case/config.local.php"
  "$php_bin" -l "$actual" >/dev/null || fail "$label fixture syntax"
  migrate "$workspace/missing.rc" "$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label transformed syntax"
  assert_operator_externals "$label" "$@"
  assert_clean "$label"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  assert_operator_externals "$label repeated" "$@"
}

check_php_external() {
  label=$1
  expected=$2
  entries=$3
  prefix=${4:-}
  actual="$workspace/php-external.php"
  missing_rc="$workspace/missing.rc"
  cp "$fixtures/php-config.input.php" "$actual"
  printf '%s\n' "$prefix" >>"$actual"
  printf '\n$pathToExternals = array(%s\n);\n' "$entries" >>"$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label fixture syntax"
  migrate "$missing_rc" "$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label transformed syntax"
  [ "$(external php "$actual")" = "$expected" ] || fail "$label effective PHP path"
  assert_clean "$label"
  assert_idempotent "$label" "$missing_rc" "$actual" "$actual"
}

run_php_external_override() {
  wrapper="$workspace/operator-php-wrapper"
  check_php_external 'operator PHP override preserved' "$wrapper" "\"php\" => '$wrapper',"
  check_php_external 'concatenated PHP expression preserved' "$wrapper" "\"php\" => '' . '$wrapper',"
  check_php_external 'old-version PHP expression preserved' "$wrapper" "\"php\" => false ? '/usr/bin/php84' : '$wrapper',"
  grep -Fq "'/usr/bin/php84'" "$actual" || fail 'old-version expression text'
  check_php_external 'double-quoted PHP override preserved' "$wrapper" "\"php\" => \"$wrapper\","
  check_php_external 'last PHP key without comma' "$wrapper" "\"php\" => '$wrapper'"
  [ "$(external curl "$actual")" = /usr/bin/curl ] || fail 'curl after comma-free key'
  check_php_external 'trailing line comment without comma' "$php_path" '"php" => "/usr/bin/php" // operator note'
  [ "$(external curl "$actual")" = /usr/bin/curl ] || fail 'curl before trailing comment'
  check_php_external 'double-slash PHP override' "/$wrapper" "\"php\" => '/$wrapper',"
  check_php_external 'single-quoted PHP key' "$wrapper" "'php' => '$wrapper',"
  check_php_external 'escaped double-quoted curl wrapper' "$php_path" '"php" => "/usr/bin/php", "curl" => "/tmp/o\"brien/curl",'
  [ "$(external curl "$actual")" = '/tmp/o"brien/curl' ] || fail 'escaped curl wrapper value'
  check_php_external 'escaped single-quoted curl wrapper' "$php_path" '"php" => "/usr/bin/php", "curl" => '\''/tmp/o\'\''brien/curl'\'', '
  [ "$(external curl "$actual")" = "/tmp/o'brien/curl" ] || fail 'single quoted curl wrapper value'
  for prefix in '// $pathToExternals = array("php" => "/comment",);' '/* $pathToExternals = array("php" => "/comment",); */' '$example = '\''$pathToExternals = array("php" => "/literal",);'\'';'; do
    check_php_external 'inactive array assignment ignored' "$php_path" '"php" => "/usr/bin/php",' "$prefix"
  done
  check_php_external 'multiline PHP key' "$php_path" '"php"
=>
"/usr/bin/php",'
  check_php_external 'nested PHP key ignored' "$php_path" "\"php\" => '/usr/bin/php', \"note\" => array('php' => '$wrapper'),"
  check_php_external 'quoted PHP key ignored' "$php_path" "\"php\" => '/usr/bin/php', \"note\" => \"Example, 'php' => '$wrapper'\","
  check_php_external 'last duplicate default updated' "$php_path" "\"php\" => '$wrapper', \"php\" => '/usr/bin/php',"
  check_php_external 'last duplicate wrapper preserved' "$wrapper" "\"php\" => '/usr/bin/php', \"php\" => '$wrapper',"
  check_php_external 'missing PHP key inserted' "$php_path" ''
  check_php_external 'unrelated version literal preserved' "$php_path" '"php" => "/usr/bin/php", "note" => "/usr/bin/php84",'
  [ "$(external note "$actual")" = /usr/bin/php84 ] || fail 'unrelated external literal'
  current_suffix=${php_bin#php}
  future_path="/usr/bin/php$((current_suffix + 1))"
  check_php_external 'missing future image PHP path migrated' "$php_path" "\"php\" => '$future_path',"
  for old in '' /usr/bin/php /usr/bin/php7 /usr/bin/php74 /usr/bin/php80 /usr/bin/php81 /usr/bin/php82 /usr/bin/php83 /usr/bin/php84; do
    check_php_external "old PHP default $old" "$php_path" "\"php\" => '$old',"
    check_php_external "old double-quoted PHP default $old" "$php_path" "\"php\" => \"$old\","
  done
  # Comment delimiters and commas cannot act as effective array entries.
  for comment in '// "php" => "/operator",' '# "php" => "/operator",' '/* "php" => "/operator", */'; do
    check_php_external 'commented PHP key ignored' "$php_path" "\"php\" => '/usr/bin/php',
$comment"
  done
  printf 'ok - PHP external values, expressions, comments and idempotency\n'
}

run_php_syntax_cases() {
  label='PHP token syntax and managed effective values'
  actual="$workspace/syntax.php"
  cp "$fixtures/php-config.input.php" "$actual"
  cat >>"$actual" <<'PHP'
/* $topDirectory = '/comment';
$scgi_host = 'comment'; $scgi_port = 42;
$forbidUserSettings = true;
*/
$example = <<<'EXAMPLE'
$topDirectory = '/heredoc';
$pathToExternals = array('php' => '/heredoc');
/RPC /usr/local/bin/curl execute2=/usr/bin/php84
EXAMPLE;
$example2 = "$" . 'scgi_host = "literal";';
$long = 'LONG_STRING_PLACEHOLDER';
$pathToExternals = ['php' => '/usr/bin/php74', 'curl' => '/usr/local/bin/curl', 'ffmpeg' => '', 'unrar' => '', 'sox' => '', 'mediainfo' => ''];
$pathToExternals['php'] = '/usr/bin/php';
$scgi_host =
  'operator host';
$scgi_port =
  5050;
$topDirectory = '/later'; // keep this operator comment
$forbidUserSettings = false;
$enableCSRFCheck = false;
PHP
  # A 100k string defeats regex backtracking limits in the previous parser.
  "$php_bin" -r '$p=$argv[1];$s=file_get_contents($p);file_put_contents($p,str_replace("LONG_STRING_PLACEHOLDER",str_repeat("x",100000),$s));' "$actual"
  chmod 0660 "$actual"
  chown 991:991 "$actual"
  before_inode=$(stat -c %i "$actual")
  expected_example=$(php_value example "$actual")
  HTTP_AUTH=true migrate "$workspace/missing.rc" "$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label syntax"
  [ "$(php_value example "$actual")" = "$expected_example" ] || fail "$label heredoc preserved"
  [ "$(php_value unrelated "$actual")" = '"/RPC /usr/local/bin/curl /usr/bin/php84"' ] || fail "$label unrelated string"
  [ "$(php_value long "$actual" | wc -c)" -gt 100000 ] || fail "$label long literal preserved"
  [ "$(php_value topDirectory "$actual")" = '"/data/downloads/"' ] || fail "$label topDirectory"
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label last host assignment"
  [ "$(php_value scgi_port "$actual")" = 0 ] || fail "$label last port assignment"
  [ "$(php_value XMLRPCMountPoint "$actual")" = '"/RPC2"' ] || fail "$label RPC mount"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label profiles disabled with auth on"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label CSRF enabled"
  [ "$(stat -c '%u:%g:%a' "$actual")" = 991:991:660 ] || fail "$label ownership and mode"
  [ "$before_inode" != "$(stat -c %i "$actual")" ] || fail "$label atomic inode"
  for key in php curl ffmpeg unrar sox mediainfo; do
    [ "$(external "$key" "$actual")" = "$(image_external "$key")" ] || fail "$label external $key"
  done
  [ "$(external dumptorrent "$actual")" = '<absent>' ] || fail "$label missing dumptorrent not invented"
  HTTP_AUTH=true assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  grep -Fq 'keep this operator comment' "$actual" || fail "$label trailing comment"
  printf 'ok - %s\n' "$label"
}

run_php_array_forms() {
  label='PHP balanced array delimiters and direct assignments'
  actual="$workspace/array-form.php"
  for style in uppercase separated multiline short; do
    cp "$fixtures/php-config.input.php" "$actual"
    case "$style" in
      uppercase) printf '%s\n' '$pathToExternals = Array("php" => "/usr/bin/php84");' >>"$actual" ;;
      separated) printf '%s\n' '$pathToExternals = array("php" => "/usr/bin/php84") ;' >>"$actual" ;;
      multiline) printf '%s\n' '$pathToExternals = array("php" => "/usr/bin/php84")' ';' >>"$actual" ;;
      short) printf '%s\n' '$pathToExternals = ["php" => "/usr/bin/php84"];' >>"$actual" ;;
    esac
    printf '%s\n' '$localhosts = array("localhost");' '$enabledOrigins = array();' >>"$actual"
    migrate "$workspace/missing.rc" "$actual"
    [ "$(external php "$actual")" = "$php_path" ] || fail "$label $style PHP path"
    [ "$(external curl "$actual")" = /usr/bin/curl ] || fail "$label $style curl path"
    [ "$(php_value localhosts "$actual")" = '["localhost"]' ] || fail "$label $style neighbor array"
    [ "$(php_value enabledOrigins "$actual")" = '[]' ] || fail "$label $style empty neighbor array"
    assert_idempotent "$label $style" "$workspace/missing.rc" "$actual" "$actual"
  done
  cp "$fixtures/php-config.input.php" "$actual"
  cat >>"$actual" <<'PHP'
$pathToExternals = [];
$pathToExternals['php'] = '/usr/bin/php84';
$pathToExternals['curl'] = '/operator/curl-wrapper';
$pathToExternals['ffmpeg'] = false ? '/usr/bin/ffmpeg' : '/operator/ffmpeg-wrapper';
$pathToExternals['unrar'] = '/operator/unrar-wrapper';
$pathToExternals['sox'] = '/operator/sox-wrapper';
$pathToExternals['mediainfo'] = '/operator/mediainfo-wrapper';
$pathToExternals['gzip'] = '/usr/bin/gzip';
$pathToExternals['stat'] = '/usr/bin/stat';
PHP
  migrate "$workspace/missing.rc" "$actual"
  [ "$(external php "$actual")" = "$php_path" ] || fail "$label direct PHP default"
  for key in curl ffmpeg unrar sox mediainfo; do
    [ "$(external "$key" "$actual")" = "/operator/$key-wrapper" ] || fail "$label direct operator $key wrapper"
  done
  [ "$(external gzip "$actual")" = '' ] || fail "$label unavailable old gzip default"
  [ "$(external stat "$actual")" = '' ] || fail "$label unavailable old stat default"
  assert_idempotent "$label direct keys" "$workspace/missing.rc" "$actual" "$actual"
  printf 'ok - %s\n' "$label"
}

run_php_environment() {
  label='PHP literal environment escaping'
  actual="$workspace/environment.php"
  cp "$fixtures/php-config.input.php" "$actual"
  DOWNLOAD_DIRECTORY="/data/operator 'quoted' path" RU_SCGI_HOST='host"with\quotes' RU_SCGI_PORT=005001 HTTP_AUTH=false migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value topDirectory "$actual")" = '"/data/operator '\''quoted'\'' path/"' ] || fail "$label download literal"
  [ "$(php_value scgi_host "$actual")" = '"host\"with\\quotes"' ] || fail "$label host literal"
  [ "$(php_value scgi_port "$actual")" = 5001 ] || fail "$label decimal port"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label profiles disabled auth off"
  DOWNLOAD_DIRECTORY="/data/operator 'quoted' path" RU_SCGI_HOST='host"with\quotes' RU_SCGI_PORT=005001 HTTP_AUTH=false assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  printf 'ok - %s\n' "$label"
  for port in invalid '1;echo 123;' 70000 -1; do
    cp "$fixtures/php-config.input.php" "$actual"
    before_hash=$(hash_of "$actual")
    if RU_SCGI_PORT="$port" /usr/local/bin/update-config "$workspace/missing.rc" "$actual" >"$workspace/output" 2>"$workspace/error"; then fail "invalid PHP port $port accepted"; fi
    [ "$before_hash" = "$(hash_of "$actual")" ] || fail 'invalid PHP port atomic refusal'
    assert_clean "$label"
  done
  printf 'ok - invalid PHP port rejected atomically\n'
}

run_php_refusals() {
  label='PHP syntax and lint failure'
  actual="$workspace/invalid.php"
  printf '%s\n' '<?php' '$topDirectory = /broken/syntax;' >"$actual"
  before_hash=$(hash_of "$actual")
  if /usr/local/bin/update-config "$workspace/missing.rc" "$actual" >"$workspace/output" 2>"$workspace/error"; then fail 'invalid PHP syntax accepted'; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail 'invalid PHP syntax changed original'
  grep -Fq "$actual:2: Invalid ruTorrent PHP configuration" "$workspace/error" || fail "PHP parse error file and line"
  assert_clean "$label"
  # Fail the output linter while leaving the token transform executable.
  shadow_bin="$workspace/lint-bin"
  mkdir "$shadow_bin"
  printf '#!/bin/sh\nfor option in "$@"; do if [ "$option" = -l ]; then echo "injected lint failure" >&2; exit 1; fi; done\nexec %s "$@"\n' "$php_path" >"$shadow_bin/$php_bin"
  chmod 0755 "$shadow_bin/$php_bin"
  cp "$fixtures/php-config.input.php" "$actual"
  before_hash=$(hash_of "$actual")
  if PATH="$shadow_bin:$PATH" /usr/local/bin/update-config "$workspace/missing.rc" "$actual" >"$workspace/output" 2>"$workspace/error"; then fail 'PHP linter failure accepted'; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail 'PHP linter failure changed original'
  grep -Fq 'lint' "$workspace/error" || { cat "$workspace/error" >&2; fail 'PHP linter failure diagnostic'; }
  assert_clean "$label"
  printf 'ok - %s\n' "$label"
}

run_php_local_policy() {
  label='managed policy after config.local.php'
  mkdir "$workspace/local-policy"
  actual="$workspace/local-policy/config.php"
  cp "$fixtures/php-config.input.php" "$actual"
  printf '%s\n' 'include __DIR__ . "/config.local.php";' >>"$actual"
  printf '%s\n' '<?php $forbidUserSettings = false; $enableCSRFCheck = false; $topDirectory="/local/";' \
    '$pathToExternals = ["curl" => "/operator/curl-wrapper"];' >"$workspace/local-policy/config.local.php"
  migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label forbid"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label CSRF"
  [ "$(php_value topDirectory "$actual")" = '"/local/"' ] || fail "$label allowed local override"
  [ "$(external curl "$actual")" = /operator/curl-wrapper ] || fail "$label local external wrapper"
  [ "$(external php "$actual")" = "$php_path" ] || fail "$label missing external after local include"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  printf 'ok - %s\n' "$label"
}
run_php_closing_tag() {
  label='PHP compact closing tag implicit semicolon'
  actual="$workspace/closing-tag.php"
  printf '%s' '<?php $topDirectory="/operator/" ?>' >"$actual"
  migrate "$workspace/missing.rc" "$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label lint"
  [ "$(php_value topDirectory "$actual")" = '"/data/downloads/"' ] || fail "$label effective directory"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label effective policy"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  # Existing canonical values need only the managed suffix added after ?>.
  sed -e '/^\/\/ docker-rutorrent: managed defaults begin/,$d' -e '/^\/\/ docker-rutorrent: managed policy begin/,$d' \
    "$actual" >"$workspace/closed-policy.php"
  actual="$workspace/closed-policy.php"
  printf '%s' '?>' >>"$actual"
  migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value topDirectory "$actual")" = '"/data/downloads/"' ] || fail "$label closed policy output"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label closed policy CSRF"
  assert_idempotent "$label closed policy" "$workspace/missing.rc" "$actual" "$actual"
  printf 'ok - %s\n' "$label"
}
run_php_closing_tag
run_php_external_override
run_php_syntax_cases
run_php_array_forms
run_php_environment
run_php_refusals
run_php_local_policy

run_php_policy_override() {
  label='explicit per-user settings policy override'
  actual="$workspace/profile-override.php"
  cp "$fixtures/php-config.input.php" "$actual"
  RU_FORBID_USER_SETTINGS=false migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value forbidUserSettings "$actual")" = false ] || fail "$label effective override"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label CSRF remains enabled"
  RU_FORBID_USER_SETTINGS=false assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  RU_FORBID_USER_SETTINGS=true migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label restore shared profile"
  before_hash=$(hash_of "$actual")
  if RU_FORBID_USER_SETTINGS=invalid /usr/local/bin/update-config "$workspace/missing.rc" "$actual" >"$workspace/output" 2>"$workspace/error"; then fail 'invalid profile policy accepted'; fi
  [ "$before_hash" = "$(hash_of "$actual")" ] || fail 'invalid profile policy changed original'
  printf 'ok - %s\n' "$label"
}
run_php_closed_newlines() {
  for blank_count in 1 2 3; do
    label="closed PHP tag with $blank_count newlines"
    actual="$workspace/closed-newlines.php"
    printf '%s' '<?php $topDirectory="/operator/" ?>' >"$actual"
    count=0
    while [ "$count" -lt "$blank_count" ]; do printf '\n' >>"$actual"; count=$((count + 1)); done
    migrate "$workspace/missing.rc" "$actual"
    assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
    printf 'ok - %s\n' "$label"
  done
}
run_php_policy_override
run_php_closed_newlines

run_php_scope_and_final_array() {
  label='PHP scope and final external array'
  actual="$workspace/php-scope.php"
  cp "$fixtures/php-config.input.php" "$actual"
  cat >>"$actual" <<'PHP'
function operator_local_config() {
    $topDirectory = '/operator/local';
    $scgi_port = 42;
    $pathToExternals = ['php' => '/operator/local-wrapper'];
    return [$topDirectory, $scgi_port, $pathToExternals['php']];
}
$pathToExternals = ['php' => '/operator/first-wrapper', 'curl' => '/operator/first-curl'];
$pathToExternals = ['php' => '/usr/bin/php84', 'curl' => '/operator/final-curl'];
PHP
  migrate "$workspace/missing.rc" "$actual"
  [ "$(external php "$actual")" = "$php_path" ] || fail "$label final PHP default"
  [ "$(external curl "$actual")" = /operator/final-curl ] || fail "$label final curl wrapper"
  observed=$("$php_bin" -r 'include $argv[1]; echo json_encode(operator_local_config(), JSON_UNESCAPED_SLASHES);' "$actual")
  [ "$observed" = '["/operator/local",42,"/operator/local-wrapper"]' ] || fail "$label function-local settings preserved"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  for invalid_directory in relative /data/line'
'break; do
    cp "$fixtures/php-config.input.php" "$actual"
    before_hash=$(hash_of "$actual")
    if DOWNLOAD_DIRECTORY="$invalid_directory" /usr/local/bin/update-config "$workspace/missing.rc" "$actual" >"$workspace/output" 2>"$workspace/error"; then fail "$label invalid directory accepted"; fi
    grep -Fq 'DOWNLOAD_DIRECTORY' "$workspace/error" || fail "$label directory diagnostic"
    [ "$before_hash" = "$(hash_of "$actual")" ] || fail "$label invalid directory preservation"
    assert_clean "$label invalid directory"
  done
  printf 'ok - %s\n' "$label"
}
run_php_scope_and_final_array

run_php_unproven_externals() {
  # Only a literal array with literal keys is rewritten; other forms keep their
  # value and missing image defaults never replace an operator wrapper.
  wrapper=/operator/php-wrapper
  curl_wrapper=/operator/curl-wrapper
  check_operator_externals 'parenthesised short array' php="$wrapper" <<'PHP'
$pathToExternals = (['php' => '/operator/php-wrapper']);
PHP
  check_operator_externals 'parenthesised long array' php="$wrapper" <<'PHP'
$pathToExternals = (array('php' => '/operator/php-wrapper'));
PHP
  check_operator_externals 'array spread' php="$wrapper" curl="$curl_wrapper" <<'PHP'
$operatorExternals = ['php' => '/operator/php-wrapper', 'curl' => '/operator/curl-wrapper'];
$pathToExternals = [...$operatorExternals];
PHP
  check_operator_externals 'array union' php="$wrapper" curl="$curl_wrapper" <<'PHP'
$pathToExternals = ['php' => '/operator/php-wrapper'] + ['curl' => '/operator/curl-wrapper'];
PHP
  check_operator_externals 'computed array key' php="$wrapper" <<'PHP'
$pathToExternals = ['ph' . 'p' => '/operator/php-wrapper'];
PHP
  check_operator_externals 'numeric string keys' php="$wrapper" 0=/operator/zero 1=/operator/one <<'PHP'
$pathToExternals = ['0' => '/operator/zero', 'php' => '/operator/php-wrapper'];
$pathToExternals['1'] = '/operator/one';
PHP
  check_operator_externals 'later union assignment' curl="$curl_wrapper" <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php'];
$pathToExternals += ['curl' => '/operator/curl-wrapper'];
PHP
  check_operator_externals 'later coalescing assignment' curl="$curl_wrapper" <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php'];
$pathToExternals['curl'] ??= '/operator/curl-wrapper';
PHP
  check_operator_externals 'array in top-level condition' php="$wrapper" curl="$curl_wrapper" <<'PHP'
if (true) {
    $pathToExternals = ['php' => '/operator/php-wrapper', 'curl' => '/operator/curl-wrapper'];
}
PHP
  check_operator_externals 'key in top-level condition' curl="$curl_wrapper" <<'PHP'
if (true) {
    $pathToExternals['curl'] = '/operator/curl-wrapper';
}
PHP
  # An attribute's #[ is closed by a plain ]; later file-scope values stay managed.
  check_operator_externals 'attributes' curl="$curl_wrapper" <<'PHP'
$operatorHook = #[OperatorNote] fn() => 1;
#[OperatorNote]
function operator_attributed() { return 1; }
$XMLRPCMountPoint = '/RPC';
$pathToExternals = ['php' => '/usr/bin/php84', 'curl' => '/operator/curl-wrapper'];
PHP
  [ "$(php_value XMLRPCMountPoint "$actual")" = '"/RPC2"' ] || fail "$label RPC mount"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label policy"
  printf 'ok - PHP external expressions, conditions and attributes keep operator values\n'
}
run_php_unproven_externals

run_php_coalescing_candidates() {
  # ??= keeps an earlier non-null value, so a stale literal before it stays effective.
  check_operator_externals 'stale PHP before same-key coalescing' <<'PHP'
$pathToExternals = [];
$pathToExternals['php'] = '/usr/bin/php84';
$pathToExternals['php'] ??= '/operator/php-wrapper';
PHP
  grep -Fq "??= '/operator/php-wrapper';" "$actual" || fail "$label coalescing wrapper text"
  check_operator_externals 'stale curl before same-key coalescing' <<'PHP'
$pathToExternals = [];
$pathToExternals['curl'] = '/usr/local/bin/curl';
$pathToExternals['curl'] ??= '/operator/curl-wrapper';
PHP
  check_operator_externals 'stale array entry before coalescing' <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php84'];
$pathToExternals['php'] ??= '/operator/php-wrapper';
PHP
  check_operator_externals 'wrapper before stale coalescing' php=/operator/php-wrapper <<'PHP'
$pathToExternals = [];
$pathToExternals['php'] = '/operator/php-wrapper';
$pathToExternals['php'] ??= '/usr/bin/php84';
PHP
  check_operator_externals 'stale PHP after coalescing' <<'PHP'
$pathToExternals = [];
$pathToExternals['php'] ??= '/operator/php-wrapper';
$pathToExternals['php'] = '/usr/bin/php84';
PHP
  printf 'ok - PHP same-key coalescing keeps earlier stale literals migrated\n'
}
run_php_coalescing_candidates

run_php_runtime_defaults() {
  # Static presence is not runtime presence: config.local.php and conditions
  # may replace or skip any write, so every image default is a final ??=.
  operator_local="<?php \$pathToExternals = ['curl' => '/operator/curl-wrapper'];"
  check_operator_externals 'literal base replaced in config.local.php' curl=/operator/curl-wrapper <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php', 'curl' => '/usr/bin/curl'];
include __DIR__ . '/config.local.php';
PHP
  operator_local=
  check_operator_externals 'literal base replaced in taken condition' curl=/operator/curl-wrapper <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php', 'curl' => '/usr/bin/curl'];
if (true) {
    $pathToExternals = ['curl' => '/operator/curl-wrapper'];
}
PHP
  check_operator_externals 'untaken braceless key' <<'PHP'
$pathToExternals = [];
if (false) $pathToExternals['curl'] = '/operator/curl-wrapper';
PHP
  # Operator code appended after the blocks moves them after it; an edited block is operator code.
  label='operator union after the managed defaults'
  printf '%s\n' "\$pathToExternals += ['curl' => '/operator/curl-wrapper'];" >>"$actual"
  migrate "$workspace/missing.rc" "$actual"
  assert_operator_externals "$label" curl=/operator/curl-wrapper
  [ "$(grep -c '^// docker-rutorrent: managed defaults begin$' "$actual")" = 1 ] || fail "$label one defaults block"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  label='edited managed defaults block'
  "$php_bin" -r '$p=$argv[1];$s=file_get_contents($p);file_put_contents($p,str_replace("// docker-rutorrent: managed defaults begin\n","// docker-rutorrent: managed defaults begin\n\$operatorNote = 1;\n",$s));' "$actual"
  migrate "$workspace/missing.rc" "$actual"
  [ "$(php_value operatorNote "$actual")" = 1 ] || fail "$label operator statement kept"
  [ "$(grep -c '^// docker-rutorrent: managed defaults begin$' "$actual")" = 2 ] || fail "$label regenerated block"
  assert_operator_externals "$label" curl=/operator/curl-wrapper
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
  check_operator_externals 'untaken braceless array' <<'PHP'
if (false) $pathToExternals = ['php' => '/operator/php-wrapper', 'curl' => '/operator/curl-wrapper'];
PHP
  label='stock config with config.local.php settings'
  operator_base=stock
  operator_local="<?php \$topDirectory = '/data/downloads/narrow/'; \$pathToExternals = ['curl' => '/operator/curl-wrapper'];"
  check_operator_externals "$label" curl=/operator/curl-wrapper </dev/null
  [ "$(php_value topDirectory "$actual")" = '"/data/downloads/narrow/"' ] || fail "$label narrower topDirectory"
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label scgi_host"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label CSRF policy"
  # Scalars config.php does not set are defaults, not overrides of config.local.php.
  label='include-only config keeps config.local.php settings'
  operator_base=bare
  operator_local="<?php \$topDirectory = '/data/downloads/narrow/'; \$scgi_host = 'local-scgi'; \$scgi_port = 1234; \$forbidUserSettings = false; \$enableCSRFCheck = false;"
  check_operator_externals "$label" <<'PHP'
include __DIR__ . '/config.local.php';
PHP
  [ "$(php_value topDirectory "$actual")" = '"/data/downloads/narrow/"' ] || fail "$label topDirectory"
  [ "$(php_value scgi_host "$actual")" = '"local-scgi"' ] || fail "$label scgi_host"
  [ "$(php_value scgi_port "$actual")" = 1234 ] || fail "$label scgi_port"
  [ "$(php_value forbidUserSettings "$actual")" = true ] || fail "$label profile policy"
  [ "$(php_value enableCSRFCheck "$actual")" = true ] || fail "$label CSRF policy"
  for case_name in 'include-only config without local settings' 'untaken scalar assignments'; do
    label=$case_name
    if [ "$label" = 'untaken scalar assignments' ]; then
      operator_local=
      operator_code="if (false) \$topDirectory = '/operator/'; if (false) \$scgi_host = '127.0.0.1'; if (false) \$scgi_port = 5000;"
    else
      operator_local='<?php $httpTimeOut = 30;'
      operator_code="include __DIR__ . '/config.local.php';"
    fi
    check_operator_externals "$label" <<PHP
$operator_code
PHP
    [ "$(php_value topDirectory "$actual")" = '"/data/downloads/"' ] || fail "$label topDirectory"
    [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label scgi_host"
    [ "$(php_value scgi_port "$actual")" = 0 ] || fail "$label scgi_port"
  done
  operator_base=
  printf 'ok - PHP image defaults fill only values still unset at runtime\n'
}
run_php_runtime_defaults

run_php_assignment_targets() {
  # Properties, variable variables and arrow-function locals are not the globals.
  label='static property and arrow-function targets'
  operator_base=bare
  check_operator_externals "$label" <<'PHP'
class OperatorConfig { public static $scgi_host = 'class-host'; public static $topDirectory = '/class/'; public static $pathToExternals = []; }
OperatorConfig::$scgi_host = 'operator-property';
OperatorConfig::$topDirectory = '/operator/property/';
OperatorConfig::$pathToExternals['php'] = '/usr/bin/php84';
$operatorHook = fn() => 1;
fn() => $scgi_port = 42;
PHP
  observed=$("$php_bin" -r 'include $argv[1]; echo json_encode([OperatorConfig::$scgi_host, OperatorConfig::$topDirectory, OperatorConfig::$pathToExternals, $scgi_host, $topDirectory, $scgi_port], JSON_UNESCAPED_SLASHES);' "$actual")
  [ "$observed" = '["operator-property","/operator/property/",{"php":"/usr/bin/php84"},"unix:///run/rtorrent/rtorrent.sock","/data/downloads/",0]' ] || fail "$label values $observed"
  label='dynamic property and variable variable targets'
  check_operator_externals "$label" <<'PHP'
$scgi_host = 'operator-host';
$topDirectory = '/operator/';
$operatorObject = new stdClass;
$operatorObject->$scgi_host = 'operator-property';
$$topDirectory = 'operator-variable';
PHP
  observed=$("$php_bin" -r 'include $argv[1]; echo json_encode([$operatorObject->$scgi_host, ${$topDirectory}, $scgi_host, $topDirectory], JSON_UNESCAPED_SLASHES);' "$actual")
  [ "$observed" = '["operator-property","operator-variable","unix:///run/rtorrent/rtorrent.sock","/data/downloads/"]' ] || fail "$label values $observed"
  operator_base=
  printf 'ok - PHP managed globals are distinguished from property and variable targets\n'
}
run_php_assignment_targets

run_php_string_escapes() {
  # Quoted keys and values are decoded as PHP decodes them, not as C strings.
  check_operator_externals 'Unicode escape in PHP key' <<'PHP'
$pathToExternals = [];
$pathToExternals["\u{70}hp"] = '/usr/bin/php84';
PHP
  check_operator_externals 'unknown escape kept in PHP value' 'php=/usr/bin/\php' <<'PHP'
$pathToExternals = ["php" => "/usr/bin/\php"];
PHP
  check_operator_externals 'binary string prefix' <<'PHP'
$pathToExternals = [b"php" => b'/usr/bin/php84', B'curl' => B"/usr/local/bin/curl"];
PHP
  check_operator_externals 'octal and hex escapes' <<'PHP'
$pathToExternals = ["\x63url" => "/usr/local/bin/\143url", "\160hp" => "\x2fusr/bin/php84"];
PHP
  check_operator_externals 'single-quoted escapes stay literal' '\u{70}hp=/usr/bin/php84' 'x\\y=/usr/bin/php84' <<'PHP'
$pathToExternals = ['\u{70}hp' => '/usr/bin/php84', 'x\\\y' => '/usr/bin/php84', 'php' => '/usr/bin/php84'];
PHP
  printf 'ok - PHP string escapes decoded as PHP literals\n'
}
run_php_string_escapes

run_php_conditional_candidates() {
  # A later syntactic write need not run; earlier literal defaults remain candidates.
  check_operator_externals 'stale array before untaken key assignment' <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php84'];
if (false) $pathToExternals['php'] = '/operator/php-wrapper';
PHP
  check_operator_externals 'stale key before untaken key assignment' <<'PHP'
$pathToExternals = [];
$pathToExternals['php'] = '/usr/bin/php84';
if (false) $pathToExternals['php'] = '/operator/php-wrapper';
PHP
  check_operator_externals 'stale array before untaken whole array' <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php84'];
if (false) $pathToExternals = ['php' => '/operator/php-wrapper'];
PHP
  check_operator_externals 'taken key assignment keeps its wrapper' php=/operator/php-wrapper <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php84'];
if (true) $pathToExternals['php'] = '/operator/php-wrapper';
PHP
  check_operator_externals 'taken else keeps its wrapper' php=/operator/php-wrapper <<'PHP'
$pathToExternals = ['php' => '/usr/bin/php84'];
if (false) $pathToExternals['php'] = '/operator/other-wrapper';
else $pathToExternals['php'] = '/operator/php-wrapper';
PHP
  printf 'ok - PHP conditional writes retain earlier effective migration candidates\n'
}
run_php_conditional_candidates

run_php_arrow_boundaries() {
  check_operator_externals 'global external in sibling arrow branch' <<'PHP'
false ? fn() => 1 : $pathToExternals = ['php' => '/usr/bin/php84'];
PHP
  check_operator_externals 'global scalar in sibling arrow branch' <<'PHP'
false ? fn() => 1 : $scgi_host = '127.0.0.1';
PHP
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label effective host"
  check_operator_externals 'nested typed arrow ends before sibling branch' <<'PHP'
false ? fn() => fn(): int => $scgi_port = 42 : $pathToExternals = ['php' => '/usr/bin/php84'];
PHP
  grep -Fq '$scgi_port = 42' "$actual" || fail "$label local assignment preserved"
  [ "$(php_value scgi_port "$actual")" = 0 ] || fail "$label global port"
  check_operator_externals 'arrow ternary remains local' <<'PHP'
fn() => false ? $scgi_host = 'arrow-host' : $scgi_port = 42;
PHP
  grep -Fq "\$scgi_host = 'arrow-host' : \$scgi_port = 42" "$actual" || fail "$label local branches preserved"
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label global host"
  [ "$(php_value scgi_port "$actual")" = 0 ] || fail "$label global port"
  printf 'ok - PHP arrow scope ends before outer sibling branches\n'
}
run_php_arrow_boundaries

run_php_binary_interpolation() {
  check_operator_externals 'binary-prefixed simple interpolation' <<'PHP'
$operatorName = 'operator';
$binarySimple = b"$operatorName";
$scgi_host = '127.0.0.1';
$pathToExternals = ['php' => '/usr/bin/php84'];
PHP
  [ "$(php_value binarySimple "$actual")" = '"operator"' ] || fail "$label simple value"
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label following managed host"
  check_operator_externals 'binary-prefixed braced interpolation' <<'PHP'
$operatorName = 'operator';
$binaryBraced = B"{$operatorName}";
$scgi_host = '127.0.0.1';
$pathToExternals = ['php' => '/usr/bin/php84'];
PHP
  [ "$(php_value binaryBraced "$actual")" = '"operator"' ] || fail "$label braced value"
  [ "$(php_value scgi_host "$actual")" = '"unix:///run/rtorrent/rtorrent.sock"' ] || fail "$label following managed host"
  printf 'ok - PHP binary-prefixed interpolation remains source text\n'
}
run_php_binary_interpolation
