#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
cat > "$workspace/helper" <<'MOCK'
#!/bin/sh
[ "$1" = reopen ] || exit 70
case "$2" in nginx|php85) ;; *) exit 71 ;; esac
printf '%s\n' "$2" >> "$fixture/signals"
exit 1
MOCK
chmod 755 "$workspace/helper"
export fixture="$workspace"
awk '/^warn_once\(\)/ {copy=1} /^LOG_FILES=/ {copy=0} copy {print}' "$repo/rootfs/etc/s6.d/logrotate/run" \
  | sed "s|/usr/local/libexec/service-runtime|$workspace/helper|g" > "$workspace/reopen"
. "$workspace/reopen"
refusals='|'
for service in nginx php85 nginx; do
  reopen_log "$service" 2>> "$workspace/reopen-refusals" && fail "refused $service reopen reported success"
done
[ "$(cat "$workspace/signals")" = "$(printf 'nginx\nphp85\nnginx')" ] || fail 'logrotate delegated incorrect service identities'
[ "$(grep -c 'refusing nginx reason=reopen-refused' "$workspace/reopen-refusals")" = 1 ] || fail 'reopen refusals are not bounded'
printf 'ok - log rotation delegates fixed service names and survives safe pidfd refusal\n'
service_uid=$(id -u)
ARCHIVE_DIR="$workspace/archive"
MAX_SIZE=8
refusals='|'
mkdir -m 0700 "$ARCHIVE_DIR"
for basename in nginx-rutorrent-access.log nginx-rutorrent-error.log nginx-error.log php_error.log errors.log; do
  for generation in generation1 generation2 generation3; do
    printf '%s\n' "$generation" > "$workspace/$basename"
    rotate_log "$workspace/$basename" || fail "common rotation failed: $basename"
  done
  [ "$(cat "$ARCHIVE_DIR/$basename.1")" = generation3 ] || fail "current archive lost: $basename"
  [ "$(gzip -cd "$ARCHIVE_DIR/$basename.1.gz")" = generation2 ] || fail "compressed retention lost: $basename"
done
[ "$(find "$ARCHIVE_DIR" -type f -name '*.1*' | wc -l)" = 10 ] || fail 'common rotation retained more than two archives per log'
# A master-owned log records its owed reopen before the move; errors.log has no master.
[ "$(cd "$ARCHIVE_DIR" && echo *.reopen)" = 'nginx-error.log.reopen nginx-rutorrent-access.log.reopen nginx-rutorrent-error.log.reopen php_error.log.reopen' ] \
  || fail 'moved master logs did not record their pending reopen'
printf 'private sentinel\n' > "$workspace/sentinel"
ln -s "$workspace/sentinel" "$workspace/errors.log"
for attempt in 1 2 3; do rotate_log "$workspace/errors.log" 2>> "$workspace/refusals" && fail 'rotation admitted a source symlink'; done
[ -L "$workspace/errors.log" ] || fail 'refused source symlink was moved'
[ "$(cat "$workspace/sentinel")" = 'private sentinel' ] || fail 'refused source target changed'
[ "$(grep -c 'reason=unsafe-file' "$workspace/refusals")" = 1 ] || fail 'rotation refusals are not bounded'
printf 'ok - shared rotation retains exactly two archives for five logs and refuses unsafe sources once\n'

# A refused reopen stays pending until it succeeds. The test holds each log open
# like its master and reopens it only after the helper has signalled that master.
cat > "$workspace/helper" <<'MOCK'
#!/bin/sh
[ "$1" = reopen ] || exit 70
case "$2" in nginx|php85) ;; *) exit 71 ;; esac
printf '%s\n' "$2" >> "$fixture/attempts"
left=$(cat "$fixture/refusals-$2")
if [ "$left" -gt 0 ]; then
  printf '%s\n' "$((left - 1))" > "$fixture/refusals-$2"
  printf 'service-runtime: reopen-pidfile-unavailable\n' >&2
  exit 1
fi
: > "$fixture/reopened-$2"
MOCK
refusals='|'
nginx_moved=''
php_moved=''
MAX_SIZE=32
# The fixed parent rule needs a root-owned sticky or private parent: use /tmp.
LOG_DIR=/tmp
ARCHIVE_DIR="$workspace/reopen-archive"
mkdir -m 0700 "$ARCHIVE_DIR"
fpm_log="$workspace/php_error.log"
access_log="$workspace/nginx-rutorrent-access.log"
error_log="$workspace/nginx-error.log"
LOG_FILES="$access_log $error_log $fpm_log"
printf 1 > "$workspace/refusals-php85"
printf 2 > "$workspace/refusals-nginx"
attempts() { grep -c "^$1\$" "$workspace/attempts" || true; }
pass() {
  rotation_pass 2>> "$workspace/reopen-stderr"
  if [ -e "$workspace/reopened-php85" ]; then rm "$workspace/reopened-php85"; exec 3>> "$fpm_log"; fi
  if [ -e "$workspace/reopened-nginx" ]; then rm "$workspace/reopened-nginx"; exec 4>> "$access_log" 5>> "$error_log"; fi
}

exec 3>> "$fpm_log" 4>> "$access_log" 5>> "$error_log"
printf '%040d\n' 0 >&3
printf '%040d\n' 0 >&4
pass
[ ! -e "$fpm_log" ] && [ ! -e "$access_log" ] || fail 'logs over the threshold were not moved'
[ "$(attempts php85)" = 1 ] && [ "$(attempts nginx)" = 1 ] || fail 'moved logs did not request a reopen'
printf 'held-fpm\n' >&3
printf 'held-access\n' >&4
# Another writer recreates the active path while the moved log is still in use.
printf '%040d\n' 1 > "$access_log"
printf '%040d\n' 2 >&5
pass
[ "$(attempts php85)" = 2 ] || fail 'refused FPM reopen was not retried without a new active log'
[ -f "$fpm_log" ] || fail 'retried FPM reopen did not restore the active log'
fpm_archived=$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")
printf 'after-fpm-reopen\n' >&3
[ "$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")" = "$fpm_archived" ] || fail 'FPM archive still grows after the retried reopen'
grep -qx held-fpm "$ARCHIVE_DIR/php_error.log.1" || fail 'FPM write held during the refusal was lost'
grep -qx after-fpm-reopen "$fpm_log" || fail 'FPM writes did not return to the active log'
printf 'ok - a refused FPM reopen is retried until the active log returns and its archive stops growing\n'

[ "$(attempts nginx)" = 2 ] || fail 'refused nginx reopen was not retried'
[ ! -e "$ARCHIVE_DIR/nginx-rutorrent-access.log.1.gz" ] || fail 'archive in use was compressed while its reopen was pending'
[ "$(head -n 1 "$access_log")" = "$(printf '%040d' 1)" ] || fail 'recreated active log was moved over the archive in use'
[ -f "$ARCHIVE_DIR/nginx-error.log.1" ] || fail 'another log of the pending master stopped rotating'
printf 'held-access-2\n' >&4
grep -qx held-access-2 "$ARCHIVE_DIR/nginx-rutorrent-access.log.1" || fail 'write to the archive in use was lost'
pass
[ "$(attempts nginx)" = 3 ] || fail 'second refused nginx reopen was not retried'
printf 'after-access-reopen\n' >&4
grep -qx after-access-reopen "$access_log" || fail 'nginx writes did not return to the active log'
pass
[ "$(attempts nginx)" = 4 ] && [ "$(attempts php85)" = 2 ] || fail 'a successful reopen was repeated or a rotation did not reopen'
gzip -cd "$ARCHIVE_DIR/nginx-rutorrent-access.log.1.gz" | grep -qx held-access-2 || fail 'released archive was not compressed by the next rotation'
grep -qx after-access-reopen "$ARCHIVE_DIR/nginx-rutorrent-access.log.1" || fail 'recreated active log did not rotate after the reopen'
for master in nginx php85; do
  [ "$(grep -c "refusing $master reason=reopen-refused" "$workspace/reopen-stderr")" = 1 ] || fail "$master reopen refusal is not reported exactly once"
  [ "$(grep -c "resumed $master after reason=reopen-refused" "$workspace/reopen-stderr")" = 1 ] || fail "$master reopen recovery was not reported"
done
printf 'ok - an archive in use stays untouched until a retried nginx reopen succeeds\n'

# A restart loses the in-memory pending state; only the markers in the private
# archive directory survive it.
restart() { nginx_moved='' php_moved='' refusals='|'; }
restart
pass
[ "$(attempts nginx)" = 4 ] && [ "$(attempts php85)" = 2 ] || fail 'restart without a pending reopen signalled a master'
printf 1 > "$workspace/refusals-php85"
printf '%040d\n' 3 >&3
pass
[ ! -e "$fpm_log" ] && [ "$(attempts php85)" = 3 ] || fail 'refused FPM reopen before the restart was not recorded'
restart
printf 'held-across-restart\n' >&3
pass
[ "$(attempts php85)" = 4 ] && [ "$(attempts nginx)" = 4 ] || fail 'restart lost the pending FPM reopen'
[ -f "$fpm_log" ] || fail 'reopen after the restart did not restore the active log'
fpm_archived=$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")
printf 'after-restart-reopen\n' >&3
[ "$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")" = "$fpm_archived" ] || fail 'FPM archive still grows after the restarted service reopened it'
grep -qx held-across-restart "$ARCHIVE_DIR/php_error.log.1" || fail 'write held across the restart was lost'
[ ! -e "$ARCHIVE_DIR/php_error.log.reopen" ] || fail 'a completed reopen left its pending marker'
printf 'ok - a restarted service completes a pending reopen and requests none without one\n'

# The archive directory is refused when the service restarts and repaired later:
# the pending reopen is still owed and the first admitted pass requests it.
base=$(attempts php85)
printf 1 > "$workspace/refusals-php85"
printf '%040d\n' 4 >&3
pass
[ ! -e "$fpm_log" ] && [ "$(attempts php85)" = $((base + 1)) ] || fail 'refused FPM reopen before the refused restart was not recorded'
chmod 0755 "$ARCHIVE_DIR"
restart
pass
pass
[ "$(attempts php85)" = $((base + 1)) ] || fail 'a refused archive directory admitted its pending state'
printf 'held-during-repair\n' >&3
chmod 0700 "$ARCHIVE_DIR"
pass
[ "$(attempts php85)" = $((base + 2)) ] || fail 'repaired archive directory did not resume the pending FPM reopen'
[ -f "$fpm_log" ] || fail 'reopen after the repair did not restore the active log'
fpm_archived=$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")
printf 'after-repair-reopen\n' >&3
[ "$(stat -c %s "$ARCHIVE_DIR/php_error.log.1")" = "$fpm_archived" ] || fail 'FPM archive still grows after the repaired service reopened it'
grep -qx held-during-repair "$ARCHIVE_DIR/php_error.log.1" || fail 'write held during the repair was lost'
[ "$(grep -c "refusing $ARCHIVE_DIR reason=unsafe-archive-directory" "$workspace/reopen-stderr")" = 1 ] || fail 'refused archive directory is not reported once'
[ "$(grep -c "resumed $ARCHIVE_DIR after reason=unsafe-archive-directory" "$workspace/reopen-stderr")" = 1 ] || fail 'repaired archive directory was not reported'
printf 'ok - a pending reopen survives a restart with a refused archive directory and resumes after its repair\n'

# Another writer recreates the active path before the restart. The moved archive
# is still in use, so it is neither compressed nor replaced until the reopen.
base=$(attempts php85)
printf 2 > "$workspace/refusals-php85"
printf '%040d\n' 5 >&3
pass
[ ! -e "$fpm_log" ] && [ "$(attempts php85)" = $((base + 1)) ] || fail 'refused FPM reopen before the recreating restart was not recorded'
printf '%040d\n' 6 > "$fpm_log"
restart
pass
[ "$(attempts php85)" = $((base + 2)) ] || fail 'restart with a recreated active log lost the pending FPM reopen'
[ "$(stat -Lc %h "/proc/$$/fd/3")" = 1 ] || fail 'archive in use was unlinked after the restart'
printf 'held-recreated\n' >&3
grep -qx held-recreated "$ARCHIVE_DIR/php_error.log.1" || fail 'archive in use was replaced after the restart'
[ "$(head -n 1 "$fpm_log")" = "$(printf '%040d' 6)" ] || fail 'recreated active log was moved over the archive in use'
pass
[ "$(attempts php85)" = $((base + 3)) ] || fail 'retried FPM reopen after the recreating restart did not happen'
printf 'after-recreated-reopen\n' >&3
pass
[ "$(attempts php85)" = $((base + 4)) ] || fail 'released archive did not rotate with the recreated active log'
gzip -cd "$ARCHIVE_DIR/php_error.log.1.gz" | grep -qx held-recreated || fail 'write held across the recreating restart was lost'
grep -qx after-recreated-reopen "$ARCHIVE_DIR/php_error.log.1" || fail 'recreated active log did not rotate after the reopen'
printf 'ok - an archive in use stays protected across a restart that finds its active log recreated\n'

# Without a marker no reopen is owed, even beside an archive whose active log is missing.
base=$(attempts php85)
nginx_base=$(attempts nginx)
rm "$fpm_log"
restart
pass
pass
[ "$(attempts php85)" = "$base" ] && [ "$(attempts nginx)" = "$nginx_base" ] || fail 'a start without a pending marker signalled a master'
printf 'ok - a start without a recorded pending reopen signals no master\n'
