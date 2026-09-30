#!/bin/sh
set -eu
image=${1:?Usage: legacy-098.sh IMAGE}
name="o05-legacy-smoke-$$"
cleanup() { docker rm -f "$name" >/dev/null 2>&1 || :; }
trap cleanup EXIT HUP INT TERM
version=$(docker run --rm --entrypoint rtorrent "$image" -h 2>&1 | sed -n '1p')
[ "$version" = "Rakshasa's BitTorrent client version 0.9.8." ] || {
  echo "unexpected rTorrent version: $version" >&2
  exit 1
}
docker run -d --name "$name" --network none \
  --tmpfs /config:rw,nosuid,noexec,size=64m,mode=0755 \
  --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755 \
  -e ALLOW_UNAUTHENTICATED_HTTP=true "$image" >/dev/null
n=0
while [ "$n" -lt 15 ]; do
  if docker exec "$name" test -S /run/rtorrent/rtorrent.sock 2>/dev/null && docker exec "$name" curl -fsS http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
    "$(dirname -- "$0")/rtorrent-log-config-migration.sh" "$image" legacy
    echo 'ok - rTorrent 0.9.8 stock startup and HTTP health'
    exit 0
  fi
  n=$((n+1))
  sleep 1
done
docker logs "$name" >&2 || :
echo 'not ok - rTorrent 0.9.8 stock startup and HTTP health' >&2
exit 1
