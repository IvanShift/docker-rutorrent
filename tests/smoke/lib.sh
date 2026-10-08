# Shared POSIX-shell smoke harness. Source this file; callers own their resources.

fail() {
  printf 'not ok - %s\n' "$1" >&2
  if [ -n "${container:-}" ]; then
    smoke_diagnostics "$container"
  fi
  exit 1
}

smoke_require_image_arg() {
  if [ "$#" -ne 1 ]; then
    printf 'Usage: %s IMAGE\n' "$0" >&2
    exit 64
  fi
}

smoke_check_image() {
  docker image inspect "$1" >/dev/null 2>&1 || fail "image not found: $1"
}

smoke_install_traps() {
  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

smoke_run() {
  docker run --pull=never "$@"
}

# Overlay source scripts individually so compiled image-only helpers stay visible.
smoke_run_with_libexec() (
  source_dir=$1
  shift
  for source in "$source_dir"/*; do
    [ -f "$source" ] || continue
    case "$source" in *.c) continue ;; esac
    set -- --mount "type=bind,src=$source,dst=/usr/local/libexec/${source##*/},readonly" "$@"
  done
  smoke_run "$@"
)

smoke_diagnostics() {
  docker inspect --format '{{json .State}}' "$1" >&2 || true
  docker logs "$1" >&2 || true
}

# Query readiness directly, without waiting for Docker's next healthcheck tick.
# Each request and the overall loop are bounded; a failed inspect is terminal.
wait_for_health() (
  target=$1
  timeout=${2:-120}
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    state=$(docker inspect --format '{{.State.Status}}' "$target") || {
      smoke_diagnostics "$target"
      return 1
    }
    case "$state" in
      exited|dead|removing)
        smoke_diagnostics "$target"
        return 1
      ;;
      running)
        if docker exec "$target" timeout 5 /usr/local/bin/rutorrent-healthcheck >/dev/null 2>&1; then
          return 0
        fi
      ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf 'Readiness timed out after %ss: %s\n' "$timeout" "$target" >&2
      smoke_diagnostics "$target"
      return 1
    fi
    sleep 1
  done
)

# Verify the Docker-owned health state as well as direct readiness. Callers
# shorten the health interval on their disposable container to bound the gate.
wait_for_docker_health() (
  target=$1
  timeout=${2:-30}
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    state=$(docker inspect --format '{{.State.Status}}:{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$target") || {
      smoke_diagnostics "$target"
      return 1
    }
    case "$state" in
      running:healthy) return 0 ;;
      running:starting|running:unhealthy) ;;
      *) smoke_diagnostics "$target"; return 1 ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf 'Docker health timed out after %ss: %s\n' "$timeout" "$target" >&2
      smoke_diagnostics "$target"
      return 1
    fi
    sleep 1
  done
)

wait_for_exit() (
  target=$1
  timeout=${2:-30}
  deadline=$(( $(date +%s) + timeout ))
  while :; do
    running=$(docker inspect --format '{{.State.Running}}' "$target") || {
      smoke_diagnostics "$target"
      return 1
    }
    [ "$running" = false ] && return 0
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf 'Exit timed out after %ss: %s\n' "$timeout" "$target" >&2
      smoke_diagnostics "$target"
      return 1
    fi
    sleep 1
  done
)

http_status() (
  target=$1
  path=$2
  shift 2
  case "$path" in
    http://*|https://*) url=$path ;;
    *) url="http://127.0.0.1:8080${path}" ;;
  esac
  docker exec "$target" curl --noproxy '*' -sS --connect-timeout 2 --max-time 10 \
    -o /dev/null -w '%{http_code}' "$@" "$url"
)

# Call after starting a container whose startup is expected to refuse input.
smoke_expect_exit() (
  target=$1
  expected=$2
  diagnostic=$3
  wait_for_exit "$target" || fail "startup did not stop: $target"
  actual=$(docker inspect --format '{{.State.ExitCode}}' "$target") || fail 'cannot read exit code'
  if [ "$actual" != "$expected" ]; then
    smoke_diagnostics "$target"
    fail "startup exit status for $target: expected $expected, got $actual"
  fi
  logs=$(docker logs "$target" 2>&1) || fail 'cannot read startup logs'
  if ! printf '%s\n' "$logs" | grep -F "$diagnostic" >/dev/null; then
    printf '%s\n' "$logs" >&2
    fail "missing startup diagnostic: $diagnostic"
  fi
)
