#!/bin/sh
# Compile Docker-owned helpers separately from the selected ruTorrent source ABI.
set -eu
[ "$#" -eq 2 ] || exit 64
source_root=$1
output_root=$2
mkdir -p "$output_root/usr/local/bin" "$output_root/usr/local/libexec" "$output_root/usr/local/share"
for spec in ownership-walk.c:bin/rutorrent-own-tree service-runtime.c:libexec/service-runtime native-actor-paths.c:libexec/native-actor-paths; do
    source_name=${spec%%:*}
    target_name=${spec#*:}
    source_file="$source_root/$source_name"
    [ -f "$source_file" ] && [ ! -L "$source_file" ] || { echo "Docker helper source unavailable: $source_name" >&2; exit 1; }
    cc -std=c11 -O2 -Wall -Wextra -Werror "$source_file" -o "$output_root/usr/local/$target_name"
    chmod 755 "$output_root/usr/local/$target_name"
done
printf '%s\n' 'version=1' 'rutorrent-own-tree' 'service-runtime' 'native-actor-paths' > "$output_root/usr/local/share/rutorrent-docker-helpers"
