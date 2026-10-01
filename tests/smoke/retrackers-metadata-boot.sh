#!/bin/sh
# Direct daemon fixture; invoked only in a disposable smoke container.
set -eu
variant=$1
mkdir -p /run/rtorrent /data/.session /data/downloads
chown 991:991 /run/rtorrent /data /data/.session /data/downloads
chmod 700 /data/.session
if [ "$variant" = legacy ]; then
  sed 's/log.open_file.flush/log.open_file/' /tests/smoke/legacy-working.rc > /tmp/metadata.rc
else
  cat > /tmp/metadata.rc <<'EOF'
system.daemon.set = true
session.path.set = /data/.session
directory.default.set = /data
network.scgi.open_local = /run/rtorrent/rtorrent.sock
schedule = chmod_scgi_socket,0,0,"execute.nothrow=chmod,600,/run/rtorrent/rtorrent.sock"
network.listen.port.range.set = 45551-45551
network.block.ipv6.set = no
dht.mode.set = disable
protocol.pex.set = no
EOF
fi
exec su-exec 991:991 /usr/local/bin/rtorrent -n -o import=/tmp/metadata.rc
