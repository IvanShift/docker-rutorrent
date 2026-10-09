#!/bin/sh
set -eu
scenario=$1
if [ -f /actor-helper ]; then cp /actor-helper /usr/local/libexec/native-actor-paths; chmod 0755 /usr/local/libexec/native-actor-paths; fi
checker=/usr/local/libexec/native-actor-paths
marker=/usr/local/share/rutorrent-native-actor-paths-required
cp -a /candidate-nginx/. /etc/nginx/
mkdir -p /rutorrent/app/plugins/retrackers /rutorrent/app/plugins/autotools /rutorrent/app/plugins/erasedata
printf '<?php // fixture actor\n' > /rutorrent/app/plugins/retrackers/update.php
printf '<?php // fixture actor\n' > /rutorrent/app/plugins/retrackers/dispatch.php
printf '<?php // fixture actor\n' > /rutorrent/app/plugins/autotools/move_worker.php
printf '<?php // fixture actor\n' > /rutorrent/app/plugins/erasedata/collect_worker.php
"$checker" normalize --require-all
printf 'version=1\n' > "$marker"
chmod 0644 "$marker"
expected=78
php_expected=no
case "$scenario" in custom-delete|custom-unsafe-replacement|full-php-wrapper) ;;
  *)
    rm /usr/bin/php85
    printf '#!/bin/sh\nid -u > /php-executed\nexit 81\n' > /usr/bin/php85
    chmod 0755 /usr/bin/php85
    ;;
esac
case "$scenario" in
  valid-root) expected=81; php_expected=yes ;;
  earlier-php-wrapper|full-php-wrapper)
    mkdir /fixture-tools
    printf '#!/bin/sh\nid -u > /data/path-php-executed\nexit 82\n' > /fixture-tools/php85
    chmod 0755 /fixture-tools/php85
    PATH="/fixture-tools:$PATH"; export PATH
    if [ "$scenario" = earlier-php-wrapper ]; then expected=81; php_expected=yes
    else
      expected=0
      mkdir -p /config/custom_plugins/fixture
      printf 'trusted fixture\n' > /config/custom_plugins/fixture/plugin.info
    fi
    ;;
  valid-service-actors) chown "$UID:$GID" /rutorrent/app/plugins/erasedata /rutorrent/app/plugins/erasedata/collect_worker.php /rutorrent/app/plugins/autotools /rutorrent/app/plugins/autotools/move_worker.php /rutorrent/app/plugins/retrackers /rutorrent/app/plugins/retrackers/update.php /rutorrent/app/plugins/retrackers/dispatch.php; expected=81; php_expected=yes ;;
  named-group) GID=users; export GID; expected=81; php_expected=yes ;;
  php-foreign-owner) chown 15555 /usr/bin/php85 ;;
  php-service-owner) chown "$UID" /usr/bin/php85 ;;
  php-no-service-execute) chmod 0744 /usr/bin/php85 ;;
  php-write-mode) chmod 0775 /usr/bin/php85 ;;
  php-symlink) mv /usr/bin/php85 /usr/bin/php85-fixture; ln -s php85-fixture /usr/bin/php85 ;;
  php-hardlink) ln /usr/bin/php85 /usr/bin/php85-fixture ;;
  php-setid) chmod 4755 /usr/bin/php85 ;;
  app-parent-write) chmod 0775 /rutorrent/app ;;
  update-write) chmod 0664 /rutorrent/app/plugins/retrackers/update.php ;;
  dispatch-symlink) rm /rutorrent/app/plugins/retrackers/dispatch.php; ln -s update.php /rutorrent/app/plugins/retrackers/dispatch.php ;;
  move-write) chmod 0664 /rutorrent/app/plugins/autotools/move_worker.php ;;
  move-symlink) rm /rutorrent/app/plugins/autotools/move_worker.php; ln -s ../retrackers/update.php /rutorrent/app/plugins/autotools/move_worker.php ;;
  move-hardlink) ln /rutorrent/app/plugins/autotools/move_worker.php /move-alias ;;
  move-foreign-owner) chown 15555 /rutorrent/app/plugins/autotools/move_worker.php ;;
  move-parent-write) chmod 0775 /rutorrent/app/plugins/autotools ;;
  move-parent-symlink) mv /rutorrent/app/plugins/autotools /held-autotools; ln -s /held-autotools /rutorrent/app/plugins/autotools ;;
  move-only-no-marker) rm /rutorrent/app/plugins/erasedata/collect_worker.php "$marker" /rutorrent/app/plugins/retrackers/dispatch.php ;;
  move-only-no-checker) rm /rutorrent/app/plugins/erasedata/collect_worker.php "$marker" /rutorrent/app/plugins/retrackers/dispatch.php "$checker" ;;
  collect-write) chmod 0664 /rutorrent/app/plugins/erasedata/collect_worker.php ;;
  collect-symlink) rm /rutorrent/app/plugins/erasedata/collect_worker.php; ln -s ../retrackers/update.php /rutorrent/app/plugins/erasedata/collect_worker.php ;;
  collect-hardlink) ln /rutorrent/app/plugins/erasedata/collect_worker.php /collect-alias ;;
  collect-foreign-owner) chown 15555 /rutorrent/app/plugins/erasedata/collect_worker.php ;;
  collect-parent-write) chmod 0775 /rutorrent/app/plugins/erasedata ;;
  collect-parent-symlink) mv /rutorrent/app/plugins/erasedata /held-erasedata; ln -s /held-erasedata /rutorrent/app/plugins/erasedata ;;
  missing-collect) rm /rutorrent/app/plugins/erasedata/collect_worker.php ;;
  collect-only-no-marker) rm "$marker" /rutorrent/app/plugins/retrackers/update.php /rutorrent/app/plugins/retrackers/dispatch.php /rutorrent/app/plugins/autotools/move_worker.php ;;
  collect-only-no-checker) rm "$marker" /rutorrent/app/plugins/retrackers/update.php /rutorrent/app/plugins/retrackers/dispatch.php /rutorrent/app/plugins/autotools/move_worker.php "$checker" ;;
  missing-move) rm /rutorrent/app/plugins/autotools/move_worker.php ;;
  missing-update) rm /rutorrent/app/plugins/retrackers/update.php ;;
  marker-missing-dispatch) rm /rutorrent/app/plugins/retrackers/dispatch.php ;;
  marker-empty-app) rm /rutorrent/app/plugins/retrackers/update.php /rutorrent/app/plugins/retrackers/dispatch.php ;;
  marker-symlink) rm "$marker"; ln -s /etc/passwd "$marker" ;;
  marker-fifo) rm "$marker"; mkfifo "$marker" ;;
  marker-write) chmod 0664 "$marker" ;;
  marker-foreign-owner) chown "$UID" "$marker" ;;
  missing-checker) rm "$checker" ;;
  override-php) RUTORRENT_NATIVE_PHP=/bin/true; export RUTORRENT_NATIVE_PHP; expected=64 ;;
  override-php-empty) RUTORRENT_NATIVE_PHP=; export RUTORRENT_NATIVE_PHP; expected=64 ;;
  override-app) RUTORRENT_NATIVE_APP_ROOT=/operator-app; export RUTORRENT_NATIVE_APP_ROOT; expected=64 ;;
  override-app-empty) RUTORRENT_NATIVE_APP_ROOT=; export RUTORRENT_NATIVE_APP_ROOT; expected=64 ;;
  old-published) rm /rutorrent/app/plugins/erasedata/collect_worker.php "$marker" /rutorrent/app/plugins/retrackers/dispatch.php /rutorrent/app/plugins/autotools/move_worker.php "$checker"; expected=81; php_expected=yes ;;
  custom-delete)
    printf '["retrackers"]' > /run/rutorrent-custom-plugins.json
    ;;
  custom-unsafe-replacement)
    printf '["retrackers"]' > /run/rutorrent-custom-plugins.json
    mkdir -p /config/custom_plugins/retrackers
    printf '<?php // unsafe replacement\n' > /config/custom_plugins/retrackers/update.php
    printf '<?php // replacement\n' > /config/custom_plugins/retrackers/dispatch.php
    chmod 0664 /config/custom_plugins/retrackers/update.php
    ;;
  *) exit 64 ;;
esac
actor_metadata() {
  for path in /usr/bin/php85 /rutorrent/app /rutorrent/app/plugins/erasedata /rutorrent/app/plugins/erasedata/collect_worker.php /rutorrent/app/plugins/autotools /rutorrent/app/plugins/autotools/move_worker.php /rutorrent/app/plugins/retrackers /rutorrent/app/plugins/retrackers/update.php /rutorrent/app/plugins/retrackers/dispatch.php "$marker"; do
    if [ -e "$path" ] || [ -L "$path" ]; then stat -c '%n:%d:%i:%u:%g:%a:%h:%F' "$path"; else printf '%s:missing\n' "$path"; fi
  done
}
before_metadata=$(actor_metadata)
status=0
/usr/local/bin/startup /bin/true > /startup-output 2>&1 || status=$?
case "$scenario" in custom-delete|custom-unsafe-replacement|full-php-wrapper) ;; *) test "$before_metadata" = "$(actor_metadata)" ;; esac
if [ "$status" -ne "$expected" ]; then cat /startup-output >&2; echo "FAIL actor guard $scenario expected $expected got $status" >&2; exit 1; fi
if [ "$php_expected" = yes ]; then test "$(cat /php-executed)" = 0
else test ! -e /php-executed; fi
if [ -e /data/path-php-executed ]; then echo "FAIL actor guard $scenario alternate PHP executed" >&2; exit 1; fi
if [ "$expected" = 78 ]; then grep -Eq 'Cannot attest native worker paths|Native worker path checker is unavailable|Unsafe immutable native actor requirement marker' /startup-output
elif [ "$expected" = 64 ]; then grep -Eq 'RUTORRENT_NATIVE_(PHP|APP_ROOT) must be' /startup-output; fi
case "$scenario" in
  full-php-wrapper) test "$(cat /rutorrent/app/plugins/fixture/plugin.info)" = "trusted fixture" ;;
  custom-delete) test ! -e /rutorrent/app/plugins/retrackers; grep -F 'Update old configurations' /startup-output >/dev/null ;;
  custom-unsafe-replacement) test "$(stat -c %a /rutorrent/app/plugins/retrackers/update.php)" = 664; grep -F 'Update old configurations' /startup-output >/dev/null ;;
esac
printf 'PASS native actor startup %s (%s)\n' "$scenario" "$status"
