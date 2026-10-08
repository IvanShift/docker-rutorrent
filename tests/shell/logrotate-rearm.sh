#!/bin/sh
# A refusal is reported once per path and reason until that path is repaired;
# a repaired path resumes rotation and a later recurrence is reported again.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
awk '/^warn_once\(\)/ {copy=1} /^LOG_FILES=/ {copy=0} copy {print}' "$repo/rootfs/etc/s6.d/logrotate/run" > "$workspace/functions"
. "$workspace/functions"
service_uid=$(id -u)
refusals='|'
MAX_SIZE=4
# The fixed parent rule needs a root-owned sticky or private parent: use /tmp.
LOG_DIR=/tmp
ARCHIVE_DIR="$workspace/archive"
mkdir -m 0700 "$ARCHIVE_DIR"
log="$workspace/errors.log"
count() { grep -c "$1" "$workspace/stderr" || true; }

printf 'private sentinel\n' > "$workspace/sentinel"
ln -s "$workspace/sentinel" "$log"
for attempt in 1 2; do rotate_log "$log" 2>> "$workspace/stderr" && fail 'rotation admitted a source symlink'; done
[ "$(count "refusing $log reason=unsafe-file")" = 1 ] || fail 'first refusal is not reported exactly once'
rm "$log"
printf 'repaired-data\n' > "$log"
rotate_log "$log" 2>> "$workspace/stderr" || fail 'repaired log did not rotate'
[ "$(cat "$ARCHIVE_DIR/errors.log.1")" = repaired-data ] || fail 'repaired log was not archived'
[ "$(count "resumed $log after reason=unsafe-file")" = 1 ] || fail 'repair did not report resumed rotation'
ln -s "$workspace/sentinel" "$log"
rotate_log "$log" 2>> "$workspace/stderr" && fail 'rotation admitted a recurring source symlink'
[ "$(count "refusing $log reason=unsafe-file")" = 2 ] || fail 'recurring refusal after repair was silent'
[ "$(cat "$workspace/sentinel")" = 'private sentinel' ] || fail 'refused source target changed'
printf 'ok - a repaired log re-arms its refusal and a recurrence is reported again\n'

chmod 0755 "$ARCHIVE_DIR"
for attempt in 1 2; do prepare_archive 2>> "$workspace/stderr" && fail 'archive directory with group/other access admitted'; done
[ "$(count "refusing $ARCHIVE_DIR reason=unsafe-archive-directory")" = 1 ] || fail 'archive refusal is not reported exactly once'
chmod 0700 "$ARCHIVE_DIR"
prepare_archive 2>> "$workspace/stderr" || fail 'repaired archive directory refused'
[ "$(count "resumed $ARCHIVE_DIR after reason=unsafe-archive-directory")" = 1 ] || fail 'archive repair was not reported'
chmod 0755 "$ARCHIVE_DIR"
prepare_archive 2>> "$workspace/stderr" && fail 'recurring unsafe archive directory admitted'
[ "$(count "refusing $ARCHIVE_DIR reason=unsafe-archive-directory")" = 2 ] || fail 'recurring archive refusal was silent'
printf 'ok - a repaired archive directory re-arms its refusal\n'
