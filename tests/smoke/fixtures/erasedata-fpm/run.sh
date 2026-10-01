#!/bin/sh
set -eu
proof_dir=$(mktemp -d /tmp/rem4-erasedata-fpm.XXXXXX)
export RENAME_PROOF_ROOT=$proof_dir
worker_pid=''
cleanup() {
  if [ -n "$worker_pid" ]; then
    kill "$worker_pid" 2>/dev/null || true
    wait "$worker_pid" 2>/dev/null || true
  fi
  rm -rf -- "$proof_dir"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
chown 991:991 "$proof_dir"
cat > "$proof_dir/pool.conf" <<CONF
[global]
error_log=$proof_dir/fpm-error.log
[worker]
user=torrent
group=torrent
listen=$proof_dir/fpm.sock
pm=static
pm.max_children=1
process.dumpable=no
catch_workers_output=yes
clear_env=no
request_terminate_timeout=5s
CONF
php-fpm85 -n -dextension=ctype.so -F -y "$proof_dir/pool.conf" >"$proof_dir/fpm-launch.log" 2>&1 &
worker_pid=$!
attempt=0
while [ ! -S "$proof_dir/fpm.sock" ]; do
  attempt=$((attempt+1))
  if [ "$attempt" -ge 50 ] || ! kill -0 "$worker_pid" 2>/dev/null; then
    cat "$proof_dir/fpm-launch.log" >&2
    exit 1
  fi
  sleep .1
done
printf 'Packaged erasedata filesystem SHA256: '
sha256sum /rutorrent/app/plugins/erasedata/filesystem.php
printf 'Packaged native rename helper SHA256: '
sha256sum /usr/local/bin/rutorrent-erasedata-rename-noreplace
timeout 10 php85 -n /proof/client.php
printf 'PASS packaged PHP-FPM UID991 process.dumpable=no inherited directory descriptor restore\n'
