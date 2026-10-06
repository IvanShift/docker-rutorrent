#!/bin/sh
# Real password publication; the adapter supplies a public fixture password.
set -eu
mkdir -p /audit/earlier /config/nginx/passwd
chown 0:991 /config /config/nginx /config/nginx/passwd
chmod 0755 /config
chmod 0750 /config/nginx /config/nginx/passwd
cat > /audit/earlier/php85 <<'SH'
#!/bin/sh
touch /audit/untrusted-php-fired
exit 94
SH
cat > /audit/earlier/openssl <<'SH'
#!/bin/sh
printf 'called\n' >> /audit/openssl-calls
exec /usr/bin/openssl passwd -6 -salt authfixture "$AUTH_FIXTURE_PASSWORD"
SH
chmod 0755 /audit/earlier/php85 /audit/earlier/openssl
PATH=/audit/earlier:$PATH
AUTH_FIXTURE_PASSWORD=public-fixture-one
export PATH AUTH_FIXTURE_PASSWORD
printf 'reviewed\n' | /candidate-gen-http-passwd >/audit/created
test ! -e /audit/untrusted-php-fired
grep -F 'password was generated' /audit/created >/dev/null
file=/config/nginx/passwd/rutorrent_passwd
test "$(stat -c '%u:%g:%a:%h' "$file")" = 0:991:640:1
expected=$(/usr/bin/openssl passwd -6 -salt authfixture "$AUTH_FIXTURE_PASSWORD")
test "$(cat "$file")" = "reviewed:$expected"
old_inode=$(stat -c %i "$file")
printf 'retained:fixture-hash\n' >> "$file"
AUTH_FIXTURE_PASSWORD=public-fixture-two
export AUTH_FIXTURE_PASSWORD
printf 'reviewed\n' | /candidate-gen-http-passwd >/audit/updated
test ! -e /audit/untrusted-php-fired
grep -F 'password has been updated' /audit/updated >/dev/null
test "$(stat -c %i "$file")" != "$old_inode"
test "$(stat -c '%u:%g:%a:%h' "$file")" = 0:991:640:1
expected=$(/usr/bin/openssl passwd -6 -salt authfixture "$AUTH_FIXTURE_PASSWORD")
grep -Fx "reviewed:$expected" "$file" >/dev/null
grep -Fx retained:fixture-hash "$file" >/dev/null
test "$(wc -l < "$file")" = 2
test "$(wc -l < /audit/openssl-calls)" = 2
test -z "$(find /config/nginx/passwd -name '*.tmp.*' -print)"
printf 'PASS fixed auth interpreter ignores earlier PATH PHP wrapper and publishes real OpenSSL hashes\n'
printf 'PASS auth update atomically replaces one root:GID0640 single-link inode and preserves another user\n'
mv /config/nginx/passwd /config/nginx/held-passwd
ln -s /config/nginx/held-passwd /config/nginx/passwd
status=0
printf 'reviewed\n' | /candidate-gen-http-passwd >/audit/refusal 2>&1 || status=$?
test "$status" = 78
grep -F 'Cannot attest HTTP authentication' /audit/refusal >/dev/null
test "$(wc -l < /audit/openssl-calls)" = 2
test ! -e /audit/untrusted-php-fired
printf 'PASS unsafe auth ancestor refuses before password hashing through the fixed interpreter\n'
