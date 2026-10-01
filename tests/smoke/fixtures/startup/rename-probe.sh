#!/bin/sh
# Harmless capability probe test double, restricted to its configured data directory.
set -eu
[ "$1" = --probe ]
id -u > "$2/.startup-probe-uid"
printf '%s\n' "$2" > "$2/.startup-probe-dir"
exit "${PROBE_STATUS:-3}"
