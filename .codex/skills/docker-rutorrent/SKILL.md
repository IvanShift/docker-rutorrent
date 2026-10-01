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

The old `overrides/rutorrent/` overlay has been removed. Do not recreate it for behavior fixes; recover old files from git history only for comparison and port the minimal still-needed change into the fork.

## Fork PHP lint without host PHP

```sh
docker run --rm --entrypoint php85 \
  --mount type=bind,src=/home/dev/Documents/my_projects/ruTorrent,dst=/src,readonly \
  -w /src rutorrent-local:dev \
  -l plugins/rutracker_check/check.php
```
