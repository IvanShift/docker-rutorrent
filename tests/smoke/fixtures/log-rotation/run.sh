#!/bin/sh
set -eu
scenario=$1
if [ "$scenario" = nondefault ]; then export UID=1000 GID=1000; fi
service_identity="${UID:-991}:${GID:-991}"
rotator=''
fpm=''
cleanup() {
  [ -z "$rotator" ] || { kill "$rotator" 2>/dev/null || true; wait "$rotator" 2>/dev/null || true; }
  [ -z "$fpm" ] || { kill "$fpm" 2>/dev/null || true; wait "$fpm" 2>/dev/null || true; }
}
trap cleanup EXIT
fail() { printf 'not ok - log rotation %s: %s\n' "$scenario" "$1" >&2; cat /tmp/rotation-service.log >&2 || true; exit 1; }
emit() {
  # Use the shipped application writer, not a fixture approximation of its I/O.
  su-exec "$service_identity" php85 -d include_path=/rutorrent/app/php/utility -r '
    require "/rutorrent/app/php/utility/fileutil.php";
    $log_file = $argv[2] ?? "/tmp/errors.log";
    $profileMask = 0644;
    for ($i = 0; $i < (int)$argv[3]; ++$i) FileUtil::toLog($argv[1] . str_repeat("x", 96));
  ' "$1" "${2:-/tmp/errors.log}" "${3:-350}"
}
start_rotation() {
  MAX_SIZE=4096 SLEEP_SECS=1 /etc/s6.d/logrotate/run > /tmp/rotation-service.log 2>&1 &
  rotator=$!
}
wait_rotation() {
  generation=$1
  basename=${2:-errors.log}
  attempt=0
  until [ -f "/tmp/rutorrent-logrotate/$basename.1" ] && grep -Fq "$generation" "/tmp/rutorrent-logrotate/$basename.1"; do
    attempt=$((attempt + 1))
    [ "$attempt" -lt 6 ] || fail "350 actual application lines did not rotate (bytes=$(stat -c %s /tmp/errors.log 2>/dev/null || echo absent))"
    sleep 1
  done
}
/usr/local/bin/startup /bin/true >/dev/null
sha256sum /rutorrent/app/php/utility/fileutil.php
if [ "$scenario" = growth ] || [ "$scenario" = nondefault ]; then
  start_rotation
  for generation in generation1 generation2 generation3; do
    emit "$generation"
    wait_rotation "$generation"
  done
  gzip -cd /tmp/rutorrent-logrotate/errors.log.1.gz | grep -Fq generation2 || fail 'compressed previous generation missing'
  [ "$(find /tmp/rutorrent-logrotate -name 'errors.log.*' | wc -l)" = 2 ] || fail 'rotation retention is not bounded to two archives'
  [ "$(stat -c '%u:%a' /tmp/rutorrent-logrotate)" = "${UID:-991}:700" ] || fail 'archive directory is not private to service UID'
  emit fresh-line /tmp/errors.log 1
  sleep 2
  grep -Fq fresh-line /tmp/errors.log || fail 'application did not recreate active log after rotation'
  [ "$(stat -c %s /tmp/errors.log)" -lt 4096 ] || fail 'fresh active log unexpectedly exceeds test threshold'
  emit stdout-sentinel php://stdout 1 > /tmp/stdout-observation
  grep -Fq stdout-sentinel /tmp/stdout-observation || fail 'explicit stdout logging changed'
  printf 'ok - three actual FileUtil::toLog bursts rotate with bounded retention and subsequent writes continue\n'
  exit 0
fi
if [ "$scenario" = other-logs ]; then
  start_rotation
  for basename in nginx-rutorrent-access.log nginx-rutorrent-error.log nginx-error.log php_error.log; do
    emit "$basename" "/tmp/$basename"
    wait_rotation "$basename" "$basename"
  done
  printf 'ok - existing nginx and FPM logs retain common rotation behavior\n'
  exit 0
fi
if [ "$scenario" = reopen-retry ]; then
  # The shipped helper refuses while the pidfile is missing; the FPM master keeps
  # writing to its moved log until a later pass reopens it.
  wait_for() {
    attempt=0
    until eval "$1"; do
      attempt=$((attempt + 1))
      [ "$attempt" -lt 10 ] || fail "$2"
      sleep 1
    done
  }
  # /proc fd links of the service UID's master need that UID, not root.
  holds() { su-exec "$service_identity" sh -c 'for fd in /proc/$1/fd/*; do [ "$(readlink "$fd")" = "$2" ] && exit 0; done; exit 1' sh "$master" "$1"; }
  su-exec "$service_identity" /usr/sbin/php-fpm85 &
  fpm=$!
  wait_for '[ -s /run/php/php85-fpm.pid ]' 'FPM master did not start'
  master=$(cat /run/php/php85-fpm.pid)
  emit php_error.log /tmp/php_error.log
  mv /run/php/php85-fpm.pid /run/php/hidden-fpm.pid
  start_rotation
  wait_for 'grep -Fq reopen-pidfile-unavailable /tmp/rotation-service.log' 'moved log did not request a reopen'
  holds /tmp/rutorrent-logrotate/php_error.log.1 || fail 'FPM master does not hold the moved log'
  wait_for '[ "$(grep -c reopen-pidfile-unavailable /tmp/rotation-service.log)" -ge 3 ]' 'refused reopen was not retried'
  [ ! -e /tmp/php_error.log ] && [ ! -e /tmp/rutorrent-logrotate/php_error.log.1.gz ] || fail 'log in use changed while its reopen was pending'
  [ "$(grep -c 'refusing php85 reason=reopen-refused' /tmp/rotation-service.log)" = 1 ] || fail 'reopen refusal is not reported once'
  # A restarted service still owes the master its pending reopen.
  kill "$rotator"
  wait "$rotator" || true
  # Only the restarted service's own refusal may satisfy the next wait.
  : > /tmp/rotation-service.log
  start_rotation
  wait_for 'grep -Fq "refusing php85 reason=reopen-refused" /tmp/rotation-service.log' 'restarted service lost the pending reopen'
  mv /run/php/hidden-fpm.pid /run/php/php85-fpm.pid
  wait_for 'grep -Fq "resumed php85 after reason=reopen-refused" /tmp/rotation-service.log' 'retried reopen did not succeed'
  wait_for 'holds /tmp/php_error.log' 'FPM master did not return to the active log'
  ! holds /tmp/rutorrent-logrotate/php_error.log.1 || fail 'FPM master still holds the moved log'
  printf 'ok - a refused FPM reopen is retried through the shipped pidfd helper, also after a service restart, until the master leaves its archive\n'
  exit 0
fi
emit sentinel
before=$(sha256sum /tmp/errors.log | cut -d ' ' -f1)
reason=unsafe-file
case "$scenario" in
  symlink)
    mv /tmp/errors.log /tmp/rotation-sentinel
    su-exec "$service_identity" ln -s /tmp/rotation-sentinel /tmp/errors.log
    ;;
  hardlink) su-exec "$service_identity" ln /tmp/errors.log /tmp/rotation-sentinel ;;
  foreign) chown 1001:1001 /tmp/errors.log ;;
  fifo) rm /tmp/errors.log; mkfifo -m 0644 /tmp/errors.log; chown 991:991 /tmp/errors.log ;;
  parent) chmod 0777 /tmp; reason=unsafe-log-parent ;;
  parent-owner) chown 1001:1001 /tmp; reason=unsafe-log-parent ;;
  repair) chown 1001:1001 /tmp/errors.log ;;
  archive-symlink)
    mkdir /tmp/rotation-outside
    chown 991:991 /tmp/rotation-outside
    su-exec "$service_identity" ln -s /tmp/rotation-outside /tmp/rutorrent-logrotate
    reason=unsafe-archive-directory
    ;;
  archive-owner)
    mkdir -m 0700 /tmp/rutorrent-logrotate
    chown 1001:1001 /tmp/rutorrent-logrotate
    reason=unsafe-archive-directory
    ;;
  archive-leaf)
    mkdir -m 0700 /tmp/rutorrent-logrotate
    chown 991:991 /tmp/rutorrent-logrotate
    su-exec "$service_identity" ln -s /tmp/errors.log /tmp/rutorrent-logrotate/errors.log.1.gz
    reason=unsafe-archive
    ;;
  *) fail 'unknown scenario' ;;
esac
start_rotation
sleep 3
[ "$(grep -c "reason=$reason" /tmp/rotation-service.log)" = 1 ] || fail 'unsafe rotation must be visible once, without a refusal storm'
if [ "$scenario" = symlink ]; then observed=/tmp/rotation-sentinel; else observed=/tmp/errors.log; fi
if [ "$scenario" != fifo ]; then
  [ "$(sha256sum "$observed" | cut -d ' ' -f1)" = "$before" ] || fail 'refused log contents changed'
fi
case "$scenario" in
  symlink) [ -L /tmp/errors.log ] || fail 'source symlink was moved' ;;
  hardlink) [ "$(stat -c %h /tmp/errors.log)" = 2 ] || fail 'hardlinked source was moved' ;;
  fifo) [ -p /tmp/errors.log ] || fail 'FIFO was moved' ;;
esac
if [ "$scenario" = repair ]; then
  chown "$service_identity" /tmp/errors.log
  wait_rotation sentinel
fi
printf 'ok - %s rotation refusal is safe, visible and bounded\n' "$scenario"
