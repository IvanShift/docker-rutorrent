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

## Dependency Updates

- For Dockerfile component version changes, verify current upstream versions before editing: Alpine releases, GitHub release tags/commit pins, FileBot downloads, RARLab source URLs, and Alpine `apk policy` for runtime/build packages.
- Prefer packages from the stable Alpine branch when they provide the required version and features. In particular, Alpine curl already uses c-ares; keep it packaged rather than rebuilding c-ares manually while `curl -V` reports both `AsynchDNS` and `c-ares/...`.
- For executables and libraries copied from build stages, inspect ELF `NEEDED` entries and declare their direct runtime ABI packages explicitly. `apk` cannot infer dependencies for files copied into the final image.
- Audit conditional branches separately. If the FileBot branch is affected, build and smoke-test both the default image and `FILEBOT=true`; GNU `findutils` belongs only to FileBot because its uninstall script uses `find -xtype`.
- Keep README build-argument defaults and version summaries in sync with Dockerfile pins. Update AGENTS.md or this skill only for durable workflow/fact changes, not for every package patch unless the file names that version.
- Verify with `git diff --check`, `docker build`, runtime version checks, and a fresh-container health/HTTP smoke test when feasible.

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
