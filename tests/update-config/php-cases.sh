# Sourced by run.sh so all PHP cases share its cleanup and signal handlers.
image_external() {
  case "$1" in
    php) printf '%s\n' "$php_path" ;;
    unrar) printf '%s\n' /usr/local/bin/unrar ;;
    *) printf '/usr/bin/%s\n' "$1" ;;
  esac
}

# Append operator code from stdin; KEY=VALUE arguments name the externals it
# must keep, and every other image external must hold its default.
check_operator_externals() {
  label=$1
  shift
  actual="$workspace/operator-externals.php"
  cp "$fixtures/php-config.input.php" "$actual"
  cat >>"$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label fixture syntax"
  migrate "$workspace/missing.rc" "$actual"
  "$php_bin" -l "$actual" >/dev/null || fail "$label transformed syntax"
  for key in php curl ffmpeg unrar sox mediainfo; do
    case " $* " in *" $key="*) continue ;; esac
    [ "$(external "$key" "$actual")" = "$(image_external "$key")" ] || fail "$label default $key"
  done
  for pair in "$@"; do
    [ "$(external "${pair%%=*}" "$actual")" = "${pair#*=}" ] || fail "$label operator ${pair%%=*}"
  done
  assert_clean "$label"
  assert_idempotent "$label" "$workspace/missing.rc" "$actual" "$actual"
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
  # Existing canonical values need only the final policy suffix added after ?>.
  sed '/^\/\/ docker-rutorrent: managed policy begin/,$d' "$actual" >"$workspace/closed-policy.php"
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
