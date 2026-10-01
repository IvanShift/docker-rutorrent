#!/bin/sh
# COPY preserves umask-dependent context modes; normalize only overlay entries.
set -eu
[ "$#" -eq 1 ] || exit 64
overlay=$1
cd "$overlay"
find . -type d -exec sh -c 'for path do chmod 755 "/${path#./}"; done' sh {} +
find . -type f -exec sh -c '
    for path do
        target="/${path#./}"
        if [ -x "$path" ]; then chmod 755 "$target"; else chmod 644 "$target"; fi
    done
' sh {} +
