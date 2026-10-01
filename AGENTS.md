# Agent Context — docker-rutorrent

## Project Overview

Docker image for ruTorrent + rTorrent with controlled source builds (exact refs and checksums), Alpine Linux base, and container filesystem/runtime configuration. Builds are not byte-reproducible: packages come from the floating Alpine branch (`apk upgrade`) and CI consumes the exact source inputs configured in `Dockerfile`.

The active ruTorrent application code lives in the fork at:

`/home/dev/Documents/my_projects/ruTorrent`

Do not implement ruTorrent PHP/JS/CSS/plugin behavior changes in this repository. Make those changes in the ruTorrent fork, then build this Docker image against the fork through `RUTORRENT_REPO` / `RUTORRENT_REF`.

The local Codex skill for this repository is `.codex/skills/docker-rutorrent/SKILL.md`. Use it together with this file when deciding whether a change belongs in the Docker image or in the ruTorrent fork.

## Key Directories

| Path | Purpose |
|------|---------|
| `/home/dev/Documents/my_projects/ruTorrent` | Active IvanShift ruTorrent fork. Put ruTorrent PHP/JS/CSS/plugin changes here. |
| `/home/dev/Documents/my_projects/rtorrent` | Local clone of the IvanShift rTorrent fork (`origin` = `https://github.com/IvanShift/rtorrent`, `upstream` = `rakshasa/rtorrent`). Native daemon patches live on its `rutorrent-native` branch; `master` mirrors upstream. |
| `rootfs/` | Container filesystem overlay (s6 services, nginx, php-fpm configs) |
| `overrides/rutorrent/` | Removed legacy overlay. Do not recreate it for ruTorrent behavior fixes; use the ruTorrent fork instead. |
| `.tmp/upstream/` | Extracted upstream ruTorrent source, if present, for reference/diffing only |
| `tests/` | CI test entry points and smoke scenarios (`tests/ci`, `tests/shell`, `tests/update-config`, `tests/smoke`) |
| `scripts/` | CI helpers, such as the ruTorrent check gate `verify_rutorrent_checks.py` |
| `packaging/` | APKBUILDs for the MediaInfo backport built in the `mediainfo-builder` stage |
| `certs/` | Validated Let's Encrypt certificate used only when Alpine needs the compatibility chain |

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
- **`createTorrent($torrent, $hash, $oldTorrent = null)`**: Core method that replaces a torrent in rtorrent when an update is detected; it takes a parsed `Torrent` object. Handles the stop/erase/reload cycle.

These notes summarize the fork for orientation and can lag behind the pinned commit. The fork's own `AGENTS.md` and its code at `RUTORRENT_REF` are authoritative.

### NNMClub Tracker (`trackers/nnmclub.php`)

Custom tracker implementation in the ruTorrent fork that bypasses Cloudflare Turnstile CAPTCHA:

1. **Scrape** the BitTorrent tracker directly (not behind Cloudflare) for fast checks
2. **Download guest .torrent** from `download.php` (not behind Cloudflare) for update detection
3. **Patch passkey** in guest torrents: the dummy credential is replaced with the passkey taken from the current torrent's own announce metadata. Passkeys are never harvested from other torrents; without one the replacement is refused.

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
- Prefer Alpine packages over custom source builds when the stable Alpine branch provides the required version and features. Verify feature parity at runtime before removing a source build; for curl/c-ares, `curl -V` must report both `AsynchDNS` and a `c-ares/` version.
- Treat binaries copied from build stages as invisible to `apk` dependency resolution. Inspect their ELF `NEEDED` entries and keep direct runtime ABI packages explicit instead of relying on incidental transitive dependencies.
- When dependency changes affect the conditional FileBot branch, build and smoke-test both the default image and `FILEBOT=true`. Keep GNU `findutils` FileBot-only: this image's own `postrm` hook (`rootfs/usr/local/bin/postrm`), which startup installs only when FileBot is present, uses GNU `find -xtype`; BusyBox `find` rejects it.
- RARLab's `rar_add.htm` links the newest UnRAR source, which can be a beta: on 2026-10-01 `unrarsrc-7.3.1` was RAR 7.30 beta 1. Check `RARVER_BETA` in `unrar/version.hpp` and pin the newest stable release.
- The GeoLite2 database release (`GEOIP2_DB_VERSION`) expires: `P3TERX/GeoLite.mmdb` publishes about every three days and keeps only the two newest releases, so an uncached build of an older pin fails with curl exit code 22. Re-pin to the newest release, with the SHA256 checked by download, in the same change that is pushed. A self-hosted copy must follow the GeoLite2 EULA: replace the database within 30 days of a newer release.
- Give build-time plugins and data files independent, tested refs or checksums. Verify required plugin entry files and exercise data readers during the build; do not silently fall back to a mutable default branch or an unverified remote asset.
- The default rTorrent source is the IvanShift fork, pinned by `RTORRENT_REPO`, `RTORRENT_REF` and `RTORRENT_VERSION`. To ship a new native commit, make it on `rutorrent-native` in `/home/dev/Documents/my_projects/rtorrent`, let the owner push it, then set `RTORRENT_VERSION` to its full SHA; an empty `RTORRENT_REF` selects that SHA, while an explicit ref must resolve to it. Keep shipped commits reachable under immutable owner-maintained tags and do not rewrite their history: the build stops when the ref resolves to another commit. `RTORRENT_BRANCH` selects the build path and the CI tag label, not the source: `v0.16.24` is the native build, `v0.9.8` the legacy build (see Runtime Model). Keep `v0.16.24` while the fork is based on upstream v0.16.24; change it together with the fork's base version (`AC_INIT` in `configure.ac`) and the version checks in `tests/smoke/default.sh`.
- Keep `README.md` build-argument defaults and version summary in sync with Dockerfile pins. Update this file and `.codex/skills/docker-rutorrent/SKILL.md` only when their facts or workflow guidance changed.
- To see what a rebuild would change in unpinned packages, compare `apk info -v` from a built image with the `APKINDEX` of `v3.24/main` and `v3.24/community`.
- After version changes, run `git diff --check`, build the image, capture key runtime versions, and run the tests below.

Use a local tag, container name and volumes of your own. `ivanshift/rutorrent:latest` is the published tag, and `rutorrent`, `rutorrent_config` and `rutorrent_data` are the names of the production example in the README.

```sh
# Build a local image
docker build --tag rutorrent-local:dev .

# What CI runs (.github/workflows/build.yml)
python3 -m unittest discover -s tests/ci
tests/shell/run.sh            # fixture extraction uses tracked modes and tar.umask=0022
docker run --rm --entrypoint /bin/sh \
  --mount "type=bind,src=${PWD}/tests/update-config,dst=/tests/update-config,readonly" \
  rutorrent-local:dev /tests/update-config/run.sh
tests/update-config/single-file-mounts.sh rutorrent-local:dev
tests/smoke/default.sh rutorrent-local:dev   # also runs identity, conf-persistence, mediainfo and the bootstrap scenarios
tests/smoke/pidfd.sh rutorrent-local:dev linux/amd64
tests/smoke/erasedata-rename.sh rutorrent-local:dev linux/amd64
tests/smoke/filebot.sh <image built with FILEBOT=true>

# Legacy build (RTORRENT_BRANCH=v0.9.8); CI has a required real-daemon job
tests/smoke/legacy-098.sh <legacy image>
docker run --rm --entrypoint /bin/sh \
  --mount "type=bind,src=${PWD}/tests/update-config,dst=/tests/update-config,readonly" \
  <legacy image> /tests/update-config/legacy-098.sh

# Manual look at a fresh container
docker run --name rutorrent-dev -d \
  -v rutorrent-dev-config:/config -v rutorrent-dev-data:/data rutorrent-local:dev
docker exec rutorrent-dev curl -fsS http://127.0.0.1:8080/healthz   # 200 from loopback only; other addresses get 403
docker exec rutorrent-dev php85 -r 'echo "OK\n";'
docker rm -f -v rutorrent-dev && docker volume rm rutorrent-dev-config rutorrent-dev-data
```

With the default `HTTP_AUTH=true`, `GET /` answers 401 until `gen-http-passwd` creates the first user.

### Custom `UID` and `GID`

`torrent:991` is created at build time. Startup remaps this account and group with
`usermod`/`groupmod` when the requested numeric IDs are free, or reuses an existing
group such as `users:100`. If a requested UID belongs to another account, startup
keeps that account and logs a warning. The real-container identity cases in
`tests/smoke/identity.sh` cover default IDs, 1000:1000, 1000:100, and 991:100.

## Runtime Model

- Two build modes. The native build (`RTORRENT_BRANCH=v0.16.24`) uses the rTorrent fork with the tinyxml2 XML-RPC backend. The legacy build (`RTORRENT_BRANCH=v0.9.8`, only with matching legacy sources) uses C++17 and xmlrpc-c and writes the marker `/etc/rtorrent-legacy`, which `update-config` and the legacy tests read.
- `HTTP_AUTH` defaults to `true`. Startup stops with exit code 64 on `HTTP_AUTH=false` without `ALLOW_UNAUTHENTICATED_HTTP=true` and on values of `HTTP_AUTH` or `ENABLE_RPC2` other than exactly `true` or `false`.
- Before ordinary ownership changes, startup checks trusted real directories and private journal records with exit code 78. It reads the effective `session.path.set`, checks its ancestors, and protects stop-intent/retrackers/autotools journals there. The erasedata queue `/config/rutorrent/erasedata-jobs` has an empty, single-linked root:root 0400 witness `/config/rutorrent/.erasedata-first-use`. Root-owned application/configuration defaults and private journals are excluded from recursive ownership changes. `tests/smoke/stop-intent-bootstrap.sh` and `tests/smoke/erasedata-queue-bootstrap.sh` pin these refusals; operator repair and `attest-erasedata-queue` usage are in README “Upgrading and troubleshooting”.
- `/rutorrent/conf-dist` is the pristine copy of the fork's `conf/`. Startup seeds files missing from `/config/rutorrent/conf` from it and keeps files the operator changed.
- `update-config` migrates `/config/rtorrent/.rtorrent.rc` and `conf/config.php` on every start (`tests/update-config/run.sh`). Token-based PHP transformation preserves comments/literals and operator executables; the final image policy forces a single shared profile and CSRF checks. Writable single-file mounts have a checked in-place fallback; ordinary directory mounts use atomic replacement. The suite exercises native and legacy transforms in every image by temporarily changing the runtime marker and restoring it.
- Daemons run under the chosen service identity; root retains supervisor/configuration ownership. `/healthz` checks PHP on loopback, and plugin readiness uses a private Unix socket. Run nginx diagnostics with the daemon identity (`docker exec -u <UID>:<GID> <container> nginx -t`) so diagnostics do not recreate logs with root ownership.

## Network and DNS

Measured 2026-09-14: public resolvers are only intermittently reachable from the owner's
network, and which one answers changes by the hour (1.1.1.1 dead and 8.8.8.8 alive at noon;
the reverse an hour later). A container whose resolvers all belong to one provider -- for
example Docker's 8.8.8.8/8.8.4.4 default when the host runs a local stub -- loses all name
resolution when that provider is out and fails every fetch with curl 6. That is a
network condition, not an image defect, and the owner chose not to change DNS configuration;
just do not assume those resolvers answer.

## Change Workflow

1. For ruTorrent behavior bugs, edit `/home/dev/Documents/my_projects/ruTorrent` first.
2. Run the smallest matching ruTorrent checks there, for example `npm test -- --runInBand <spec>` under `tests/`, `node --check <file>`, and `php -l <file>` when PHP is available.
3. Build or smoke-test `docker-rutorrent` only after the fork contains the intended ruTorrent change.
4. Do not recreate `overrides/rutorrent/` unless the Dockerfile is intentionally reintroducing an overlay layer and the README/AGENTS contract is updated in the same change.

### Commit Scope

Stage exact paths. This repository often contains untracked diagnostic scripts and task files (`tasks/`); do not include them unless the owner explicitly asks.

### Clean Up Task Resources

- Record resources when creating them in a task-local ledger: owning agent, exact path/name,
  Docker image/container/volume IDs, temporary branch/worktree names and expected ownership.
  Use a unique task and agent prefix; a matching prefix alone does not prove ownership.
- Remove each agent's idle scratch promptly after copying unique logs and verification evidence
  into the ignored task report directory, with checksums and the tested source/image identity.
  Stop and reap that agent's fixture processes before deleting their files.
- Delete only resources this task created. Preserve pre-existing or user-created branches,
  worktrees, images, caches and data, including a tag the user has replaced since its creation.
  Recheck identity and active references before removal; do not use broad Git or Docker prune.
- Use `--rm` for disposable containers and `docker rm -v` for retained test containers, then
  remove only their recorded named volumes. Record anonymous volume IDs from container mounts
  before removal. Keep shared builders and build caches unless explicitly authorized otherwise.
- Avoid root-owned bind-mount outputs. If a privileged tool creates them, record their exact paths
  and owners immediately and arrange scoped cleanup; do not leave them for an unprivileged agent.
- Coordinate the final cleanup after all resource-producing agents finish. Never count or prune
  Docker volumes while other agents run. Reconcile the ledger with live state and report removed,
  deliberately retained and blocked resources separately from test results.

## Run Independent Work In Parallel

The owner's standing rule, the same as in the ruTorrent fork's `AGENTS.md`: finish work sooner by
running every independent part of it at the same time, with as many agents as the session allows.
Work that waits behind a small agent pool, or behind one long-running agent, is the default to avoid.

- **Split small, one agent each.** One agent per file, or per part of a large file, for review; one
  agent per finding or pair of findings for verification; one agent per section for a report.
  On 2026-09-30 batches of five findings per verifier made each verifier run 15-25 minutes.
- **Pipeline, not barrier.** Start a unit's next stage as soon as its own previous stage is done.
  Wait for everything only when a step needs all results: deduplication, the final document.
- **Do not wait on one slow agent.** Start the next stage for the units that are done, and split the
  slow unit's work across several agents instead of waiting for it.
- **Sequential only where there is a real dependency:** git history operations, edits of the same
  file, a step that consumes a previous step's result.
- **Isolation is what makes it safe.** Every agent gets its own scratch directory and its own
  container and volume name prefix, runs containers with `--rm`, and removes what it created with
  `docker rm -v`: the image declares `VOLUME /data /config`, so a plain `docker rm` leaves two
  anonymous volumes behind. Never count or prune Docker volumes while other agents are running.
- The workflow concurrency cap comes from `CLAUDE_CODE_WORKFLOW_MAX_CONCURRENT_AGENTS` in
  `~/.claude/settings.json` (50 on 2026-09-30). Use the actual session limit when dispatching.
- Before a large wave, say in one line what it is for, how many agents it takes and roughly how long.
- **Check before re-running.** Agents fail when the account's session usage limit is reached
  ("You've hit your session limit") or when the machine sleeps; an agent marked failed may
  already have written its result file. Re-run only the units whose results are missing.
