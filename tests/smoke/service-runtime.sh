#!/bin/sh
set -eu
[ "$#" -ge 1 ] && [ "$#" -le 2 ] || exit 64
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$smoke_dir/lib.sh"
smoke_check_image "$1"
smoke_require_reviewed_native "$1" 'service runtime gates'
python3 "$smoke_dir/service-runtime.py" "$@"
python3 "$smoke_dir/service-runtime-tty.py" "$@"
