#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps
for scenario in stopped inspect-error timeout; do
  status=0
  (
    docker() {
      case "$1" in
        inspect)
          case "$scenario" in
            stopped) printf 'exited\n' ;;
            inspect-error) return 42 ;;
            timeout) printf 'running\n' ;;
          esac
        ;;
        exec) return 1 ;;
        logs) printf 'known-startup-diagnostic\n' ;;
      esac
    }
    wait_for_health fake 0
  ) > "$workspace/$scenario.log" 2>&1 || status=$?
  [ "$status" = 1 ] || fail "$scenario readiness unexpectedly passed"
  grep -F known-startup-diagnostic "$workspace/$scenario.log" >/dev/null || fail "$scenario missing startup diagnostics"
done
(
  docker() { case "$1" in inspect) printf 'running\n' ;; exec) return 0 ;; *) return 1 ;; esac; }
  wait_for_health fake 0
) || fail 'direct readiness did not accept socket and HTTP'
status=0
(
  docker() { case "$1" in inspect) return 42 ;; logs) printf 'known-exit-diagnostic\n' ;; esac; }
  wait_for_exit fake
) > "$workspace/exit.log" 2>&1 || status=$?
[ "$status" = 1 ] || fail 'failed inspect did not stop exit wait'
grep -F known-exit-diagnostic "$workspace/exit.log" >/dev/null || fail 'exit wait diagnostics'
status=0
sh -c '. "$1"; trap_file=$2; cleanup() { printf "cleanup\n" >> "$trap_file"; }; smoke_install_traps; kill -TERM $$' sh "$repo/tests/smoke/lib.sh" "$workspace/trap.log" || status=$?
[ "$status" = 143 ] || fail 'TERM trap exit status'
[ "$(wc -l < "$workspace/trap.log")" = 1 ] || fail 'TERM cleanup invocation count'
printf 'ok - shared smoke direct readiness, stopped/inspect/timeout diagnostics, exit errors and TERM cleanup\n'
