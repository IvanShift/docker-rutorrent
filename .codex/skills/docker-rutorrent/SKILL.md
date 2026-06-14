---
name: docker-rutorrent
description: Use when changing or reviewing /home/dev/Documents/my_projects/docker-rutorrent, especially Dockerfile dependency pins, rTorrent/libtorrent/PHP/nginx/s6 runtime configuration, rootfs scripts, image builds, or deciding whether a ruTorrent behavior change belongs in the ruTorrent fork instead.
---

# docker-rutorrent

## Boundary

This repository owns the container image, not active ruTorrent application behavior.

Keep here:

- Dockerfile dependency pins and build stages
- rTorrent/libtorrent/PHP/nginx/s6 runtime configuration
- `rootfs/` startup scripts and `/config` migration behavior
- build-time fetching of third-party plugins such as `geoip2` and `ratiocolor`
- image-level smoke tests

Put ruTorrent behavior changes in `/home/dev/Documents/my_projects/ruTorrent`:

- ruTorrent PHP, JavaScript, CSS, and UI behavior
- bundled plugin fixes
- `rutracker_check`
- `httprpc`, `xmlrpc.php`, and `Snoopy.class.inc` compatibility

## Removed Overlay

The old `overrides/rutorrent/` overlay has been removed. Do not recreate it for behavior fixes. If old notes or errors refer to that path, recover old files from git history only for comparison and port the minimal still-needed change into the ruTorrent fork.

## Verification

For image/runtime changes:

```sh
docker build --tag ivanshift/rutorrent:latest .
docker run --name rutorrent -d -p 8080:8080 -p 45000:45000 \
  -v rutorrent_config:/config -v rutorrent_data:/data \
  ivanshift/rutorrent:latest
docker exec rutorrent php85 -r 'echo "OK\n";'
```

For fork PHP lint when host PHP is missing:

```sh
docker run --rm --entrypoint php85 \
  -v /home/dev/Documents/my_projects/ruTorrent:/src \
  -w /src ivanshift/rutorrent:latest \
  -l plugins/rutracker_check/check.php
```

## Commit Scope

Stage exact paths. This repository often contains untracked diagnostic scripts and task files; do not include them unless the user explicitly asks.
