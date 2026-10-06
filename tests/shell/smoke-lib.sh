#!/bin/sh
set -eu
repo=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
. "$repo/tests/smoke/lib.sh"
workspace=$(mktemp -d)
cleanup() { rm -rf "$workspace"; }
smoke_install_traps

# A deterministic clock gives each refusal a real positive wait budget without
# sleeping. Any lost early return becomes an observable loop rather than a hang.
date() { cat "$workspace/clock"; }
sleep() {
  current=$(cat "$workspace/clock")
  printf '%s\n' "$((current + 1))" > "$workspace/clock"
  printf 'sleep\n' >> "$workspace/calls"
}
for scenario in stopped inspect-error timeout healthy; do
  printf '0\n' > "$workspace/clock"
  : > "$workspace/calls"
  status=0
  (
    docker() {
      printf '%s\n' "$1" >> "$workspace/calls"
      case "$1" in
        inspect)
          case "$scenario" in
            stopped) printf 'exited\n' ;;
            inspect-error) return 42 ;;
            timeout|healthy) printf 'running\n' ;;
          esac
        ;;
        exec) [ "$scenario" = healthy ] ;;
        logs) printf 'known-startup-diagnostic\n' ;;
      esac
    }
    wait_for_health fake 10
  ) > "$workspace/$scenario.log" 2>&1 || status=$?
  if [ "$scenario" = healthy ]; then
    [ "$status" = 0 ] || fail 'direct readiness rejected socket and HTTP'
    grep -Fx exec "$workspace/calls" >/dev/null || fail 'readiness omitted the actual health command'
  else
    [ "$status" = 1 ] || fail "$scenario readiness unexpectedly passed"
    grep -F known-startup-diagnostic "$workspace/$scenario.log" >/dev/null || fail "$scenario missing startup diagnostics"
    case "$scenario" in
      stopped|inspect-error)
        if grep -Fx sleep "$workspace/calls" >/dev/null; then fail "$scenario did not leave readiness immediately"; fi
      ;;
      timeout)
        [ "$(cat "$workspace/clock")" = 10 ] || fail 'readiness timeout did not exhaust the bounded budget'
      ;;
    esac
  fi
done

printf '0\n' > "$workspace/clock"
: > "$workspace/calls"
status=0
(
  docker() {
    case "$1" in inspect) return 42 ;; logs) printf 'known-exit-diagnostic\n' ;; esac
  }
  wait_for_exit fake 10
) > "$workspace/exit.log" 2>&1 || status=$?
[ "$status" = 1 ] || fail 'failed inspect did not stop exit wait'
grep -F known-exit-diagnostic "$workspace/exit.log" >/dev/null || fail 'exit wait diagnostics'
if grep -Fx sleep "$workspace/calls" >/dev/null; then fail 'inspect error did not leave exit wait immediately'; fi

for scenario in docker-healthy docker-missing docker-inspect-error; do
  printf '0\n' > "$workspace/clock"
  : > "$workspace/calls"
  status=0
  (
    docker() {
      case "$1:$scenario" in
        inspect:docker-healthy) printf 'running:healthy\n' ;;
        inspect:docker-missing) printf 'running:\n' ;;
        inspect:docker-inspect-error) return 42 ;;
        logs:*) printf 'known-docker-health-diagnostic\n' ;;
      esac
    }
    wait_for_docker_health fake 10
  ) > "$workspace/$scenario.log" 2>&1 || status=$?
  case "$scenario" in
    docker-healthy) [ "$status" = 0 ] || fail 'Docker healthy state was refused' ;;
    *) [ "$status" = 1 ] || fail "$scenario was accepted" ;;
  esac
  if grep -Fx sleep "$workspace/calls" >/dev/null; then fail "$scenario did not return immediately"; fi
done

status=0
sh -c '. "$1"; trap_file=$2; cleanup() { printf "cleanup\n" >> "$trap_file"; }; smoke_install_traps; kill -TERM $$' sh "$repo/tests/smoke/lib.sh" "$workspace/trap.log" || status=$?
[ "$status" = 143 ] || fail 'TERM trap exit status'
[ "$(wc -l < "$workspace/trap.log")" = 1 ] || fail 'TERM cleanup invocation count'
printf 'ok - shared smoke readiness, immediate refusals, bounded waits, Docker health and TERM cleanup\n'
