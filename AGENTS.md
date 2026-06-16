# Agent Context — docker-rutorrent

## Project Overview

Docker image for ruTorrent + rTorrent with deterministic builds, Alpine Linux base, and container filesystem/runtime configuration.

The active ruTorrent application code lives in the fork at:

`/home/dev/Documents/my_projects/ruTorrent`

Do not implement ruTorrent PHP/JS/CSS/plugin behavior changes in this repository. Make those changes in the ruTorrent fork, then build this Docker image against the fork through `RUTORRENT_REPO` / `RUTORRENT_REF`.

The local Codex skill for this repository is `.codex/skills/docker-rutorrent/SKILL.md`. Use it together with this file when deciding whether a change belongs in the Docker image or in the ruTorrent fork.

## Key Directories

| Path | Purpose |
|------|---------|
| `/home/dev/Documents/my_projects/ruTorrent` | Active IvanShift ruTorrent fork. Put ruTorrent PHP/JS/CSS/plugin changes here. |
| `rootfs/` | Container filesystem overlay (s6 services, nginx, php-fpm configs) |
| `tools/` | Standalone CLI utilities (not used by plugins at runtime) |
| `overrides/rutorrent/` | Removed legacy overlay. Do not recreate it for ruTorrent behavior fixes; use the ruTorrent fork instead. |
| `.tmp/upstream/` | Extracted upstream ruTorrent source, if present, for reference/diffing only |

## Architecture Notes

### Repository Boundary

`docker-rutorrent` owns:

- Dockerfile dependency pins and build stages
- rTorrent/libtorrent/PHP/nginx/s6 runtime configuration
- `rootfs/` startup scripts, `/config` migration, and container health/runtime behavior
- build-time fetching of third-party ruTorrent plugins such as `geoip2` and `ratiocolor`

`/home/dev/Documents/my_projects/ruTorrent` owns:

- ruTorrent core PHP/JS/CSS changes
- bundled ruTorrent plugins and plugin fixes
- `rutracker_check`, including tracker-specific behavior for RuTracker and NNMClub
- compatibility fixes in `webui.js`, `httprpc`, `xmlrpc.php`, `Snoopy.class.inc`, or other ruTorrent-owned files

If a bug report or old task points at `overrides/rutorrent/...`, treat it as historical context from the old overlay model. Recover the old file from git history only for comparison, then port the minimal still-needed change into the ruTorrent fork instead of recreating the overlay.

### rutracker_check Plugin

The active `rutracker_check` plugin lives in `/home/dev/Documents/my_projects/ruTorrent/plugins/rutracker_check/`. It checks if torrents have been updated on their source trackers. Key components:

- **`check.php`**: Main checker infrastructure (`ruTrackerChecker` class). Defines `run_ex()` which iterates registered trackers and calls their handlers.
- **`trackers/*.php`**: Per-tracker implementations. Each file registers itself via `registerTracker($commentPattern, $announcePattern, $handler)`.
- **`createTorrent($data, $hash)`**: Core method that replaces a torrent in rtorrent when an update is detected. Handles stop/erase/reload cycle.

### NNMClub Tracker (`trackers/nnmclub.php`)

Custom tracker implementation in the ruTorrent fork that bypasses Cloudflare Turnstile CAPTCHA:

1. **Scrape** the BitTorrent tracker directly (not behind Cloudflare) for fast checks
2. **Download guest .torrent** from `download.php` (not behind Cloudflare) for update detection
3. **Patch passkey** in guest torrents (dummy → real) using donor passkeys from session

Key discoveries:
- NNMClub passkeys are **per-user**, not per-torrent (interchangeable)
- `download.php` works without authentication
- Tracker `bt02.nnm-club.cc:2710` accepts direct scrape requests

### Snoopy HTTP Client

The plugins use ruTorrent's `Snoopy` class (`php/Snoopy.class.inc`) for HTTP requests. It supports cookies, redirects, and is integrated with `loginmgr` for automatic authentication. Change it in the ruTorrent fork when behavior needs to change.

### rTorrent XMLRPC

Communication with rTorrent is done via `rXMLRPCRequest` / `rXMLRPCCommand` classes defined in ruTorrent's `php/xmlrpc.php`. The Docker image uses rTorrent's tinyxml2 XML-RPC backend, but ruTorrent XMLRPC compatibility code belongs in the fork.

### Torrent Class

`php/Torrent.php` — bencode parser/serializer. Key methods:
- `hash_info()` — SHA1 of the info dictionary
- `announce()` / `announce_list()` — getter/setter for tracker URLs
- `save($path)` — serialize and write to disk

## Build & Test

### Dependency Updates

- For Dockerfile component bumps, verify current upstream versions before editing: Alpine releases, GitHub release tags/commit pins, FileBot downloads, RARLab source URLs, and Alpine `apk policy` for runtime/build packages.
- Keep `README.md` build-argument defaults and version summary in sync with Dockerfile pins. Update this file and `.codex/skills/docker-rutorrent/SKILL.md` only when their facts or workflow guidance changed.
- After version changes, run `git diff --check`, build the image, capture key runtime versions, and smoke-test a fresh container health/HTTP path when feasible.

```sh
# Build image
docker build --tag ivanshift/rutorrent:latest .

# Run container
docker run --name rutorrent -d -p 8080:8080 -p 45000:45000 \
  -v rutorrent_config:/config -v rutorrent_data:/data \
  ivanshift/rutorrent:latest

# Test PHP inside container
docker exec rutorrent php85 -r 'echo "OK\n";'

# Check rutracker_check plugin
docker exec rutorrent ls /rutorrent/app/plugins/rutracker_check/trackers/
```

## Change Workflow

1. For ruTorrent behavior bugs, edit `/home/dev/Documents/my_projects/ruTorrent` first.
2. Run the smallest matching ruTorrent checks there, for example `npm test -- --runInBand <spec>` under `tests/`, `node --check <file>`, and `php -l <file>` when PHP is available.
3. Build or smoke-test `docker-rutorrent` only after the fork contains the intended ruTorrent change.
4. Do not recreate `overrides/rutorrent/` unless the Dockerfile is intentionally reintroducing an overlay layer and the README/AGENTS contract is updated in the same change.
