#!/bin/sh
# Exercise public feed links and relative redirects through actual nginx/FPM.
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_require_image_arg "$@"
image=$1
smoke_check_image "$image"
version=$(docker image inspect "$image" --format '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}')
case "${version#v}" in
  0.16.27|0.9.8) ;;
  *) echo 'ok - public URL gate # SKIP image is outside the authorized .27/.98 runtime targets'; exit 0 ;;
esac
if [ "${version#v}" = 0.9.8 ] && ! smoke_run --rm --network none --entrypoint /bin/sh "$image" \
  -c 'grep -F "function publicBaseURL(" /rutorrent/app/php/utility/requests.php >/dev/null'; then
  echo 'ok - public URL gate # SKIP historical legacy image has no new application contract'
  exit 0
fi
workspace=$(mktemp -d)
suffix="$$-$(date +%s)"
cleanup() { smoke_remove_run_containers stop || true; rm -rf "$workspace"; }
smoke_install_traps
for source in environment configuration; do
  container="rutorrent-public-url-$suffix-$source"
  base='https://public.example:9443/torrents/'
  setting=$base
  if [ "$source" = configuration ]; then
    base='https://configured.example:9443/local-prefix/'
    setting=''
  fi
  owned_id=$(smoke_run -d --network none --name "$container" \
    -e HTTP_AUTH=false -e ALLOW_UNAUTHENTICATED_HTTP=true -e ENABLE_RPC2=true \
    -e HTTP_ALLOWED_HOSTS=internal.example \
    -e "RU_PUBLIC_URL=$setting" "$image")
  docker inspect "$owned_id" --format 'public-url resource {{.Id}} image={{.Image}} mounts={{json .Mounts}}'
  if [ "${version#v}" = 0.9.8 ]; then
    # Historical images predate the readiness helper; the HTTP contract stays real.
    ready=false
    deadline=$(( $(date +%s) + 120 ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
      [ "$(docker inspect -f '{{.State.Running}}' "$owned_id")" = true ] || break
      if docker exec "$owned_id" curl --noproxy '*' -fsS --max-time 2 http://127.0.0.1:8080/healthz >/dev/null 2>&1; then
        ready=true
        break
      fi
      sleep 1
    done
    [ "$ready" = true ] || fail 'legacy public URL HTTP readiness'
  else
    wait_for_health "$owned_id" || fail 'public URL image readiness'
  fi
  docker exec -i "$owned_id" sh -eu -c 'cat > /config/rutorrent/conf/config.local.php' <<EOF
<?php
\$publicURL = 'https://configured.example:9443/local-prefix/';
EOF
  docker exec "$owned_id" curl --noproxy '*' -fsS --max-time 10 \
    -H 'Host: internal.example:8080' -H 'X-Forwarded-Host: ignored.example' \
    'http://127.0.0.1:8080/plugins/feeds/action.php?mode=all' > "$workspace/feed.xml"
  python3 - "$workspace/feed.xml" "$base" <<'PY'
import sys,xml.etree.ElementTree as ET
root=ET.parse(sys.argv[1]).getroot(); channel=root.find('channel'); base=sys.argv[2]
assert channel is not None
assert channel.findtext('link')==base, channel.findtext('link')
assert channel.findtext('image/link')==base, channel.findtext('image/link')
assert channel.findtext('image/url')==base+'images/logo.png', channel.findtext('image/url')
PY
  printf 'ok - actual FPM feed public base from %s with rewritten internal Host\n' "$source"
  docker exec "$owned_id" curl --noproxy '*' -sS --max-time 10 -D - -o /dev/null \
    -H 'Host: internal.example:8080' -H 'Origin: http://internal.example:8080' \
    --data '' http://127.0.0.1:8080/php/addtorrent.php > "$workspace/addtorrent.headers"
  grep -Eq '^HTTP/[^ ]+ 302 ' "$workspace/addtorrent.headers" || fail 'empty addtorrent POST status'
  grep -Ei '^Location: \./addtorrent\.php\?' "$workspace/addtorrent.headers" >/dev/null || fail 'relative addtorrent redirect'
  printf 'ok - actual empty addtorrent POST keeps a relative302 behind Host rewrite\n'
  for door in /plugins/httprpc/action.php /RPC2; do
    docker exec "$owned_id" curl --noproxy '*' -fsS --max-time 10 \
      -H 'Host: internal.example:8080' -H 'Origin: http://internal.example:8080' \
      -H 'Content-Type: text/xml' --data '<?xml version="1.0"?><methodCall><methodName>system.client_version</methodName><params></params></methodCall>' \
      "http://127.0.0.1:8080$door" > "$workspace/rpc.xml"
    python3 - "$workspace/rpc.xml" "${version#v}" <<'PY'
import sys,xml.etree.ElementTree as ET
root=ET.parse(sys.argv[1]).getroot()
assert root.find('fault') is None
assert sys.argv[2] in ''.join(root.itertext()), ''.join(root.itertext())
PY
    printf 'ok - actual raw XMLRPC %s serves the expected daemon version\n' "$door"
  done
  smoke_remove_container "$owned_id" stop
 done
