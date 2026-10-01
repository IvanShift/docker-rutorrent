# Shared temporary-file cleanup for root-owned configuration helpers.
# Callers assign only paths returned by mktemp to these variables.
temporary_path=''
temporary_directory=''

atomic_cleanup() {
  if [ -n "${temporary_path}" ]; then
    rm -f -- "${temporary_path}"
    temporary_path=''
  fi
  if [ -n "${temporary_directory}" ]; then
    rm -rf -- "${temporary_directory}"
    temporary_directory=''
  fi
}

trap atomic_cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
