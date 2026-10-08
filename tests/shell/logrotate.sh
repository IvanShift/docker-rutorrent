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
reopen_log nginx
reopen_log php85
[ "$(cat "$workspace/signals")" = "$(printf 'nginx\nphp85')" ] || fail 'logrotate delegated incorrect service identities'
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
[ "$(find "$ARCHIVE_DIR" -type f | wc -l)" = 10 ] || fail 'common rotation retained more than two archives per log'
printf 'private sentinel\n' > "$workspace/sentinel"
ln -s "$workspace/sentinel" "$workspace/errors.log"
for attempt in 1 2 3; do rotate_log "$workspace/errors.log" 2>> "$workspace/refusals" && fail 'rotation admitted a source symlink'; done
[ -L "$workspace/errors.log" ] || fail 'refused source symlink was moved'
[ "$(cat "$workspace/sentinel")" = 'private sentinel' ] || fail 'refused source target changed'
[ "$(grep -c 'reason=unsafe-file' "$workspace/refusals")" = 1 ] || fail 'rotation refusals are not bounded'
printf 'ok - shared rotation retains exactly two archives for five logs and refuses unsafe sources once\n'
