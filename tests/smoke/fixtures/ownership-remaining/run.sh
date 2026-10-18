#!/bin/sh
# Disposable helper-only startup cases: no daemon, licensed operation or network.
set -eu
scenario=$1
cp -a /candidate-nginx/. /etc/nginx/
start() { /usr/local/bin/startup /bin/true; }
filebot_fixture() {
  mkdir -p /filebot /config/filebot
  cat > /filebot/filebot.sh <<'SH'
#!/bin/sh
set -eu
if [ "${1:-}" = --license ]; then
  test -r "$2" || exit 9
  id -u > /filebot/data/license-uid
  printf registered > /filebot/data/.license
else
  id -u > /filebot/data/hook-uid
  printf 'packaged-hook\n'
fi
SH
  chmod 0755 /filebot/filebot.sh
  printf 'music=y\n' > /filebot/args_amc.txt
  printf fixture > /config/filebot/license.psm
  FILEBOT_LICENSE=/config/filebot/license.psm
  export FILEBOT_LICENSE
}
claim_fixture() {
  mkdir -m 0755 /data/.session
  mkdir -m 0700 /data/.session/.rutorrent-stop-intents
  hash=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  token=bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  claim=/data/.session/.rutorrent-stop-intents/$hash.claim
  twin=$claim.$token.tmp
  printf '%s:%s\n%s\n000\n%s\n' "$token" cccccccccccccccc dddddddddddddddddddddddddddddddd eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee > "$claim"
  chmod 0600 "$claim"
  ln "$claim" "$twin"
}
# A legal 600-level data tree; the startup walk must not need a descriptor per level.
deep_fixture() {
  leaf=/data/deep; i=0
  while [ "$i" -lt 600 ]; do leaf=$leaf/d; i=$((i + 1)); done
  mkdir -p "$leaf"
  printf leaf > "$leaf/file"
}
case "$scenario" in
  fresh) start; test "$(stat -c %u /data/.session)" = "$UID" ;;
  filebot-uid) filebot_fixture; start; test "$(cat /config/filebot/data/license-uid)" = "$UID"; test "$(stat -c '%u %a' /config/filebot/queue)" = "$UID 700" ;;
  filebot-media) filebot_fixture; CHECK_PERM_DATA=false; export CHECK_PERM_DATA; start; test "$(stat -c %u /data/media)" = "$UID" ;;
  filebot-data-link) filebot_fixture; ln -s /etc /config/filebot/data; start ;;
  filebot-license-link) filebot_fixture; mv /config/filebot/license.psm /config/filebot/real-license; ln -s /config/filebot/real-license /config/filebot/license.psm; start ;;
  filebot-license-hardlink) filebot_fixture; ln /config/filebot/license.psm /config/filebot/license-copy; start ;;
  filebot-license-missing) filebot_fixture; rm /config/filebot/license.psm; start ;;
  filebot-license-unreadable) filebot_fixture; mv /config/filebot/license.psm /filebot-license.psm; chmod 0600 /filebot-license.psm; FILEBOT_LICENSE=/filebot-license.psm; export FILEBOT_LICENSE; start ;;
  filebot-cache-link) filebot_fixture; mkdir /config/filebot/data; ln -s /etc/passwd /config/filebot/data/.license; start ;;
  filebot-custom-fallback)
    filebot_fixture
    printf '#!/bin/sh\nprintf "custom-hook\\n"\n' > /config/filebot/postdl
    chmod 0644 /config/filebot/postdl
    start
    test "$(su-exec "$UID:$GID" /usr/local/libexec/filebot-postdl fixture title)" = custom-hook
    rm /config/filebot/postdl
    start
    test "$(su-exec "$UID:$GID" /usr/local/libexec/filebot-postdl fixture title)" = packaged-hook
    test "$(cat /config/filebot/data/hook-uid)" = "$UID"
    ;;
  filebot-manual-root)
    # Root hooks only queue requests, as the service; jobs refuse root.
    filebot_fixture; start
    /usr/local/bin/postdl fixture title > /tmp/postdl.out
    /usr/local/bin/postrm
    test ! -s /tmp/postdl.out
    set -- /config/filebot/queue/job-*
    test "$#" = 1
    test -f "$1"
    printf 'fixture\0title\0' | cmp - "$1"
    test "$(stat -c '%u %a' "$1" /config/filebot/queue/cleanup)" = "$UID 600
$UID 600"
    for job in filebot-postdl filebot-cleanup; do
      status=0; "/usr/local/libexec/$job" fixture title || status=$?
      test "$status" = 78
    done
    test ! -e /config/filebot/data/hook-uid
    ;;
  filebot-named-group)
    filebot_fixture; GID=users; export GID; start
    /usr/local/bin/postdl fixture title
    test "$(stat -c '%u %g' /config/filebot/queue/job-*)" = "$UID 100"
    ;;
  filebot-defaults-missing) filebot_fixture; rm /usr/local/share/rutorrent-defaults/args_amc.txt; start ;;
  rc-default-missing) rm /usr/local/share/rutorrent-defaults/rtorrent.rc; start ;;
  filebot-root-zero-refused)
    filebot_fixture; start
    UID=000; export UID; /usr/local/bin/postdl fixture title
    ;;
  env-auth-drift) start; HTTP_AUTH=false; ALLOW_UNAUTHENTICATED_HTTP=true; export HTTP_AUTH ALLOW_UNAUTHENTICATED_HTTP; start ;;
  env-rpc-drift) start; ENABLE_RPC2=true; export ENABLE_RPC2; start ;;
  groupmod-failure)
    printf '#!/bin/sh\necho raw-groupmod-error >&2\nexit 23\n' > /usr/local/bin/groupmod
    chmod 0755 /usr/local/bin/groupmod
    GID=15555; export GID; start
    ;;
  usermod-failure)
    printf '#!/bin/sh\necho raw-usermod-error >&2\nexit 24\n' > /usr/local/bin/usermod
    chmod 0755 /usr/local/bin/usermod; start
    ;;
  claim-pair-uppercase)
    claim_fixture
    upper=/data/.session/.rutorrent-stop-intents/AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA.claim
    mv "$claim" "$upper"; mv "$twin" "$upper.$token.tmp"; claim=$upper; twin=$upper.$token.tmp
    printf 'EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE' | dd of="$claim" bs=1 seek=87 conv=notrunc 2>/dev/null
    start; test ! -e "$twin"; test "$(stat -c %h "$claim")" = 1
    ;;
  claim-pair) claim_fixture; start; test ! -e "$twin"; test "$(stat -c %h "$claim")" = 1; test "$(stat -c %u "$claim")" = "$UID" ;;
  claim-wrong-token) claim_fixture; mv "$twin" "$claim.ffffffffffffffffffffffffffffffff.tmp"; start ;;
  claim-different-inode) claim_fixture; rm "$twin"; cp "$claim" "$twin"; ln "$claim" /data/other-alias; start ;;
  claim-third-link) claim_fixture; ln "$claim" /data/third-alias; start ;;
  claim-malformed) claim_fixture; printf X | dd of="$claim" bs=1 seek=32 conv=notrunc 2>/dev/null; start ;;
  claim-foreign) claim_fixture; chown 15555 "$claim"; start ;;
  claim-mode) claim_fixture; chmod 0644 "$claim"; start ;;
  claim-parent-mode) claim_fixture; chmod 0770 /data/.session/.rutorrent-stop-intents; start ;;
  private-hardlink)
    mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.retrackers-migration
    printf state > /data/.session/.retrackers-migration/state; chmod 0600 /data/.session/.retrackers-migration/state
    ln /data/.session/.retrackers-migration/state /data/private-alias; start
    ;;
  preowned-private-access)
    # Root without DAC capabilities mirrors root_squash traversal refusal;
    # metadata and ownership are already correct, and selected UID opens0700.
    mkdir -m 0755 /data/.session; mkdir -m 0700 /data/.session/.retrackers-migration
    printf state > /data/.session/.retrackers-migration/state; chmod 0600 /data/.session/.retrackers-migration/state
    chown -R "$UID:$GID" /data/.session
    /usr/local/bin/rutorrent-own-tree session "$UID" "$GID" /data/.session
    ;;
  deep-data) deep_fixture; chown -R "$UID:$GID" /data/deep; (ulimit -n 64 && start); test "$(stat -c %u:%g "$leaf/file")" = "$UID:$GID" ;;
  deep-data-root-owned) deep_fixture; (ulimit -n 64 && start); test -z "$(find /data/deep ! -user "$UID" -o ! -group "$GID" | head -n 1)" ;;
  *) exit 64 ;;
esac
printf 'PASS ownership startup %s\n' "$scenario"
