#!/bin/sh
# Run real descriptor traversal with deterministic race checkpoints.
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT HUP INT TERM
cc -std=c11 -O2 -Wall -Wextra -Werror "$repo/tests/shell/ownership-fixture.c" -o "$scratch/own-tree"
uid=$(id -u); gid=$(id -g)
mkdir -p "$scratch/outside" "$scratch/tree/victim"
printf untouched > "$scratch/outside/sentinel"
printf ordinary > "$scratch/tree/victim/file"
ln -s "$scratch/outside" "$scratch/tree/external"
export OWNERSHIP_TEST_OUTSIDE="$scratch/outside" OWNERSHIP_TEST_TREE="$scratch/tree" OWNERSHIP_TEST_VISITED="$scratch/visited"
OWNERSHIP_TEST_SCENARIO=swap "$scratch/own-tree" shared "$uid" "$gid" "$scratch/tree"
[ "$(cat "$scratch/outside/sentinel")" = untouched ]
[ -L "$scratch/tree/victim" ] && [ -f "$scratch/tree/held/file" ] && [ -f "$scratch/visited" ]
printf 'PASS descriptor directory replacement never reaches outside inode\n'
mkdir "$scratch/disappearing"
printf transient > "$scratch/disappearing/vanishing"
OWNERSHIP_TEST_SCENARIO=vanish "$scratch/own-tree" shared "$uid" "$gid" "$scratch/disappearing"
printf 'PASS disappearing shared file is skipped\n'
mkdir -m 0755 "$scratch/private"
printf private > "$scratch/private/vanishing"
status=0
OWNERSHIP_TEST_VISITOR_ONLY=1 OWNERSHIP_TEST_SCENARIO=vanish "$scratch/own-tree" session "$uid" "$gid" "$scratch/private" > "$scratch/refusal" 2>&1 || status=$?
[ "$status" = 78 ] || { cat "$scratch/refusal"; echo 'FAIL missing session record was not refused' >&2; exit 1; }
grep -F 'directory entry cannot be opened' "$scratch/refusal" >/dev/null
printf 'PASS disappearing private session record remains refused\n'
mkdir -p "$scratch/protected-root/protected"
printf private > "$scratch/protected-root/protected/file"
protected_inode=$(stat -c '%d:%i' "$scratch/protected-root/protected")
OWNERSHIP_TEST_SCENARIO=protected "$scratch/own-tree" shared "$uid" "$gid" "$scratch/protected-root" --exclude "$protected_inode"
OWNERSHIP_TEST_SCENARIO=protected "$scratch/own-tree" shared "$uid" "$gid" "$scratch/protected-root" --exclude-path "$scratch/protected-root/protected"
printf 'PASS identity and lexical protected exclusions both stop the sweep\n'
mkdir -m 0755 "$scratch/hardlink-session"
mkdir -m 0700 "$scratch/hardlink-session/.retrackers-migration"
printf private > "$scratch/hardlink-session/.retrackers-migration/state"
chmod 0600 "$scratch/hardlink-session/.retrackers-migration/state"
ln "$scratch/hardlink-session/.retrackers-migration/state" "$scratch/journal-alias"
status=0
OWNERSHIP_TEST_VISITOR_ONLY=1 "$scratch/own-tree" session "$uid" "$gid" "$scratch/hardlink-session" > "$scratch/refusal" 2>&1 || status=$?
[ "$status" = 78 ] || { cat "$scratch/refusal"; echo 'FAIL hardlinked private journal was not refused' >&2; exit 1; }
grep -F 'session file is not single-linked' "$scratch/refusal" >/dev/null
printf 'PASS private journal hardlink remains refused\n'
for operation in fstat chown readable readdir; do
  mkdir -p "$scratch/stale-$operation/child"
  chmod 0755 "$scratch/stale-$operation" "$scratch/stale-$operation/child"
  printf transient > "$scratch/stale-$operation/child/file"
  target="$scratch/stale-$operation/child"
  case "$operation" in fstat|chown) target=$target/file ;; esac
  export OWNERSHIP_TEST_STALE_INODE=$(stat -c '%i' "$target")
  OWNERSHIP_TEST_SCENARIO=stale-$operation "$scratch/own-tree" shared 4294967293 "$gid" "$scratch/stale-$operation" > "$scratch/fault" 2>&1
  printf 'PASS shared post-open stale %s is skipped\n' "$operation"
  status=0
  OWNERSHIP_TEST_VISITOR_ONLY=1 OWNERSHIP_TEST_SCENARIO=stale-$operation "$scratch/own-tree" session "$uid" "$gid" "$scratch/stale-$operation" > "$scratch/refusal" 2>&1 || status=$?
  # In the chown case select another GID to force the actual fchownat boundary.
  if [ "$operation" = chown ]; then
    status=0
    OWNERSHIP_TEST_VISITOR_ONLY=1 OWNERSHIP_TEST_SCENARIO=stale-$operation "$scratch/own-tree" session "$uid" 4294967293 "$scratch/stale-$operation" > "$scratch/refusal" 2>&1 || status=$?
  fi
  [ "$status" = 78 ] || { cat "$scratch/refusal"; echo "FAIL private stale $operation was not refused" >&2; exit 1; }
  case "$operation" in
    fstat) reason='opened inode cannot be inspected' ;;
    chown) reason='cannot assign service UID:GID' ;;
    readable) reason='cannot enumerate directory' ;;
    readdir) reason='directory enumeration failed' ;;
  esac
  grep -F "$reason" "$scratch/refusal" >/dev/null
  printf 'PASS private post-open stale %s remains refused\n' "$operation"
done
helper=$scratch/rutorrent-own-tree
cc -std=c11 -O2 -Wall -Wextra -Werror "$repo/rootfs/usr/local/libexec/ownership-walk.c" -o "$helper"
mkdir "$scratch/arguments"
# An option is recognized before its value is required: incomplete or
# malformed options are usage errors, never a crash.
while IFS= read -r options; do
  status=0
  # shellcheck disable=SC2086 # the table row is the option vector
  "$helper" shared "$uid" "$gid" "$scratch/arguments" $options > "$scratch/usage" 2>&1 || status=$?
  [ "$status" = 64 ] || { cat "$scratch/usage"; echo "FAIL ownership options '$options' exited $status, not 64" >&2; exit 1; }
done <<'EOF'
--access-uid
--access-gid
--exclude-path
--exclude
--preserve-root --access-gid
--access-uid 0 --exclude-path
--unknown
--unknown /value
--access-uid x
--access-gid 4294967295
--exclude-path relative
--exclude 1
--exclude 1:2x
--preserve-root extra
EOF
"$helper" shared "$uid" "$gid" "$scratch/arguments" --access-uid "$uid" --access-gid "$gid" --preserve-root \
  --exclude-path "$scratch/arguments/none" --exclude 0:0
printf 'PASS incomplete or malformed ownership options exit 64; complete options pass\n'
# Depth costs neither descriptors nor C stack; exhausting a tiny limit is
# reported as a descriptor limit, not as an ownership defect.
leaf=$scratch/deep; i=0
while [ "$i" -lt 600 ]; do leaf=$leaf/d; i=$((i + 1)); done
mkdir -p "$leaf"
printf leaf > "$leaf/file"
status=0
(ulimit -n 64 && "$helper" shared "$uid" "$gid" "$scratch/deep") > "$scratch/deep-output" 2>&1 || status=$?
[ "$status" = 0 ] || { cat "$scratch/deep-output"; echo "FAIL 600-level tree under 64 descriptors exited $status" >&2; exit 1; }
printf 'PASS 600-level tree walks within 64 descriptors\n'
status=0
(ulimit -n 8 && "$helper" shared "$uid" "$gid" "$scratch/deep") > "$scratch/deep-output" 2>&1 || status=$?
if [ "$status" != 78 ] || ! grep -F 'descriptor limit reached (EMFILE)' "$scratch/deep-output" >/dev/null; then
  cat "$scratch/deep-output"; echo "FAIL descriptor exhaustion exited $status without its diagnosis" >&2; exit 1
fi
printf 'PASS descriptor exhaustion is diagnosed as such\n'
# Two deep siblings below a deep stem: the stem's released levels must be
# reopened and re-identified before the second sibling is walked.
branches() {
  stem=$1; i=0
  while [ "$i" -lt 20 ]; do stem=$stem/d; i=$((i + 1)); done
  for branch in a b; do
    leaf=$stem/$branch; i=0
    while [ "$i" -lt 40 ]; do leaf=$leaf/d; i=$((i + 1)); done
    mkdir -p "$leaf"
    printf leaf > "$leaf/f"
  done
  find "$1" -type d -exec chmod 0755 {} +
  find "$1" -type f -exec chmod 0666 {} +
}
branches "$scratch/branches"
status=0
(ulimit -n 64 && OWNERSHIP_TEST_VISITOR_ONLY=1 "$scratch/own-tree" session "$uid" "$gid" "$scratch/branches") > "$scratch/refusal" 2>&1 || status=$?
[ "$status" = 0 ] || { cat "$scratch/refusal"; echo "FAIL deep session siblings exited $status" >&2; exit 1; }
[ "$(find "$scratch/branches" -type f -perm 0644 | wc -l)" -eq 2 ] || { echo 'FAIL a session file after a reopened level kept mode 0666' >&2; exit 1; }
printf 'PASS released levels are re-pinned for the second deep sibling\n'
branches "$scratch/moved"
status=0
OWNERSHIP_TEST_TREE="$scratch/moved" OWNERSHIP_TEST_VISITOR_ONLY=1 OWNERSHIP_TEST_SCENARIO=release-move \
  "$scratch/own-tree" session "$uid" "$gid" "$scratch/moved" > "$scratch/refusal" 2>&1 || status=$?
[ "$status" = 0 ] && [ -d "$scratch/moved/moved" ] || { cat "$scratch/refusal"; echo "FAIL session walk around a moved pinned level exited $status" >&2; exit 1; }
[ "$(find "$scratch/moved" -type f -perm 0644 | wc -l)" -eq 2 ] || { echo 'FAIL a session file after a level reopened by name kept mode 0666' >&2; exit 1; }
printf 'PASS a released level is reopened by its admitted name when ascent cannot re-pin it\n'
for mode in session shared; do
  branches "$scratch/swap-$mode"
  status=0
  OWNERSHIP_TEST_TREE="$scratch/swap-$mode" OWNERSHIP_TEST_VISITOR_ONLY=1 OWNERSHIP_TEST_SCENARIO=release-swap \
    "$scratch/own-tree" "$mode" "$uid" "$gid" "$scratch/swap-$mode" > "$scratch/refusal" 2>&1 || status=$?
  [ -L "$scratch/swap-$mode/d" ] && [ -d "$scratch/swap-$mode/moved" ] || { echo "FAIL $mode pinned and released levels were not moved" >&2; exit 1; }
  expected=0; [ "$mode" = shared ] || expected=78
  [ "$status" = "$expected" ] || { cat "$scratch/refusal"; echo "FAIL $mode replaced released ancestor exited $status" >&2; exit 1; }
  if [ "$mode" = session ] && ! grep -F 'directory identity changed while its descriptor was released' "$scratch/refusal" >/dev/null; then
    cat "$scratch/refusal"; echo 'FAIL session refusal does not name the changed identity' >&2; exit 1
  fi
  [ "$(cat "$scratch/outside/sentinel")" = untouched ]
  printf 'PASS %s replaced released ancestor is never followed\n' "$mode"
done
printf 'PASS ownership shell focused cases: 21\n'
