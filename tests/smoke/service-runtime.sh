#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || exit 64
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$1"
image_version=$(docker image inspect "$1" --format '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}')
case "${image_version#v}" in 0.16.25) ;; *) echo 'ok - native service gate # SKIP image lacks reviewed .25 contract'; exit 0 ;; esac
expected_version=${SMOKE_EXPECTED_RTORRENT_VERSION:-$image_version}
case "${expected_version#v}" in
  0.16.25) ;;
  0.9.8) echo 'ok - reviewed .25 service gate # SKIP explicit legacy contract'; exit 0 ;;
  *) echo 'ok - reviewed .25 service gate # SKIP published native version has no new .25 gate contract'; exit 0 ;;
esac
python3 "$smoke_dir/service-runtime.py" "$@"
python3 "$smoke_dir/service-runtime-tty.py" "$@"
