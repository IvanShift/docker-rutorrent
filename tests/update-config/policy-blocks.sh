#!/bin/sh
# Exercise the actual transform without executing operator PHP as root.
set -eu
helper=${1:-/usr/local/libexec/migrate-rutorrent-config.php}
workspace=$(mktemp -d)
# A signal ends the run; its exit then removes the scratch once, as in run.sh.
trap 'rm -rf "$workspace"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
php_bin=${PHP_BIN:-php85}
fail() { printf 'not ok - %s\n' "$1" >&2; exit 1; }
migrate() { "$php_bin" "$helper" "$workspace/config.php" >/dev/null; }
blocks() {
  "$php_bin" -r '$n=0;$depth=0;foreach(token_get_all(file_get_contents($argv[1])) as $t){if(is_array($t)){if($t[0]===T_COMMENT && $depth===0 && trim($t[1])==="// docker-rutorrent: managed policy begin")++$n;}elseif(in_array($t,["{","[","("]))++$depth;elseif(in_array($t,["}","]",")"]))--$depth;}echo $n;' "$workspace/config.php"
}
seed() { printf '%s\n' '<?php $operator = "preserved";' > "$workspace/config.php"; migrate; }
seed
printf '%s\n' '$following = "operator suffix";' >> "$workspace/config.php"
migrate
[ "$(blocks)" = 1 ] || fail 'operator suffix leaves duplicate managed block'
grep -Fq '$following = "operator suffix";' "$workspace/config.php" || fail 'operator suffix removed'
cp "$workspace/config.php" "$workspace/first.php"
migrate
cmp -s "$workspace/config.php" "$workspace/first.php" || fail 'operator suffix is not idempotent'
echo 'ok - token-managed interior block removed, following statement retained, idempotent'
seed
cat >> "$workspace/config.php" <<'PHP'
$literal = <<<'LITERAL'
// docker-rutorrent: managed policy begin
$forbidUserSettings = true;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
LITERAL;
/* // docker-rutorrent: managed policy begin
$forbidUserSettings = true;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end */
function operator_policy_example() {
$local = 'operator code before example';
// docker-rutorrent: managed policy begin
$forbidUserSettings = false;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
return $forbidUserSettings;
}
$following = 'tail';
PHP
migrate
[ "$(blocks)" = 1 ] || fail 'literal or function block confused with managed code'
grep -Fq 'return $forbidUserSettings;' "$workspace/config.php" || fail 'operator function lost'
[ "$(grep -c '^// docker-rutorrent: managed policy begin' "$workspace/config.php")" = 3 ] || fail 'literal or nested function marker removed'
cp "$workspace/config.php" "$workspace/first.php"
migrate
cmp -s "$workspace/config.php" "$workspace/first.php" || fail 'literal preservation is not idempotent'
echo 'ok - heredoc, comment and nested operator function retained'
seed
cat >> "$workspace/config.php" <<'PHP'
// docker-rutorrent: managed policy begin
$forbidUserSettings = true;
$extraOperator = 'inside custom block';
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
PHP
migrate
[ "$(blocks)" = 2 ] || fail 'noncanonical operator-marked block removed'
grep -Fq '$extraOperator = '\''inside custom block'\'';' "$workspace/config.php" || fail 'custom operator statement removed'
echo 'ok - noncanonical operator-marked block retained'
seed
printf '%s\n' '?>outside<?php $following = "reopened";' >> "$workspace/config.php"
RU_FORBID_USER_SETTINGS=false migrate
[ "$(blocks)" = 1 ] || fail 'closed/reopened PHP retains duplicate block'
grep -Fq '?>outside<?php $following = "reopened";' "$workspace/config.php" || fail 'operator tags/text changed'
cp "$workspace/config.php" "$workspace/first.php"
RU_FORBID_USER_SETTINGS=false migrate
cmp -s "$workspace/config.php" "$workspace/first.php" || fail 'closed/reopened PHP not idempotent'
echo 'ok - closed/reopened PHP preserved under selected profile policy'
seed
cat >> "$workspace/config.php" <<'PHP'
// docker-rutorrent: managed policy begin
$forbidUserSettings = false;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
// docker-rutorrent: managed policy begin
$forbidUserSettings = true;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
PHP
migrate
[ "$(blocks)" = 1 ] || fail 'adjacent managed blocks not removed'
"$php_bin" -l "$workspace/config.php" >/dev/null || fail 'adjacent removals overlap'
cp "$workspace/config.php" "$workspace/first.php"
migrate
cmp -s "$workspace/config.php" "$workspace/first.php" || fail 'adjacent blocks not idempotent'
echo 'ok - adjacent canonical blocks removed without overlapping edits'
seed
cat >> "$workspace/config.php" <<'PHP'
if (false)
// docker-rutorrent: managed policy begin
$forbidUserSettings = true;
$enableCSRFCheck = true;
// docker-rutorrent: managed policy end
$afterConditional = 'preserved';
PHP
migrate
[ "$(blocks)" = 2 ] || fail 'operator-controlled conditional block removed'
grep -Fq 'if (false)' "$workspace/config.php" || fail 'operator condition removed'
echo 'ok - conditional operator statement retained'
