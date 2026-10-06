#!/bin/sh
set -eu
cd /proof
trial() {
  mode=$1 payload=$2 count=$3 thread_control=$4
  i=1
  while [ "$i" -le "$count" ]; do
    output=$mode-$payload-$thread_control-$i.torrent
    log=$output.log
    set -- -v -l 18 -a http://fixture.invalid/announce -o "$output" "$payload"
    [ "$thread_control" = default ] || set -- -t 1 "$@"
    status=0
    if [ "$mode" = file ]; then
      timeout -s KILL 10 mktorrent "$@" > "$log" 2>&1 || status=$?
    else
      timeout -s KILL 10 sh -ec 'set -o pipefail; mktorrent "$@" 2>&1 | cat' sh "$@" > "$log" 2>&1 || status=$?
    fi
    if [ "$status" -ne 0 ]; then
      cat "$log" >&2
      printf 'not ok - mktorrent %s %s %s trial%s status%s\n' "$mode" "$payload" "$thread_control" "$i" "$status" >&2
      exit 1
    fi
    i=$((i + 1))
  done
}
# File and pipe stdout both exposed the pinned printer's cancellation race.
trial file small 24 default
trial pipe small 24 default
trial pipe small 8 single
trial pipe multiple 8 default
