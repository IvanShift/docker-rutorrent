#!/bin/sh
# Keep only the listed ruTorrent test files that image smoke runs in the image.
set -eu
[ "$#" -eq 2 ] || exit 64
root=$1
list=$2
[ -d "$root" ] && [ ! -L "$root" ] && [ -f "$list" ] || exit 64
keep=$(mktemp)
listing=$(mktemp)
trap 'rm -f "$keep" "$listing"' EXIT HUP INT TERM
grep -v -e '^#' -e '^$' "$list" > "$keep" || :
[ -s "$keep" ] || { echo 'image test keep-list is empty' >&2; exit 1; }
# A source that declares the native helper contract must provide every listed
# test; other sources (stock upstream, older refs) keep only what they have.
strict=false
[ ! -f "$root/tools/native-helpers.list" ] || strict=true
while IFS= read -r path; do
    case "$path" in
        tests/*) ;;
        *) echo "image test outside tests/: $path" >&2; exit 1 ;;
    esac
    case "$path" in
        *[!A-Za-z0-9_./-]*|*/.*|*//*|*/) echo "invalid image test path: $path" >&2; exit 1 ;;
    esac
    if [ -L "$root/$path" ] || { [ -e "$root/$path" ] && [ ! -f "$root/$path" ]; }; then
        echo "image test is not a regular file: $path" >&2; exit 1
    fi
    if [ "$strict" = true ] && [ ! -f "$root/$path" ]; then
        echo "image smoke test missing from ruTorrent source: $path" >&2; exit 1
    fi
done < "$keep"
[ -e "$root/tests" ] || exit 0
[ -d "$root/tests" ] && [ ! -L "$root/tests" ] || { echo 'ruTorrent tests is not a directory' >&2; exit 1; }
(cd "$root" && find tests ! -type d) > "$listing"
while IFS= read -r path; do
    grep -Fqx -- "$path" "$keep" || rm -f -- "$root/$path"
done < "$listing"
find "$root/tests" -mindepth 1 -depth -type d -empty -exec rmdir -- {} \;
# Fail closed on anything left that is not exactly a listed regular file.
(cd "$root" && find tests ! -type d) > "$listing"
while IFS= read -r path; do
    grep -Fqx -- "$path" "$keep" && [ -f "$root/$path" ] && [ ! -L "$root/$path" ] \
        || { echo "unexpected ruTorrent test residue: $path" >&2; exit 1; }
done < "$listing"
