#!/bin/sh

set -eu

smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
container="rutorrent-filebot-smoke-$$-$(date +%s)"
volume="${container}-config"
cleanup() {
  smoke_remove_run_containers || true
  docker volume rm "$volume" >/dev/null 2>&1 || true
}
smoke_install_traps
smoke_check_image "$image"

smoke_run --rm --network none --entrypoint /bin/sh "$image" -eu -c '
  filebot_version=$(/filebot/filebot.sh -version)
  printf "%s\n" "$filebot_version" | grep -F "FileBot 5.3.0" >/dev/null
  fpcalc -version
  find --version | grep -F "GNU findutils" >/dev/null
'

# Exercise the FileBot image entrypoint with an already-registered local fixture.
# It bypasses licence import only; startup, services, hooks and persistence are real.
docker volume create "$volume" >/dev/null
smoke_run --rm --network none --entrypoint /bin/sh -v "$volume:/config" "$image" -eu -c '
  mkdir -p /config/filebot/data
  printf "%s\n" smoke-fixture > /config/filebot/data/.license
  printf "%s\n" smoke-fixture > /config/filebot/smoke-license.psm
'
smoke_run -d --network none --name "$container" -v "$volume:/config" \
  -e FILEBOT_LICENSE=/config/filebot/smoke-license.psm "$image" >/dev/null
# Startup installs each hook line exactly once, unchanged across restarts.
check_hooks() {
  rc=$(docker exec "$container" cat /config/rtorrent/.rtorrent.rc) || fail 'cannot read the persisted rc'
  for hook in 'method.set_key = event.download.finished,filebot,"execute.nothrow=/usr/local/bin/postdl,$d.base_path=,$d.name="' \
    'method.set_key = event.download.erased,filebot_cleaner,"execute.nothrow=/usr/local/bin/postrm"'; do
    [ "$(printf '%s\n' "$rc" | grep -Fcx "$hook")" = 1 ] || fail "FileBot hook must occur exactly once: $hook"
  done
}
wait_for_health "$container" || fail 'FileBot entrypoint readiness'
check_hooks
docker exec "$container" sh -eu -c '
  test -L /filebot/data
  test "$(readlink /filebot/data)" = /config/filebot/data
  test -L /filebot/args_amc.txt
  test -f /config/filebot/args_amc.txt
  test -x /usr/local/bin/postdl
  test -x /usr/local/bin/postrm
  test "$(s6-svstat -o up /etc/s6.d/filebot-queue)" = true
  printf "%s\n" "# operator FileBot argument" >> /config/filebot/args_amc.txt
' || fail 'FileBot startup hooks and persisted arguments'
docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'FileBot restart readiness'
check_hooks
docker exec "$container" grep -Fqx '# operator FileBot argument' /config/filebot/args_amc.txt \
  || fail 'FileBot restart lost operator arguments'
# The source remains a symlink after a real restart: test the missing-target branch.
docker exec "$container" rm /config/filebot/args_amc.txt
docker restart "$container" >/dev/null
wait_for_health "$container" || fail 'FileBot missing args restart readiness'
docker exec "$container" sh -eu -c '
  test -f /config/filebot/args_amc.txt
  test ! -L /config/filebot/args_amc.txt
  test -L /filebot/args_amc.txt
  grep -Fqx music=y /config/filebot/args_amc.txt
' || fail 'FileBot arguments were not restored from the image'

printf 'ok - FileBot toolchain, full entrypoint, hooks and restart persistence\n'
"$smoke_dir/filebot-queue.sh" "$image"
"$smoke_dir/mediainfo.sh" "$image"
"$smoke_dir/screenshots.sh" "$image"
