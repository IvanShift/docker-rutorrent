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
printf 'PASS ownership shell focused cases: 14\n'
