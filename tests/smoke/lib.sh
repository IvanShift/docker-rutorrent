# Shared POSIX-shell smoke harness. Source this file; callers own their resources.

fail() {
  printf 'not ok - %s\n' "$1" >&2
  if [ -n "${container:-}" ]; then
    # A name may be reserved before creation or belong to another run.
    owner=$(docker inspect --format "{{index .Config.Labels \"$SMOKE_OWNER_LABEL\"}}" "$container" 2>/dev/null) || owner=
    if [ "$owner" = "$smoke_owner" ]; then smoke_diagnostics "$container"; fi
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

# Gates written for the reviewed native daemon run only when the image label and
# SMOKE_EXPECTED_RTORRENT_VERSION (empty or unset: that label) both name it;
# otherwise the caller ends with a skip. Like require_reviewed_native in
# smoke_harness.py, the decision reads image metadata only and starts nothing.
SMOKE_REVIEWED_NATIVE_VERSION=0.16.25
smoke_require_reviewed_native() {
  reviewed_label=$(docker image inspect "$1" --format '{{index .Config.Labels "io.github.ivanshift.rtorrent.version"}}') \
    || fail "cannot read the rTorrent version label of $1"
  reviewed_expected=${SMOKE_EXPECTED_RTORRENT_VERSION:-$reviewed_label}
  if [ "${reviewed_label#v}" != "$SMOKE_REVIEWED_NATIVE_VERSION" ] \
    || [ "${reviewed_expected#v}" != "$SMOKE_REVIEWED_NATIVE_VERSION" ]; then
    printf 'ok - %s # SKIP requires explicit reviewed .25 version\n' "$2"
    exit 0
  fi
}

smoke_install_traps() {
  trap cleanup EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
}

# One owner-label scheme for every smoke container: smoke_run and smoke_create
# label each container with this run's token, and cleanup removes a container
# only when that label proves this run created it.
SMOKE_OWNER_LABEL=io.ivanshift.smoke.owner
smoke_owner="$(basename -- "$0" .sh)-$$-$(date +%s)"

smoke_run() {
  docker run --pull=never --label "$SMOKE_OWNER_LABEL=$smoke_owner" "$@"
}

smoke_create() {
  docker create --pull=never --label "$SMOKE_OWNER_LABEL=$smoke_owner" "$@"
}

# ROOTFS_SOURCE copies a whole candidate rootfs into the container (cp -R). A
# per-file read-only bind into the same tree makes that copy fail midway, so the
# two development overlays are refused together, before any container starts.
smoke_rootfs_overlay_exclusive() (
  [ -n "${ROOTFS_SOURCE:-}" ] || return 0
  for overlay_variable in "$@"; do
    eval "overlay_value=\${$overlay_variable:-}"
    if [ -n "$overlay_value" ]; then
      printf 'ROOTFS_SOURCE cannot be combined with %s: its read-only bind would block the rootfs copy; put that candidate file into the ROOTFS_SOURCE tree instead\n' "$overlay_variable" >&2
      return 64
    fi
  done
)

# Overlay source scripts individually so compiled image-only helpers stay visible.
# A directory mount over /usr/local/libexec hides native-actor-paths and makes
# startup refuse with 78. An empty SOURCE_DIR runs the image's own libexec.
smoke_run_with_libexec() (
  source_dir=$1
  shift
  if [ -n "$source_dir" ]; then
    if [ ! -d "$source_dir" ]; then
      printf 'libexec overlay is not a directory: %s\n' "$source_dir" >&2
      return 1
    fi
    for source in "$source_dir"/*; do
      [ -f "$source" ] || continue
      case "$source" in *.c) continue ;; esac
      set -- --mount "type=bind,src=$source,dst=/usr/local/libexec/${source##*/},readonly" "$@"
    done
  fi
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

# Retry CMD once per second until it succeeds. A target that stopped or cannot
# be inspected is terminal; otherwise the wait ends after SECONDS.
smoke_wait_until() (
  target=$1
  timeout=$2
  description=$3
  shift 3
  deadline=$(( $(date +%s) + timeout ))
  until "$@"; do
    running=$(docker inspect --format '{{.State.Running}}' "$target" 2>/dev/null) || running=unknown
    if [ "$running" != true ]; then
      printf '%s: container is not running (%s): %s\n' "$description" "$running" "$target" >&2
      smoke_diagnostics "$target"
      return 1
    fi
    if [ "$(date +%s)" -ge "$deadline" ]; then
      printf '%s timed out after %ss: %s\n' "$description" "$timeout" "$target" >&2
      smoke_diagnostics "$target"
      return 1
    fi
    sleep 1
  done
)

# Wait for an actual SCGI reply from the daemon, independent of HTTP health.
wait_for_scgi() (
  target=$1
  timeout=${2:-40}
  scgi_ready() {
    docker exec --user 991:991 "$target" php85 /tests/smoke/service-rpc.php system.pid >/dev/null 2>&1
  }
  smoke_wait_until "$target" "$timeout" 'SCGI readiness' scgi_ready
)

# Remove only the exact container this run created, with its anonymous volumes:
# smoke_remove_owned TARGET OWNED_ID OWNER_LABEL OWNER [stop|force]. "stop"
# (default) stops gracefully first, "force" removes at once. Nothing recorded or
# already gone is success. A container without our ID and owner label is left
# alone and reported with status 1, as is a failed removal. Under set -e, a
# failing command in an EXIT trap ends the shell with its own status, in dash
# and bash alike, replacing the status the script was exiting with: a trap that
# must not change that status adds "|| true".
# OWNER_LABEL must be $SMOKE_OWNER_LABEL; callers pass an explicit OWNER only
# for a token of their own, otherwise they use the wrappers below.
smoke_remove_owned() (
  target=$1
  owned_id=$2
  owner_label=$3
  owner=$4
  mode=${5:-stop}
  if [ "$owner_label" != "$SMOKE_OWNER_LABEL" ]; then
    printf 'unknown smoke owner label %s; use %s\n' "$owner_label" "$SMOKE_OWNER_LABEL" >&2
    return 64
  fi
  [ -n "$owned_id" ] || return 0
  current=$(docker inspect "$target" --format "{{.Id}} {{index .Config.Labels \"$owner_label\"}}" 2>/dev/null) || return 0
  if [ "$current" != "$owned_id $owner" ]; then
    printf 'refusing to remove container %s: not the one this run created\n' "$target" >&2
    return 1
  fi
  case "$mode" in
    stop)
      docker stop --timeout 60 "$owned_id" >/dev/null 2>&1 || true
      docker rm -v "$owned_id" >/dev/null
    ;;
    force) docker rm -f -v "$owned_id" >/dev/null ;;
    *) printf 'unknown container removal mode: %s\n' "$mode" >&2; return 64 ;;
  esac
)

# Remove one container of this run by name or ID: smoke_remove_container TARGET [stop|force].
smoke_remove_container() (
  target=$1
  id=$(docker inspect --format '{{.Id}}' "$target" 2>/dev/null) || return 0
  smoke_remove_owned "$target" "$id" "$SMOKE_OWNER_LABEL" "$smoke_owner" "${2:-force}"
)

# Remove every container this run created, named or not: the harness cleanup.
smoke_remove_run_containers() (
  ids=$(docker ps -aq --no-trunc --filter "label=$SMOKE_OWNER_LABEL=$smoke_owner") || return 1
  status=0
  for id in $ids; do
    smoke_remove_owned "$id" "$id" "$SMOKE_OWNER_LABEL" "$smoke_owner" "${1:-force}" || status=1
  done
  return "$status"
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
