# Shared launcher for disposable startup/configuration checks. Source lib.sh first.
# STARTUP_SOURCE, UPDATE_CONFIG_SOURCE, RUNTIME_LIB_SOURCE, LIBEXEC_SOURCE and RTORRENT_RC_SOURCE
# optionally bind candidate files into an existing local image for development.
# ROOTFS_SOURCE instead copies a candidate overlay into the disposable container;
# TOKENIZER_APK_SOURCE permits installing a locally cached tokenizer APK offline.

bootstrap_prolog=$(cat <<'SH'
startup_log=/tmp/startup-smoke.log
: > "$startup_log"
trap 'rc=$?; if [ "$rc" -ne 0 ]; then printf "case exit status: %s\n" "$rc" >&2; cat "$startup_log" >&2; fi' EXIT
startup() { sh /usr/local/bin/startup true > "$startup_log" 2>&1; }
refused() {
  expected=$1
  result=0
  startup || result=$?
  if [ "$result" -ne 78 ]; then
    printf 'expected startup status 78, got %s\n' "$result" >&2
    exit 1
  fi
  if ! grep -F "$expected" "$startup_log" >/dev/null; then
    printf 'missing startup refusal: %s\n' "$expected" >&2
    exit 1
  fi
}
snapshot() { stat -c '%d:%i:%u:%g:%a:%h' "$1"; sha256sum "$1"; }
unchanged() { test "$(snapshot "$1")" = "$2"; }
SH
)

smoke_startup_case() (
  case_image=$1
  case_script=$2
  shift 2
  set -- --rm --network none --mount "type=bind,src=$smoke_dir,dst=/tests/smoke,readonly" "$@"
  if [ -n "${BOOTSTRAP_DATA_VOLUME:-}" ]; then
    set -- "$@" --mount "type=volume,src=$BOOTSTRAP_DATA_VOLUME,dst=/data"
  else
    set -- "$@" --tmpfs /data:rw,nosuid,noexec,size=64m,mode=0755
  fi
  if [ -n "${STARTUP_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$STARTUP_SOURCE,dst=/usr/local/bin/startup,readonly"
  fi
  if [ -n "${UPDATE_CONFIG_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$UPDATE_CONFIG_SOURCE,dst=/usr/local/bin/update-config,readonly"
  fi
  if [ -n "${RUNTIME_LIB_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$RUNTIME_LIB_SOURCE,dst=/usr/local/lib/rutorrent,readonly"
  fi
  if [ -n "${LIBEXEC_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$LIBEXEC_SOURCE,dst=/usr/local/libexec,readonly"
  fi
  if [ -n "${TOKENIZER_APK_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$TOKENIZER_APK_SOURCE,dst=/candidate-apks,readonly"
    case_script='apk add --no-network /candidate-apks/php85-tokenizer-*.apk >/dev/null
'"$case_script"
  fi
  if [ -n "${RTORRENT_RC_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$RTORRENT_RC_SOURCE,dst=/usr/local/share/rutorrent-defaults/rtorrent.rc,readonly"
  fi
  if [ -n "${ROOTFS_SOURCE:-}" ]; then
    set -- "$@" --mount "type=bind,src=$ROOTFS_SOURCE,dst=/candidate-rootfs,readonly"
    case_script='cp -R /candidate-rootfs/. /
mkdir -p /usr/local/share/rutorrent-defaults
cp /candidate-rootfs/home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc
'"$case_script"
  fi
  smoke_run "$@" --entrypoint sh "$case_image" -ec "$bootstrap_prolog
$case_script"
)
