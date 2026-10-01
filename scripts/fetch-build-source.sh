#!/bin/sh
# Fetch one exact source identity. Mutable refs require a separate expected SHA.
set -eu

if [ "$#" -ne 4 ]; then
    echo 'usage: fetch-build-source.sh repository ref expected-sha destination' >&2
    exit 64
fi
repository=$1
ref=$2
expected=$3
destination=$4
[ -n "$repository" ] && [ -n "$destination" ] || exit 64
case "$expected" in
    *[!0-9a-f]*|'') echo 'expected source identity must be a full lowercase commit SHA' >&2; exit 64 ;;
esac
[ "${#expected}" -eq 40 ] || { echo 'expected source identity must contain 40 hex characters' >&2; exit 64; }
ref=${ref:-$expected}
[ ! -e "$destination" ] || { echo "source destination already exists: $destination" >&2; exit 64; }

git init --quiet -- "$destination"
git -C "$destination" remote add origin "$repository"
git -C "$destination" fetch --quiet --depth 1 --no-tags origin "$ref"
resolved=$(git -C "$destination" rev-parse --verify 'FETCH_HEAD^{commit}')
printf 'Source %s requested %s resolved %s\n' "$repository" "$ref" "$resolved"
if [ "$resolved" != "$expected" ]; then
    printf 'Source identity mismatch: expected %s, got %s\n' "$expected" "$resolved" >&2
    exit 1
fi
git -C "$destination" checkout --quiet --detach "$resolved"
printf '%s\n' "$resolved" > "$destination/.source-revision"
rm -rf "$destination/.git"
