#!/bin/sh
set -eu
[ -f /etc/rtorrent-legacy ] || { echo 'not ok - legacy image marker'; exit 1; }
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT HUP INT TERM
actual="$work/rtorrent.rc"
cat > "$actual" <<'RC'
protocol.encryption.set = allow
network.listen.port.range.set = 45000-45000
network.listen.port.random.set = no
network.prefer.ipv6.set = yes
network.block.ipv6.set = no
dht.override_port.set = 6881
RC
/usr/local/bin/update-config "$actual" "$work/missing.php" >/dev/null
grep -Fxq 'encryption = allow_incoming,try_outgoing,enable_retry' "$actual"
grep -Fxq 'port_range = 45000-45000' "$actual"
grep -Fxq 'port_random = no' "$actual"
grep -Fxq 'dht_port = 6881' "$actual"
[ "$(wc -l < "$actual")" -eq 4 ] || { echo 'not ok - legacy stock conversion'; exit 1; }
first=$(sha256sum "$actual" | cut -d' ' -f1)
/usr/local/bin/update-config "$actual" "$work/missing.php" >/dev/null
[ "$first" = "$(sha256sum "$actual" | cut -d' ' -f1)" ] || { echo 'not ok - legacy idempotence'; exit 1; }
echo 'ok - legacy stock conversion and idempotence'
printf '%s\n' 'network.prefer.ipv6.set = no' >> "$actual"
first=$(sha256sum "$actual" | cut -d' ' -f1)
if /usr/local/bin/update-config "$actual" "$work/missing.php" >/dev/null 2>"$work/error"; then
  echo 'not ok - custom IPv6 assignment refused'; exit 1
fi
[ "$first" = "$(sha256sum "$actual" | cut -d' ' -f1)" ] || { echo 'not ok - custom IPv6 atomic refusal'; exit 1; }
grep -Fq 'Incompatible rTorrent network assignment' "$work/error"
echo 'ok - unsupported custom IPv6 assignment refused atomically'
