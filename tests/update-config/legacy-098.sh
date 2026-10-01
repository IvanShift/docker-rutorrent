#!/bin/sh
set -eu
# run.sh covers the legacy transform in every image by temporarily setting its
# runtime marker. This entry point also checks an actual legacy image marker.
[ -f /etc/rtorrent-legacy ] || { printf 'not ok - legacy image marker\n' >&2; exit 1; }
exec /tests/update-config/run.sh
