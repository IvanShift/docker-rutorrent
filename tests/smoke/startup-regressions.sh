#!/usr/bin/env sh
# Run startup regressions against the local candidate without rebuilding.
set -eu
image=${1:-ivanshift/rutorrent:deps-20261001}
repo=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
case_count=0
run_case() {
  label=$1 expected=$2 prepare=$3
  shift 3
  case_count=$((case_count + 1))
  output=$(mktemp)
  status=0
  docker run --rm --name "rem4-startup-reg-${$}-${case_count}" --entrypoint /bin/sh \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/startup,dst=/usr/local/bin/startup,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/lib/rutorrent,dst=/usr/local/lib/rutorrent,readonly" \
    --mount "type=bind,src=$repo/rootfs/home/torrent/.rtorrent.rc,dst=/usr/local/share/rutorrent-defaults/rtorrent.rc,readonly" \
    --mount "type=bind,src=$repo/tests/smoke/fixtures/startup,dst=/startup-fixtures,readonly" \
    "$@" "$image" -ec "$prepare; /usr/local/bin/startup /bin/true" >"$output" 2>&1 || status=$?
  if [ "$status" -ne "$expected" ]; then
    cat "$output" >&2
    rm -f "$output"
    printf 'FAIL %s: expected %s, got %s\n' "$label" "$expected" "$status" >&2
    exit 1
  fi
  if [ "$expected" -eq 64 ]; then
    grep -q 'must be\|requires\|is invalid' "$output" || { cat "$output"; exit 1; }
  fi
  case "$label" in
    'invalid HTTP auth rejected') grep -F 'HTTP_AUTH must be exactly true or false' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'read-only password mount does not abort ownership sweep') grep -F 'read-only or restricted mount' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'unsupported rename capability warns under service UID') grep -F 'does not support rename-noreplace' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'rename probe error warns without aborting startup') grep -F 'Unable to check rename-noreplace' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
  esac
  rm -f "$output"
  printf 'PASS %s\n' "$label"
}
run_case 'double-slash protected download target rejected' 64 ':' -e DOWNLOAD_DIRECTORY=//etc/php85
run_case 'normal AutoTools lock survives native session admission' 0 'mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.autotools-file-jobs; umask 022; touch /data/.session/.autotools-file-jobs/.lock'
run_case 'root-owned config download target rejected' 64 ':' -e DOWNLOAD_DIRECTORY=/etc/php85
run_case 'root UID rejected' 64 ':' -e UID=0
run_case 'named UID rejected' 64 ':' -e UID=torrent
run_case 'invalid data ownership toggle rejected' 64 ':' -e CHECK_PERM_DATA=yes
run_case 'invalid HTTP auth rejected' 64 ':' -e HTTP_AUTH=True
run_case 'invalid listen port rejected' 64 ':' -e PORT_RTORRENT=abc
run_case 'invalid DHT mode rejected' 64 ':' -e MODE_DHT=banana
run_case 'invalid peer exchange rejected' 64 ':' -e PEER_EXCHANGE=banana
run_case 'relative download directory rejected' 64 ':' -e DOWNLOAD_DIRECTORY=relative
run_case 'fresh startup under umask 0002' 0 'umask 0002'
run_case 'normalized UID and named GID on repeated startup' 0 '/usr/local/bin/startup /bin/true' -e UID=0991 -e GID=users
run_case 'safe setgid session accepted' 0 'mkdir -m 2755 /data/.session'
run_case 'safe setgid private journals accepted' 0 'mkdir -m 2755 /data/.session; mkdir -m 2700 /data/.session/.retrackers-migration; touch /data/.session/.retrackers-migration/state; chmod 0600 /data/.session/.retrackers-migration/state'
run_case 'foreign RR journal rejected' 78 'mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.retrackers-migration; chown 1001 /data/.session/.retrackers-migration'
run_case 'foreign session file rejected' 78 'mkdir -m 0755 /data/.session; touch /data/.session/untrusted.torrent; chown 1001 /data/.session/untrusted.torrent'
run_case 'root private journals handed to configured UID' 0 'mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.retrackers-migration; touch /data/.session/.retrackers-migration/state; chmod 0600 /data/.session/.retrackers-migration/state' -e CHECK_PERM_DATA=false -e UID=1000
run_case 'hardlinked witness rejected' 78 'mkdir -m 0755 /config/rutorrent; mkdir -m 0700 /config/rutorrent/erasedata-jobs; touch /config/rutorrent/.erasedata-first-use; chmod 0400 /config/rutorrent/.erasedata-first-use; ln /config/rutorrent/.erasedata-first-use /config/witness-copy'
run_case 'dangling conf symlink rejected' 78 'mkdir -m 0755 /config/rutorrent; ln -s /missing /config/rutorrent/conf'
run_case 'safe download punctuation preserved' 0 ':' -e 'DOWNLOAD_DIRECTORY=/data/downloads space,amp&pipe|dollar$1'
run_case 'journal root must be a directory' 78 'mkdir -m 0755 /data/.session; touch /data/.session/.rutorrent-stop-intents; chmod 0600 /data/.session/.rutorrent-stop-intents'
run_case 'journal entries must be regular files' 78 'mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.rutorrent-stop-intents; mkdir -m 0700 /data/.session/.rutorrent-stop-intents/nested'
run_case 'image and root configuration remain root-owned' 0 '/usr/local/bin/startup /bin/true; test "$(stat -c %u /etc/php85/conf.d)" = 0; test "$(stat -c %u /etc/nginx)" = 0; test "$(stat -c %u /etc/s6.d)" = 0; test "$(stat -c %u /rutorrent/conf-dist)" = 0'
run_case 'deleted externalized trees recover on same-container restart' 0 '/usr/local/bin/startup /bin/true; rm -rf /config/rutorrent/conf /config/rutorrent/share /config/rtorrent/.rtorrent.rc; /usr/local/bin/startup /bin/true; test -f /config/rutorrent/conf/config.php; test -d /config/rutorrent/share/settings; test -f /config/rtorrent/.rtorrent.rc'
run_case 'moved session ownership is inspected before chown' 78 '/usr/local/bin/startup /bin/true; sed -i "s|session.path.set = /data/.session|session.path.set = /data/session2|" /config/rtorrent/.rtorrent.rc; mkdir -m 0755 /data/session2; mkdir -m 0700 /data/session2/.rutorrent-stop-intents; chown 1001 /data/session2/.rutorrent-stop-intents'
run_case 'double-slash persisted session target rejected' 78 '/usr/local/bin/startup /bin/true; sed -i "s|session.path.set = /data/.session|session.path.set = //etc/php85|" /config/rtorrent/.rtorrent.rc'
run_case 'custom PHP plugin copied and refreshed on restart' 0 'mkdir -p /config/custom_plugins/startuptest; cp /startup-fixtures/action.php /config/custom_plugins/startuptest/action.php; /usr/local/bin/startup /bin/true; test ! -L /rutorrent/app/plugins/startuptest; test "$(su-exec 991:991 php85 /rutorrent/app/plugins/startuptest/action.php)" = first; sed -i s/first/second/ /config/custom_plugins/startuptest/action.php; /usr/local/bin/startup /bin/true; test "$(su-exec 991:991 php85 /rutorrent/app/plugins/startuptest/action.php)" = second'
run_case 'escaped backslash download path accepted' 0 ':' -e 'DOWNLOAD_DIRECTORY=/data/back\slash'
printf '%s\n' 'torrent:placeholder' > "$scratch/passwd"
chmod 644 "$scratch/passwd"
run_case 'read-only password mount does not abort ownership sweep' 0 ':' --mount "type=bind,src=$scratch/passwd,dst=/config/nginx/passwd/rutorrent_passwd,readonly"
mkdir -m 0755 "$scratch/data" "$scratch/data/config"
run_case 'nested config witness survives CHECK_PERM_DATA ownership pass' 0 '/usr/local/bin/startup /bin/true; test "$(stat -c %u /config/rutorrent/.erasedata-first-use)" = 0; test "$(stat -c %u /data/config/rutorrent/.erasedata-first-use)" = 0' -e UID=1000 --mount "type=bind,src=$scratch/data,dst=/data" --mount "type=bind,src=$scratch/data/config,dst=/config"
run_case 'legacy daemon bypasses native data trust rules' 0 'touch /etc/rtorrent-legacy; printf "#!/bin/sh\nexit 0\n" > /usr/local/bin/update-config; chmod 755 /usr/local/bin/update-config; chmod 0775 /data'
run_case 'nondefault template values are persisted' 0 '/usr/local/bin/startup /bin/true; grep -F "network.listen.port.range.set = 46000-46000" /config/rtorrent/.rtorrent.rc; grep -F "dht.override_port.set = 46001" /config/rtorrent/.rtorrent.rc; grep -F "dht.mode.set = auto" /config/rtorrent/.rtorrent.rc; grep -F "protocol.pex.set = yes" /config/rtorrent/.rtorrent.rc' -e PORT_RTORRENT=46000 -e PORT_DHT=46001 -e MODE_DHT=auto -e PEER_EXCHANGE=yes
run_case 'conditional runtime PHP option whitelist' 0 '/usr/local/bin/startup /bin/true; grep -F "env[RU_LOCALHOSTS]" /etc/php85/php-fpm.d/runtime-env.conf; grep -F "env[RU_FORBID_USER_SETTINGS]" /etc/php85/php-fpm.d/runtime-env.conf; ! grep -F "env[RU_TRUSTED_ORIGINS]" /etc/php85/php-fpm.d/runtime-env.conf; su-exec 991:991 php-fpm85 -t' -e RU_LOCALHOSTS=example.local
run_case 'unsupported rename capability warns under service UID' 0 'cp /startup-fixtures/rename-probe.sh /usr/local/bin/rutorrent-erasedata-rename-noreplace; chmod 755 /usr/local/bin/rutorrent-erasedata-rename-noreplace; printf "rename-probe=1\n" > /usr/local/share/rutorrent-native-capabilities; /usr/local/bin/startup /bin/true; test "$(cat /data/first/.startup-probe-uid)" = 1000; sed -i "s|directory.default.set = \"/data/first\"|directory.default.set = \"/data/effective\"|" /config/rtorrent/.rtorrent.rc; /usr/local/bin/startup /bin/true; test "$(cat /data/effective/.startup-probe-dir)" = /data/effective' -e UID=1000 -e DOWNLOAD_DIRECTORY=/data/first
run_case 'rename probe error warns without aborting startup' 0 'cp /startup-fixtures/rename-probe.sh /usr/local/bin/rutorrent-erasedata-rename-noreplace; chmod 755 /usr/local/bin/rutorrent-erasedata-rename-noreplace; printf "rename-probe=1\n" > /usr/local/share/rutorrent-native-capabilities' -e PROBE_STATUS=5
printf 'PASS startup regression cases: %s\n' "$case_count"
