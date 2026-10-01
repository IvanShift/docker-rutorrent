#!/bin/sh
set -eu
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 IMAGE [CANDIDATE_ROOTFS]" >&2
  exit 64
fi
image=$1
shift
if [ "$#" = 1 ]; then
  set -- --mount "type=bind,src=$1,dst=/candidate,readonly"
fi
docker run --rm -i --name "rem4-services-attestation-$$" "$@" --entrypoint /bin/sh "$image" -s <<'CONTAINER'
set -eu
if [ -d /candidate ]; then
  mkdir -p /usr/local/libexec
  cp /candidate/usr/local/libexec/attest-erasedata-queue.php /usr/local/libexec/
  cp /candidate/usr/local/bin/attest-erasedata-queue /usr/local/bin/
fi
clean() {
  rm -rf /config/rutorrent
  chmod 0755 /config
  chown 0:0 /config
  mkdir -p /config/rutorrent/share/settings/erasedata
  chmod 0700 /config/rutorrent/share/settings/erasedata
}
expect() {
  expected=$1
  shift
  code=0
  "$@" >/tmp/attestation-output 2>&1 || code=$?
  if [ "$code" != "$expected" ]; then
    cat /tmp/attestation-output >&2
    echo "not ok - expected attestation exit $expected, got $code" >&2
    exit 1
  fi
}
command=/usr/local/bin/attest-erasedata-queue
flag=--attest-clean-legacy-queue
clean
expect 64 "$command"
[ ! -e /config/rutorrent/.erasedata-first-use ]
expect 0 "$command" "$flag"
[ ! -e /config/rutorrent/share/settings/erasedata ]
[ "$(stat -c '%u:%g:%a:%h:%s' /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0 ]
[ "$(stat -c '%u:%g:%a' /config/rutorrent/erasedata-jobs)" = 991:991:700 ]
expect 0 "$command" "$flag"
chown 991:991 /config/rutorrent/.erasedata-first-use
chmod 0600 /config/rutorrent/.erasedata-first-use
expect 0 "$command" "$flag"
[ "$(stat -c '%u:%g:%a:%h:%s' /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0 ]
echo 'ok - explicit clean legacy attestation, idempotence and clean witness repair'
clean
touch /config/rutorrent/share/settings/erasedata/active.list
expect 78 "$command" "$flag"
[ -f /config/rutorrent/share/settings/erasedata/active.list ]
[ ! -e /config/rutorrent/.erasedata-first-use ]
echo 'ok - pending legacy jobs are refused and preserved'
clean
ln -s /etc/passwd /config/rutorrent/.erasedata-first-use
expect 78 "$command" "$flag"
[ -L /config/rutorrent/.erasedata-first-use ]
clean
touch /config/rutorrent/.erasedata-first-use
chmod 0400 /config/rutorrent/.erasedata-first-use
ln /config/rutorrent/.erasedata-first-use /config/rutorrent/witness-link
expect 78 "$command" "$flag"
[ "$(stat -c %h /config/rutorrent/.erasedata-first-use)" = 2 ]
echo 'ok - symlink and multiple-link witnesses are refused'
clean
chown 1001 /config/rutorrent/share/settings/erasedata
expect 78 "$command" "$flag"
[ ! -e /config/rutorrent/.erasedata-first-use ]
clean
chmod 0777 /config/rutorrent/share/settings
expect 78 "$command" "$flag"
[ ! -e /config/rutorrent/.erasedata-first-use ]
clean
mkdir -m 0700 /config/rutorrent/erasedata-jobs
touch /config/rutorrent/erasedata-jobs/planted
expect 78 "$command" "$flag"
[ -f /config/rutorrent/erasedata-jobs/planted ]
echo 'ok - foreign owners, writable parents and unexpected job entries are refused'
clean
touch /etc/s6.d/rtorrent/down
s6-supervise /etc/s6.d/rtorrent &
supervisor=$!
attempt=0
while ! s6-svok /etc/s6.d/rtorrent; do
  attempt=$((attempt + 1))
  [ "$attempt" -lt 20 ] || exit 1
  sleep 0.1
done
expect 78 "$command" "$flag"
kill -TERM "$supervisor"
wait "$supervisor"
echo 'ok - operator attestation refuses active application supervision'
CONTAINER
