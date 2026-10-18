#!/bin/sh
# Candidates and locks of another type, owner, mode or link count refuse without
# blocking and leave the published password file unchanged.
set -eu
directory=/config/nginx/passwd
file=$directory/rutorrent_passwd
lock=/run/rutorrent-passwd.lock
helper=/usr/local/libexec/service-runtime
mkfifo -m 0640 "$directory/rutorrent_passwd.tmp.fifo"
status=0
timeout 5 "$helper" publish-auth rutorrent_passwd.tmp.fifo 2>/audit/fifo-candidate || status=$?
test "$status" = 1
test "$(cat /audit/fifo-candidate)" = 'service-runtime: unsafe-auth-candidate'
rm "$directory/rutorrent_passwd.tmp.fifo"
printf 'PASS FIFO publication candidate refuses without blocking open\n'
for argument in '' 0 2 ' 9' +9 9x; do
  status=0
  "$helper" lock-auth "$argument" 2>/audit/lock-argument || status=$?
  test "$status" = 1
  test "$(cat /audit/lock-argument)" = 'service-runtime: invalid-auth-lock-descriptor'
done
status=0
"$helper" lock-auth 9 9<&- 2>/audit/lock-argument || status=$?
test "$status" = 1
test "$(cat /audit/lock-argument)" = 'service-runtime: unsafe-auth-lock'
before=$(sha256sum "$file")
: > /audit/lock-target
chmod 0600 /audit/lock-target
refuse_lock() {
  status=0
  printf 'reviewed\n' | AUTH_FIXTURE_PASSWORD=public-fixture-five timeout 5 /candidate-gen-http-passwd \
    >/dev/null 2>/audit/lock-refusal || status=$?
  if [ "$status" != 1 ] || [ "$(cat /audit/lock-refusal)" != 'service-runtime: unsafe-auth-lock' ]; then
    printf 'FAIL %s lock (status %s):\n' "$1" "$status" >&2
    cat /audit/lock-refusal >&2
    exit 1
  fi
  test "$(sha256sum "$file")" = "$before"
  test -z "$(find "$directory" -name '*.tmp.*' -print)"
  rm -f "$lock" "$lock.link"
}
rm -f "$lock"
mkfifo -m 0600 "$lock"
refuse_lock FIFO
ln -s /audit/lock-target "$lock"
refuse_lock symlinked
: > "$lock"
chmod 0640 "$lock"
refuse_lock group-readable
: > "$lock"
chmod 0600 "$lock"
ln "$lock" "$lock.link"
refuse_lock hard-linked
: > "$lock"
chmod 0600 "$lock"
chown 991 "$lock"
refuse_lock service-owned
printf 'reviewed\n' | AUTH_FIXTURE_PASSWORD=public-fixture-five /candidate-gen-http-passwd >/dev/null
test "$(stat -c '%F:%u:%a:%h' "$lock")" = 'regular empty file:0:600:1'
grep -Fx "reviewed:$(/usr/bin/openssl passwd -6 -salt authfixture public-fixture-five)" "$file" >/dev/null
printf 'PASS invalid lock descriptors and FIFO, symlinked, readable, linked or service-owned locks refuse before reading\n'
