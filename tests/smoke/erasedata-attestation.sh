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
docker run --rm -i --pull=never --network none --name "smoke-services-attestation-$$" "$@" --entrypoint /bin/sh "$image" -s <<'CONTAINER'
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
assert() { "$@" || { echo "not ok - attestation assertion: $*" >&2; exit 1; }; }
command=/usr/local/bin/attest-erasedata-queue
flag=--attest-clean-legacy-queue
clean
expect 64 "$command"
assert [ ! -e /config/rutorrent/.erasedata-first-use ]
expect 0 "$command" "$flag"
assert [ ! -e /config/rutorrent/share/settings/erasedata ]
assert [ "$(stat -c '%u:%g:%a:%h:%s' /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0 ]
assert [ "$(stat -c '%u:%g:%a' /config/rutorrent/erasedata-jobs)" = 991:991:700 ]
expect 0 "$command" "$flag"
chown 991:991 /config/rutorrent/.erasedata-first-use
chmod 0600 /config/rutorrent/.erasedata-first-use
expect 0 "$command" "$flag"
assert [ "$(stat -c '%u:%g:%a:%h:%s' /config/rutorrent/.erasedata-first-use)" = 0:0:400:1:0 ]
echo 'ok - explicit clean legacy attestation, idempotence and clean witness repair'
clean
mkdir -p /config/rutorrent/share/users
printf '*' > /config/rutorrent/share/users/.gitignore
queue=/config/rutorrent/share/settings/erasedata
for name in .drain-state.lock scheduler.lock AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.lock; do touch "$queue/$name"; chmod 0666 "$queue/$name"; done
printf '%s' '{"version":1,"user":"","generation":"0000000000000001","acknowledged":"0000000000000001","phase":"disarmed","journal":[],"diagnostics":[]}' > "$queue/.drain-state"
printf '%064d' 0 > "$queue/.private-root-key"
printf v1 > "$queue/.private-root-key-state"
chmod 0666 "$queue/.drain-state"
chmod 0600 "$queue/.private-root-key" "$queue/.private-root-key-state"
expect 0 "$command" "$flag"
test ! -e "$queue" || { echo 'not ok - idle captured legacy queue was not retired' >&2; exit 1; }
mkdir -m 0700 /config/rutorrent/erasedata-jobs/default
touch /config/rutorrent/erasedata-jobs/default/scheduler.lock
queue=/config/rutorrent/erasedata-jobs/default
printf '%s' '{"version":1,"user":"","generation":"0000000000000001","acknowledged":"0000000000000001","phase":"disarmed","journal":[],"diagnostics":[]}' > "$queue/.drain-state"
chmod 0666 "$queue/.drain-state"
before=$(sha256sum "$queue/.drain-state")
rm /config/rutorrent/.erasedata-first-use
expect 0 "$command" "$flag"
test -f "$queue/scheduler.lock" || exit 1
test "$(sha256sum "$queue/.drain-state")" = "$before" || exit 1
test "$(stat -c %a "$queue/.drain-state")" = 666 || exit 1
echo 'ok - V-8 captured idle controls and users gitignore admitted; protected controls retained'
clean
printf '%s' '{"version":1,"user":"","generation":"0000000000000001","acknowledged":"0000000000000001","phase":"armed","journal":[],"diagnostics":[]}' > /config/rutorrent/share/settings/erasedata/.drain-state
chmod 0600 /config/rutorrent/share/settings/erasedata/.drain-state
expect 78 "$command" "$flag"
test -f /config/rutorrent/share/settings/erasedata/.drain-state || exit 1
echo 'ok - active drain state preserved and refused'
for invalid in foreign-owner multiple-link symlink fifo executable-mode; do
  clean
  queue=/config/rutorrent/share/settings/erasedata
  printf '%s' '{"version":1,"user":"","generation":"0000000000000001","acknowledged":"0000000000000001","phase":"disarmed","journal":[],"diagnostics":[]}' > "$queue/.drain-state"
  chmod 0666 "$queue/.drain-state"
  case "$invalid" in
    foreign-owner) chown 1001 "$queue/.drain-state" ;;
    multiple-link) ln "$queue/.drain-state" /config/drain-state-link ;;
    symlink) rm "$queue/.drain-state"; ln -s /etc/passwd "$queue/.drain-state" ;;
    fifo) rm "$queue/.drain-state"; mkfifo "$queue/.drain-state" ;;
    executable-mode) chmod 0777 "$queue/.drain-state" ;;
  esac
  before=$(stat -c %u:%g:%a:%h:%s "$queue/.drain-state")
  expect 78 "$command" "$flag"
  test "$(stat -c %u:%g:%a:%h:%s "$queue/.drain-state")" = "$before" || exit 1
  test ! -e /config/rutorrent/.erasedata-first-use || exit 1
  rm -f /config/drain-state-link
done
echo 'ok - captured 0666 idle state retains owner, link, type and non-executable mode refusals'
clean
touch /config/rutorrent/share/settings/erasedata/active.list
expect 78 "$command" "$flag"
assert [ -f /config/rutorrent/share/settings/erasedata/active.list ]
assert [ ! -e /config/rutorrent/.erasedata-first-use ]
echo 'ok - pending legacy jobs are refused and preserved'
clean
ln -s /etc/passwd /config/rutorrent/.erasedata-first-use
expect 78 "$command" "$flag"
assert [ -L /config/rutorrent/.erasedata-first-use ]
clean
touch /config/rutorrent/.erasedata-first-use
chmod 0400 /config/rutorrent/.erasedata-first-use
ln /config/rutorrent/.erasedata-first-use /config/rutorrent/witness-link
expect 78 "$command" "$flag"
assert [ "$(stat -c %h /config/rutorrent/.erasedata-first-use)" = 2 ]
echo 'ok - symlink and multiple-link witnesses are refused'
clean
chown 1001 /config/rutorrent/share/settings/erasedata
expect 78 "$command" "$flag"
assert [ ! -e /config/rutorrent/.erasedata-first-use ]
clean
chmod 0777 /config/rutorrent/share/settings
expect 78 "$command" "$flag"
assert [ ! -e /config/rutorrent/.erasedata-first-use ]
clean
mkdir -m 0700 /config/rutorrent/erasedata-jobs
touch /config/rutorrent/erasedata-jobs/planted
expect 78 "$command" "$flag"
assert [ -f /config/rutorrent/erasedata-jobs/planted ]
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
