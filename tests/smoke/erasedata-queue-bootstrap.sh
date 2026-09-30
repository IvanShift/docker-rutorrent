#!/bin/sh

set -eu

if [ "$#" -ne 1 ]; then
  printf 'Usage: %s IMAGE\n' "$0" >&2
  exit 64
fi

image=$1
volume="rutorrent-erasedata-queue-$$-$(date +%s)"

cleanup() {
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

fail() {
  printf 'not ok - erasedata queue bootstrap: %s\n' "$1" >&2
  exit 1
}

run_case() {
  script=$1
  shift
  docker run --rm --entrypoint sh -e CHECK_PERM_DATA=false "$@" "$image" -ec "$script"
}

docker image inspect "$image" >/dev/null 2>&1 || fail "image not found: $image"
docker volume create "$volume" >/dev/null

# A first boot mints one root-owned witness; the recursive chown on the second
# boot must leave it intact. Keep this over a real persisted /config volume.
for boot in 1 2; do
  run_case '
    sh /usr/local/bin/startup true
    test ! -L /config/rutorrent/.erasedata-first-use
    test "$(stat -c %u:%a /config/rutorrent/.erasedata-first-use)" = 0:400
    test ! -L /config/rutorrent/erasedata-jobs
    test "$(stat -c %u:%a /config/rutorrent/erasedata-jobs)" = 991:700
  ' -v "$volume:/config" || fail "fresh or persisted boot $boot"
done

# An old group-writable profile has no first-use proof. It must keep running
# without silently gaining the new queue or witness.
run_case '
  mkdir -p /config/rutorrent/share/settings/erasedata
  printf "legacy pending intent\n" > /tmp/legacy-expected.list
  cp /tmp/legacy-expected.list /config/rutorrent/share/settings/erasedata/old.list
  chown -R 991:991 /config/rutorrent
  chmod 0775 /config /config/rutorrent/share /config/rutorrent/share/settings
  chmod 0700 /config/rutorrent/share/settings/erasedata
  chmod 0600 /config/rutorrent/share/settings/erasedata/old.list
  sh /usr/local/bin/startup true
  cmp -s /tmp/legacy-expected.list /config/rutorrent/share/settings/erasedata/old.list
  test ! -e /config/rutorrent/erasedata-jobs
  test ! -e /config/rutorrent/.erasedata-first-use
' || fail 'existing 0775 legacy profile or pending intent'

# A writable name-owning parent can make an old root disappear. Refuse before
# creating a new root, even though the path looks absent at startup.
run_case '
  chmod 0775 /config
  if sh /usr/local/bin/startup true > /tmp/queue-startup.log 2>&1; then exit 1; else result=$?; fi
  test "$result" -eq 78
  grep -Fq "Cannot attest erasedata queue root: /config is writable or foreign-owned" /tmp/queue-startup.log
  test ! -e /config/rutorrent
' || fail 'writable fresh /config was accepted'

# Model the old startup recursively chowning a foreign preplant after another
# group member hid a real queue. The patched startup must reject that marker.
run_case '
  mkdir -p /config/rutorrent/share/settings/erasedata
  chown -R 991:991 /config
  chmod 0775 /config
  printf "old intent\n" > /config/rutorrent/share/settings/erasedata/old.list
  chown 991:991 /config/rutorrent/share/settings/erasedata/old.list
  chmod 0600 /config/rutorrent/share/settings/erasedata/old.list
  su-exec 1001:991 mv /config/rutorrent /config/rutorrent.hidden
  su-exec 1001:991 mkdir /config/rutorrent
  su-exec 1001:991 mkdir -m 0700 /config/rutorrent/erasedata-jobs
  su-exec 1001:991 sh -ec ": > /config/rutorrent/.erasedata-first-use; chmod 0400 /config/rutorrent/.erasedata-first-use"
  find /config -exec chown -h 991:991 {} +
  chmod 0755 /config /config/rutorrent
  if sh /usr/local/bin/startup true > /tmp/queue-startup.log 2>&1; then exit 1; else result=$?; fi
  test "$result" -eq 78
  grep -Fq "Cannot attest erasedata queue root: first-use witness is unsafe" /tmp/queue-startup.log
  test "$(cat /config/rutorrent.hidden/share/settings/erasedata/old.list)" = "old intent"
' || fail 'old-chown foreign preplant was adopted'

# A crash after sibling creation but before witness publication leaves a
# visible hold. Restart must not convert that partial state into authority.
run_case '
  mkdir -p /config/rutorrent/erasedata-jobs
  chown -R 991:991 /config/rutorrent
  chmod 0755 /config/rutorrent
  chmod 0700 /config/rutorrent/erasedata-jobs
  if sh /usr/local/bin/startup true > /tmp/queue-startup.log 2>&1; then exit 1; else result=$?; fi
  test "$result" -eq 78
  grep -Fq "Cannot attest erasedata queue root: pre-existing sibling lacks first-use witness" /tmp/queue-startup.log
  test -d /config/rutorrent/erasedata-jobs
  test ! -e /config/rutorrent/.erasedata-first-use
' || fail 'sibling without witness was adopted'

printf 'ok - erasedata first-use queue bootstrap, legacy hold and preplant cuts\n'
