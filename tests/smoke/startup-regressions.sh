#!/usr/bin/env sh
# Run startup regressions against the local candidate without rebuilding.
set -eu
[ "$#" = 1 ] || { echo "Usage: $0 IMAGE" >&2; exit 64; }
image=$1
repo=$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
scratch=$(mktemp -d)
cleanup() {
  # Nested root-protected authentication directories are deliberate fixture data.
  docker run --rm --pull=never --network none --entrypoint /bin/sh \
    --mount "type=bind,src=$scratch,dst=/fixture" "$image" -ec 'rm -rf /fixture/*' >/dev/null 2>&1 || true
  rm -rf "$scratch"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
case_count=0
run_case() {
  label=$1 expected=$2 prepare=$3
  if [ -n "${STARTUP_CASE_FILTER:-}" ]; then
    case "$label" in *"$STARTUP_CASE_FILTER"*) ;; *) return 0 ;; esac
  fi
  shift 3
  case_count=$((case_count + 1))
  output=$(mktemp)
  status=0
  smoke_run_with_libexec "$repo/rootfs/usr/local/libexec" --rm --name "smoke-startup-regression-${$}-${case_count}" --entrypoint /bin/sh \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/startup,dst=/usr/local/bin/startup,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/gen-http-passwd,dst=/usr/local/bin/gen-http-passwd,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/nginx,dst=/usr/local/bin/nginx,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/bin/update-config,dst=/usr/local/bin/update-config,readonly" \
    --mount "type=bind,src=$repo/rootfs/etc/nginx/nginx.conf,dst=/candidate-nginx.conf,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/lib/rutorrent,dst=/usr/local/lib/rutorrent,readonly" \
    --mount "type=bind,src=$repo/rootfs/home/torrent/.rtorrent.rc,dst=/usr/local/share/rutorrent-defaults/rtorrent.rc,readonly" \
    --mount "type=bind,src=$repo/tests/smoke/fixtures/startup,dst=/startup-fixtures,readonly" \
    "$@" "$image" -ec "cp /candidate-nginx.conf /etc/nginx/nginx.conf; $prepare; /usr/local/bin/startup /bin/true" >"$output" 2>&1 || status=$?
  if [ "$status" -ne "$expected" ]; then
    cat "$output" >&2
    rm -f "$output"
    printf 'not ok - %s: expected %s, got %s\n' "$label" "$expected" "$status" >&2
    exit 1
  fi
  if [ "$expected" -eq 64 ]; then
    grep -q 'must be\|requires\|is invalid' "$output" || { cat "$output"; exit 1; }
  fi
  case "$label" in
    'foreign read-only ownership warnings are bounded') test "$(grep -c 'leaving ownership of' "$output")" -le 3 && grep -F 'additional ownership assignments refused' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'invalid HTTP auth rejected') grep -F 'HTTP_AUTH must be exactly true or false' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'unsafe read-only password owner is refused') grep -F 'Cannot attest HTTP authentication' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'unsupported rename capability warns under service UID') grep -F 'does not support rename-noreplace' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'native rename probe succeeds without a capability warning') ! grep -E 'Unable to check rename-noreplace|does not support rename-noreplace|using locked rename fallback' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'usable rename fallback is reported without a capability warning') grep -F 'using locked rename fallback' "$output" >/dev/null && ! grep -E 'Unable to check rename-noreplace|does not support rename-noreplace' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
    'rename probe error warns without aborting startup') grep -F 'Unable to check rename-noreplace' "$output" >/dev/null || { cat "$output"; exit 1; } ;;
  esac
  rm -f "$output"
  printf 'PASS %s\n' "$label"
}
run_case 'double-slash protected download target rejected' 64 ':' -e DOWNLOAD_DIRECTORY=//etc/php85
run_case 'compiled native actor checker survives source overlays' 0 'test -x /usr/local/libexec/native-actor-paths'
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
run_case 'fresh startup under umask 0002' 0 'umask 0002; /usr/local/bin/startup /bin/true; test "$(stat -c %a /config/rtorrent/.rtorrent.rc)" = 644; test "$(stat -c %a /data/.session)" = 755'
run_case 'normalized UID and named GID on repeated startup' 0 '/usr/local/bin/startup /bin/true' -e UID=0991 -e GID=users
run_case 'safe setgid session normalized for published daemon' 0 'mkdir -m 2755 /data/.session; /usr/local/bin/startup /bin/true; test "$(stat -c %a /data/.session)" = 755'
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
run_case 'unsafe read-only password owner is refused' 78 ':' --mount "type=bind,src=$scratch/passwd,dst=/config/nginx/passwd/rutorrent_passwd,readonly"
mkdir -m 0755 "$scratch/data" "$scratch/data/config"
run_case 'nested config witness survives CHECK_PERM_DATA ownership pass' 0 '/usr/local/bin/startup /bin/true; test "$(stat -c %u /config/rutorrent/.erasedata-first-use)" = 0; test "$(stat -c %u /data/config/rutorrent/.erasedata-first-use)" = 0' -e UID=1000 --mount "type=bind,src=$scratch/data,dst=/data" --mount "type=bind,src=$scratch/data/config,dst=/config"
run_case 'legacy daemon bypasses native data trust rules' 0 'touch /etc/rtorrent-legacy; chmod 0775 /data'
run_case 'nondefault template values are persisted' 0 '/usr/local/bin/startup /bin/true; grep -F "network.listen.port.range.set = 46000-46000" /config/rtorrent/.rtorrent.rc; grep -F "dht.override_port.set = 46001" /config/rtorrent/.rtorrent.rc; grep -F "dht.mode.set = auto" /config/rtorrent/.rtorrent.rc; grep -F "protocol.pex.set = yes" /config/rtorrent/.rtorrent.rc' -e PORT_RTORRENT=46000 -e PORT_DHT=46001 -e MODE_DHT=auto -e PEER_EXCHANGE=yes
run_case 'conditional runtime PHP option whitelist' 0 '/usr/local/bin/startup /bin/true; grep -F "env[RU_LOCALHOSTS]" /etc/php85/php-fpm.d/runtime-env.conf; grep -F "env[RU_FORBID_USER_SETTINGS]" /etc/php85/php-fpm.d/runtime-env.conf; ! grep -F "env[RU_TRUSTED_ORIGINS]" /etc/php85/php-fpm.d/runtime-env.conf; su-exec 991:991 php-fpm85 -t' -e RU_LOCALHOSTS=example.local
rename_probe_setup='cp /startup-fixtures/rename-probe.sh /usr/local/bin/rutorrent-erasedata-rename-noreplace; chmod 755 /usr/local/bin/rutorrent-erasedata-rename-noreplace; printf "rename-probe=1\n" > /usr/local/share/rutorrent-native-capabilities'
run_case 'unsupported rename capability warns under service UID' 0 "$rename_probe_setup"'; /usr/local/bin/startup /bin/true; test "$(cat /data/first/.startup-probe-uid)" = 1000; sed -i "s|directory.default.set = \"/data/first\"|directory.default.set = \"/data/effective\"|" /config/rtorrent/.rtorrent.rc; /usr/local/bin/startup /bin/true; test "$(cat /data/effective/.startup-probe-dir)" = /data/effective' -e UID=1000 -e DOWNLOAD_DIRECTORY=/data/first
run_case 'native rename probe succeeds without a capability warning' 0 "$rename_probe_setup" -e PROBE_STATUS=0
run_case 'usable rename fallback is reported without a capability warning' 0 "$rename_probe_setup" -e PROBE_STATUS=5
run_case 'rename probe error warns without aborting startup' 0 "$rename_probe_setup" -e PROBE_STATUS=6
run_case 'DHT disable compatibility retained' 0 ':' -e MODE_DHT=disable
run_case 'newline download path rejected before rc seed' 64 ':' -e 'DOWNLOAD_DIRECTORY=/data/a
b'
run_case 'outside data session refused before adoption' 78 '/usr/local/bin/startup /bin/true; sed -i "s|session.path.set = /data/.session|session.path.set = /srv/private|" /config/rtorrent/.rtorrent.rc; mkdir -m 0700 -p /srv/private'
run_case 'owned umask-writable session records normalized' 0 'mkdir -m 0755 /data/.session; touch /data/.session/ordinary.torrent; chown 991:991 /data/.session/ordinary.torrent; chmod 0664 /data/.session/ordinary.torrent; /usr/local/bin/startup /bin/true; test "$(stat -c %a /data/.session/ordinary.torrent)" = 644'
run_case 'AutoTools nonmove journal 0644 admitted' 0 'mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.autotools-file-jobs; touch /data/.session/.autotools-file-jobs/a.nonmove.json; chmod 0644 /data/.session/.autotools-file-jobs/a.nonmove.json'
run_case 'authentication ancestors stay root-owned' 0 '/usr/local/bin/startup /bin/true; test "$(stat -c %u /config)" = 0; test "$(stat -c %u:%g:%a /config/nginx/passwd)" = 0:991:750'
run_case 'sticky data ancestor supported' 0 'chmod 1777 /data'
run_case 'root nginx diagnostics retain service temp ownership' 0 '/usr/local/bin/startup /bin/true; nginx -t; test "$(stat -c %u /tmp/client_body)" = 991'
run_case 'password symlink is refused before reading target' 78 '/usr/local/bin/startup /bin/true; printf sentinel > /config/password-sentinel; ln -s /config/password-sentinel /config/nginx/passwd/rutorrent_passwd; printf "operator\n" | gen-http-passwd'
run_case 'password FIFO is refused without blocking' 78 '/usr/local/bin/startup /bin/true; mkfifo /config/nginx/passwd/rutorrent_passwd; printf "operator\n" | timeout 5 gen-http-passwd'
run_case 'failed custom copy recovers without stale image conflict' 0 'mkdir -p /config/custom_plugins/good /config/custom_plugins/bad; printf first > /config/custom_plugins/good/version; ln -s /config/custom_plugins/good/version /config/custom_plugins/bad/link; /usr/local/bin/startup /bin/true; test ! -e /rutorrent/app/plugins/bad; printf second > /config/custom_plugins/good/version; rm /config/custom_plugins/bad/link; printf fixed > /config/custom_plugins/bad/version; /usr/local/bin/startup /bin/true; test "$(cat /rutorrent/app/plugins/good/version)" = second; test "$(cat /rutorrent/app/plugins/bad/version)" = fixed'
run_case 'offline UID repair changes all profile records in one startup' 0 'UID=991 GID=991 /usr/local/bin/startup /bin/true; touch /data/.session/ordinary.torrent; chown 991:991 /data/.session/ordinary.torrent; mkdir -m 0700 /config/rutorrent/erasedata-jobs/default; touch /config/rutorrent/erasedata-jobs/default/scheduler.lock; chown -R 991:991 /config/rutorrent/erasedata-jobs/default; rm -f /tmp/nginx*.log; find /config /data -user 991 -not -path /config/nginx -not -path "/config/nginx/*" -not -path /config/rutorrent/.erasedata-first-use -exec chown -h 1000:1000 {} +; /usr/local/bin/startup /bin/true; test "$(stat -c %u:%g /config/rutorrent/erasedata-jobs/default/scheduler.lock)" = 1000:1000; test "$(stat -c %u:%g /data/.session/ordinary.torrent)" = 1000:1000; test "$(stat -c %u /config/rutorrent/.erasedata-first-use)" = 0' -e UID=1000 -e GID=1000
run_case 'service-owned FileBot hook gets executable bit restored' 0 'mkdir -p /filebot /config/filebot; printf "#!/bin/sh\nexit 0\n" > /filebot/filebot.sh; chmod 0755 /filebot/filebot.sh; printf music=y > /filebot/args_amc.txt; printf license > /config/filebot/license; printf "#!/bin/sh\necho hook-ok\n" > /config/filebot/postdl; chmod 0644 /config/filebot/postdl; /usr/local/bin/startup /bin/true; test "$(su-exec 991:991 /usr/local/bin/postdl)" = hook-ok' -e FILEBOT_LICENSE=/config/filebot/license
run_case 'host allowlist deduplicates built-in and operator names' 0 '/usr/local/bin/startup /bin/true; test "$(grep -c "localhost 1;" /etc/nginx/http-host-policy.conf)" = 1; test "$(grep -c "fixture.example 1;" /etc/nginx/http-host-policy.conf)" = 1' -e HTTP_AUTH=false -e ALLOW_UNAUTHENTICATED_HTTP=true -e HTTP_ALLOWED_HOSTS=localhost,fixture.example,fixture.example
run_case 'reserved nginx map host name rejected' 78 ':' -e HTTP_ALLOWED_HOSTS=include
run_case 'writable rc parent is refused' 78 'mkdir -m 0777 -p /config/rtorrent'
run_case 'writable custom plugin root is refused' 78 'mkdir -m 0777 -p /config/custom_plugins'
mkdir "$scratch/readonly"
for fixture_index in 1 2 3 4 5 6 7 8 9 10; do printf 'fixture\n' >"$scratch/readonly/$fixture_index"; done
chmod 0755 "$scratch/readonly"
chmod 0644 "$scratch/readonly/"*
run_case 'foreign read-only ownership warnings are bounded' 0 ':' --mount "type=bind,src=$scratch/readonly,dst=/data/readonly,readonly"
run_case 'IPv6 loopback content keeps fresh IPv6 enabled' 0 '/usr/local/bin/startup /bin/true; grep -Fx "network.block.ipv6.set = no" /config/rtorrent/.rtorrent.rc' --network none --sysctl net.ipv6.conf.lo.disable_ipv6=0
run_case 'empty IPv6 proc file blocks fresh IPv6' 0 '/usr/local/bin/startup /bin/true; grep -Fx "network.block.ipv6.set = yes" /config/rtorrent/.rtorrent.rc' --network none --sysctl net.ipv6.conf.lo.disable_ipv6=1
[ "$case_count" -gt 0 ] || { echo "No startup regression cases selected" >&2; exit 1; }
printf 'PASS startup regression cases: %s\n' "$case_count"
