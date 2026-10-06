#!/bin/sh
# Helper-only Docker gate; no daemon, published credentials or external network.
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 3 ] || { echo 'Usage: http-passwd-path.sh IMAGE [SERVICE_RUNTIME_BINARY [GEN_HTTP_PASSWD_SCRIPT]]' >&2; exit 64; }
image=$1
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
helper=${2:-}
generator=${3:-$repo/rootfs/usr/local/bin/gen-http-passwd}
owner="http-passwd-path-$(cat /proc/sys/kernel/random/uuid)"
keep_cidfile=false
if [ -n "${HTTP_PASSWD_PATH_CIDFILE:-}" ]; then cidfile=$HTTP_PASSWD_PATH_CIDFILE; keep_cidfile=true
else cidfile=$(mktemp); fi
rm -f "$cidfile"
cleanup() {
    if [ -s "$cidfile" ]; then
        id=$(cat "$cidfile")
        label=$(docker inspect --format '{{index .Config.Labels "io.github.ivanshift.http-passwd-path-owner"}}' "$id" 2>/dev/null || true)
        if [ "$label" = "$owner" ]; then docker rm -f -v "$id" >/dev/null; fi
    fi
    if [ "$keep_cidfile" != true ]; then rm -f "$cidfile"; fi
}
trap cleanup EXIT HUP INT TERM
set -- docker run --rm --pull=never --network none --name "$owner" --label "io.github.ivanshift.http-passwd-path-owner=$owner" --cidfile "$cidfile" --entrypoint /bin/sh \
    --mount "type=bind,src=$generator,dst=/candidate-gen-http-passwd,readonly" \
    --mount "type=bind,src=$repo/rootfs/usr/local/lib/rutorrent/startup.php,dst=/usr/local/lib/rutorrent/startup.php,readonly" \
    --mount "type=bind,src=$repo/tests/smoke/fixtures/http-passwd-path,dst=/http-passwd-fixtures,readonly"
if [ -n "$helper" ]; then set -- "$@" --mount "type=bind,src=$helper,dst=/usr/local/libexec/service-runtime,readonly"; fi
"$@" "$image" /http-passwd-fixtures/run.sh
