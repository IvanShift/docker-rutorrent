#!/bin/sh
set -eu
umask 077
mkdir -p /run/rtorrent /data/.session
chown 991:991 /data /data/.session /run/rtorrent
chmod 0700 /data/.session /run/rtorrent
export RUTORRENT_NATIVE_PHP=/usr/bin/php85
export RUTORRENT_NATIVE_APP_ROOT=/rutorrent/app
exec su-exec 991:991 /usr/local/bin/rtorrent -n -o import=/tests/schedules/rtorrent.rc
