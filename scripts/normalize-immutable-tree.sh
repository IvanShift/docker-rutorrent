#!/bin/sh
# Preserve source executable bits while removing checkout-dependent write access.
set -eu
[ "$#" -gt 0 ] || exit 64
for tree do
    [ -d "$tree" ] && [ ! -L "$tree" ] || exit 64
    # find does not follow symbolic links; never chmod an external link target.
    find "$tree" -type d -exec chmod go-w {} +
    find "$tree" -type f -exec chmod go-w {} +
done
