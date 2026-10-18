#!/bin/sh
# Keep only the listed ruTorrent test files that image smoke runs in the image.
set -eu
[ "$#" -eq 2 ] || exit 64
root=$1
list=$2
[ -d "$root" ] && [ ! -L "$root" ] && [ -f "$list" ] || exit 64
keep=$(mktemp)
listing=$(mktemp)
# A signal ends the run; its exit then removes the scratch once.
trap 'rm -f -- "$keep" "$listing"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
# grep exits 1 when no line matches; a higher status is a read error, which must
# never count as an unlisted test.
unreadable() { echo 'cannot read image test keep-list' >&2; exit 1; }
listed() { grep -Fqx -- "$1" "$keep" || { [ "$?" -eq 1 ] || unreadable; return 1; }; }
grep -v -e '^#' -e '^$' "$list" > "$keep" || [ "$?" -eq 1 ] || unreadable
[ -s "$keep" ] || { echo 'image test keep-list is empty' >&2; exit 1; }
# A source that declares the native helper contract must provide every listed
# test; other sources (stock upstream, older refs) keep only what they have.
strict=false
[ ! -f "$root/tools/native-helpers.list" ] || strict=true
present=
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
    if [ -f "$root/$path" ]; then
        present="$present $path"
    elif [ "$strict" = true ]; then
        echo "image smoke test missing from ruTorrent source: $path" >&2; exit 1
    fi
done < "$keep"
[ -e "$root/tests" ] || exit 0
[ -d "$root/tests" ] && [ ! -L "$root/tests" ] || { echo 'ruTorrent tests is not a directory' >&2; exit 1; }
# The listing is read line by line with IFS= read -r, so only a newline can corrupt
# it: the name would be split into other paths, possibly outside tests/. Refuse
# such a file or directory name before anything is deleted.
nl='
'
(cd "$root" && find tests -name "*$nl*") > "$listing"
[ ! -s "$listing" ] || { echo 'ruTorrent test path contains a newline' >&2; exit 1; }
(cd "$root" && find tests ! -type d) > "$listing"
while IFS= read -r path; do
    listed "$path" || rm -f -- "$root/$path"
done < "$listing"
find "$root/tests" -mindepth 1 -depth -type d -empty -exec rmdir -- {} \;
# Every listed test present before pruning must still be there. The checked paths
# hold no field-splitting or pattern characters.
for path in $present; do
    [ -f "$root/$path" ] && [ ! -L "$root/$path" ] \
        || { echo "image smoke test lost while pruning: $path" >&2; exit 1; }
done
# Fail closed on anything left that is not exactly a listed regular file.
(cd "$root" && find tests ! -type d) > "$listing"
while IFS= read -r path; do
    listed "$path" && [ -f "$root/$path" ] && [ ! -L "$root/$path" ] \
        || { echo "unexpected ruTorrent test residue: $path" >&2; exit 1; }
done < "$listing"
