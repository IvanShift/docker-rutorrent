#!/bin/sh
# Concurrent runs for different users each keep the other's change. run.sh has
# published reviewed and retained entries and installed its PATH adapters.
set -eu
file=/config/nginx/passwd/rutorrent_passwd
mkdir /audit/merged
# Each merge waits until the other run merged too or waits in lock-auth, so an
# unserialized pair always publishes two candidates of one old state. /proc/locks
# cannot show that waiter: it hides a lock whose locking helper has exited.
cat > /audit/earlier/awk <<'SH'
#!/bin/sh
merge=false
for argument do
  case "$argument" in passwd=*) merge=true;; username=*) user=${argument#username=};; esac
done
/usr/bin/awk "$@" || exit
[ "$merge" = true ] || exit 0
touch "/audit/merged/$user"
attempt=0
until [ "$(ls /audit/merged | wc -l)" -ge 2 ] \
  || cat /proc/[0-9]*/cmdline 2>/dev/null | tr '\0' ' ' | grep 'service-runtime lock-aut[h]' >/dev/null; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 1000 ] || { echo 'merge barrier timed out' >&2; exit 1; }
  usleep 10000
done
SH
chmod 0755 /audit/earlier/awk
printf 'reviewed\n' | AUTH_FIXTURE_PASSWORD=public-fixture-three /candidate-gen-http-passwd >/audit/concurrent-reviewed 2>&1 &
reviewed=$!
printf 'added\n' | AUTH_FIXTURE_PASSWORD=public-fixture-four /candidate-gen-http-passwd >/audit/concurrent-added 2>&1 &
added=$!
status=0
wait "$reviewed" || status=$?
wait "$added" || status=$((status + $?))
rm /audit/earlier/awk
three=$(/usr/bin/openssl passwd -6 -salt authfixture public-fixture-three)
four=$(/usr/bin/openssl passwd -6 -salt authfixture public-fixture-four)
if [ "$status" != 0 ] || [ "$(wc -l < "$file")" != 3 ] || ! grep -Fx "reviewed:$three" "$file" >/dev/null \
  || ! grep -Fx "added:$four" "$file" >/dev/null || ! grep -Fx retained:fixture-hash "$file" >/dev/null; then
  printf 'FAIL concurrent runs lost an update (status %s):\n' "$status" >&2
  cat /audit/concurrent-reviewed /audit/concurrent-added "$file" >&2
  exit 1
fi
grep -F 'password has been updated for the http user: reviewed' /audit/concurrent-reviewed >/dev/null
grep -F 'password was generated for the http user: added' /audit/concurrent-added >/dev/null
test "$(stat -c '%u:%g:%a:%h' "$file")" = 0:991:640:1
test -z "$(find /config/nginx/passwd -name '*.tmp.*' -print)"
test "$(stat -c '%F:%u:%a:%h' /run/rutorrent-passwd.lock)" = 'regular empty file:0:600:1'
test -z "$(find /config/nginx/passwd -name '*.lock' -print)"
printf 'PASS concurrent different-user runs serialize read, merge and publication under the /run lock\n'
