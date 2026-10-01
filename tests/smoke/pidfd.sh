#!/bin/sh
set -eu
smoke_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  printf 'Usage: %s IMAGE [PLATFORM]\n' "$0" >&2
  exit 64
fi
exec "$smoke_dir/native-run.sh" pidfd "$1" "${2:-}"
