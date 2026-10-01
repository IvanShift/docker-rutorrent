#!/bin/sh
# Source presence controls compilation; every referenced helper must then exist.
set -eu
[ "$#" -eq 2 ] || exit 64
source_root=$1
output_root=$2
mkdir -p "$output_root/usr/local/bin" "$output_root/usr/local/share"
manifest="$output_root/usr/local/share/rutorrent-native-helpers"
helpers="$(mktemp)"
references="$(mktemp)"
sources="$(mktemp)"
trap 'rm -f "$helpers" "$references" "$sources"' EXIT HUP INT TERM
flags='-std=c11 -O2 -Wall -Wextra -Werror'
source_manifest="$source_root/tools/native-helpers.list"
declared=false
if [ -f "$source_manifest" ]; then
    declared=true
    version= flags= names=
    while IFS="$(printf '\t')" read -r name value extra; do
        case "$name" in ''|'#'*) continue ;; esac
        [ -z "$extra" ] && [ -n "$value" ] || { echo 'invalid native helper manifest entry' >&2; exit 1; }
        case "$name" in
            version) [ -z "$version" ] && [ "$value" = 1 ] || exit 1; version=$value ;;
            flags) [ -z "$flags" ] || exit 1; flags=$value ;;
            rutorrent-*)
                case "$name" in *[!a-z-]*) exit 1 ;; esac
                case " $names " in *" $name "*) echo 'duplicate native helper name' >&2; exit 1 ;; esac
                case "$value" in /*|*[!a-zA-Z0-9_./-]*|../*|*/../*|*/..|*.) exit 1 ;; esac
                case "$value" in *.c) ;; *) exit 1 ;; esac
                names="$names $name"
                printf '%s:%s\n' "$name" "$value" >> "$sources"
                ;;
            *) echo 'unknown native helper manifest directive' >&2; exit 1 ;;
        esac
    done < "$source_manifest"
    [ "$version" = 1 ] && [ -n "$flags" ] && [ -s "$sources" ] || { echo 'incomplete native helper manifest' >&2; exit 1; }
    for flag in $flags; do
        case "$flag" in -*[!a-zA-Z0-9_=.+-]*|[!-]*|'') echo 'invalid native helper compiler flag' >&2; exit 1 ;; esac
    done
else
    # Compatibility contract for the already-published commits before the fork
    # declared its own manifest. New helper names must be declared by the fork.
    printf '%s\n' \
        'rutorrent-task-kill-pidfd:plugins/_task/kill-verified.c' \
        'rutorrent-task-supervise:plugins/_task/supervise.c' \
        'rutorrent-erasedata-rename-noreplace:plugins/erasedata/rename-noreplace.c' > "$sources"
fi
while IFS= read -r pair; do
    name=${pair%%:*}
    source_file="$source_root/${pair#*:}"
    if [ -f "$source_file" ]; then
        # Manifest flags are validated words, never evaluated as shell code.
        cc $flags -o "$output_root/usr/local/bin/$name" "$source_file"
        printf '%s\n' "$name" >> "$helpers"
    elif [ "$declared" = true ]; then
        echo "declared native helper source unavailable: $source_file" >&2
        exit 1
    fi
done < "$sources"
# BusyBox grep has no --include. A failed find/read must still stop the build;
# grep's status 1 alone means a readable PHP file has no helper references.
find "$source_root/php" "$source_root/plugins" -type f -name '*.php' -exec sh -c '
    for path do
        if grep -h -o -E "/usr/local/bin/rutorrent-[a-z-]+" "$path"; then
            :
        else
            status=$?
            [ "$status" -eq 1 ] || exit "$status"
        fi
    done
' sh {} + > "$references"
sort -u "$references" -o "$references"
while IFS= read -r helper; do
    grep -q -x "${helper##*/}" "$helpers" && [ -x "$output_root$helper" ] \
        || { echo "required native helper unavailable: $helper" >&2; exit 1; }
done < "$references"
if [ -s "$helpers" ] || [ -s "$references" ]; then
    printf 'contract=native\n' > "$manifest"
    if [ "$declared" = false ]; then
        for helper in rutorrent-task-kill-pidfd rutorrent-task-supervise rutorrent-erasedata-rename-noreplace; do
            grep -q -x "$helper" "$helpers" && [ -x "$output_root/usr/local/bin/$helper" ] \
                || { echo "incomplete native helper contract: $helper" >&2; exit 1; }
        done
    fi
else
    printf 'contract=legacy\n' > "$manifest"
fi
sort -u "$helpers" >> "$manifest"
if [ "$declared" = true ] && grep -q '^rutorrent-erasedata-rename-noreplace$' "$helpers"; then
    # ABI 1 declares the directory capability probe; older published sources
    # compiled through the compatibility path do not promise that interface.
    printf 'rename-probe=1\n' > "$output_root/usr/local/share/rutorrent-native-capabilities"
fi
