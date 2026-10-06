#!/bin/sh
# Disposable helper-only startup cases: no daemon, licensed operation or network.
set -eu
scenario=$1
cp -a /candidate-nginx/. /etc/nginx/
start() { /usr/local/bin/startup /bin/true; }
filebot_fixture() {
  mkdir -p /filebot /config/filebot /usr/local/share/rutorrent-defaults
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
  printf 'music=y\n' > /usr/local/share/rutorrent-defaults/args_amc.txt
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
case "$scenario" in
  fresh) start; test "$(stat -c %u /data/.session)" = "$UID" ;;
  filebot-uid) filebot_fixture; start; test "$(cat /config/filebot/data/license-uid)" = "$UID" ;;
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
    test "$(su-exec "$UID:$GID" /usr/local/bin/postdl fixture title)" = custom-hook
    rm /config/filebot/postdl
    start
    test "$(su-exec "$UID:$GID" /usr/local/bin/postdl fixture title)" = packaged-hook
    test "$(cat /config/filebot/data/hook-uid)" = "$UID"
    ;;
  filebot-manual-root)
    filebot_fixture; start
    test "$(/usr/local/bin/postdl fixture title)" = packaged-hook
    test "$(cat /config/filebot/data/hook-uid)" = "$UID"
    ;;
  filebot-named-group)
    filebot_fixture; GID=users; export GID; start
    test "$(/usr/local/bin/postdl fixture title)" = packaged-hook
    test "$(cat /config/filebot/data/hook-uid)" = "$UID"
    ;;
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
  *) exit 64 ;;
esac
printf 'PASS ownership startup %s\n' "$scenario"
