---
name: docker-rutorrent
description: Use when changing or reviewing /home/dev/Documents/my_projects/docker-rutorrent, especially Dockerfile dependency pins, rTorrent/libtorrent/PHP/nginx/s6 runtime configuration, rootfs scripts, image builds, or deciding whether a ruTorrent behavior change belongs in the ruTorrent fork instead.
---

# docker-rutorrent

Read the repository's `AGENTS.md` before changing anything. It holds the rules for dependency updates, the runtime model (build modes, exit codes 64 and 78, `conf-dist`), the test entry points, commit scope and parallel work. This skill only adds the boundary check and a lint recipe.

## Boundary

This repository owns the container image, not active ruTorrent application behavior.

Keep here:

- Dockerfile dependency pins and build stages
- rTorrent/libtorrent/PHP/nginx/s6 runtime configuration
- `rootfs/` startup scripts and `/config` migration behavior
- build-time fetching of third-party plugins such as `geoip2` and `ratiocolor`
- image-level tests

Put ruTorrent behavior changes in `/home/dev/Documents/my_projects/ruTorrent`:

- ruTorrent PHP, JavaScript, CSS, and UI behavior
- bundled plugin fixes
- `rutracker_check`
- `httprpc`, `xmlrpc.php`, and `Snoopy.class.inc` compatibility

Native rTorrent changes go to the `rutorrent-native` branch of `/home/dev/Documents/my_projects/rtorrent`.
The libtorrent fork is cloned at `/home/dev/Documents/my_projects/libtorrent` (`origin` = IvanShift/libtorrent, `upstream` = rakshasa) for upstream libtorrent PRs; `LIBTORRENT_VERSION` still pins upstream libtorrent, not this fork.

For a 0.16.x release (0.16.27 since the 2026-10-28 pins), keep `RTORRENT_BRANCH`, its reviewed fork SHA, and `LIBTORRENT_BRANCH`/`LIBTORRENT_VERSION` coherent with `configure.ac` (rTorrent 0.16.27 requires libtorrent >= 0.16.27). Publish a ruTorrent ref that accepts the version and worker/helper ABI before Docker source pins move: the ruTorrent XML-RPC proxy refuses a daemon newer than its `NEWEST_016_MINOR` with 503, and `tests/smoke/default.sh` fails at the RPC2 version step. Focused local exact-byte overlays require explicit expected app/native smoke revisions and a separate byte/mode manifest; a local image label does not publish the dirty source.

The old `overrides/rutorrent/` overlay has been removed. Do not recreate it for behavior fixes; recover old files from git history only for comparison and port the minimal still-needed change into the fork.

For final uncommitted-source integration, use verified named-context exports through
the production multistage builder and keep a narrow source-fetch-only override.
Replay the packaged app/native/helpers directly; earlier overlays and O2 lab
binaries do not establish final O3/LTO image behavior. Read AGENTS.md "Reviewed
Native Actors and Image Evidence" and "Service and HTTP Contracts" when changing
actor admission, ownership, supervisors, watchdog, FPM health or public URLs.
These sections preserve the selected service UID, fixed executable paths, private
metadata trust and exact resource-cleanup boundary. Do not change published pins
or infer push/deploy authorization merely to run a local candidate.

## Fork PHP lint without host PHP

```sh
docker image inspect rutorrent-local:dev >/dev/null
docker run --rm --pull=never --entrypoint php85 \
  --mount type=bind,src=/home/dev/Documents/my_projects/ruTorrent,dst=/src,readonly \
  -w /src rutorrent-local:dev \
  -l plugins/rutracker_check/check.php
```
