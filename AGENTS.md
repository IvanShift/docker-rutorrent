# Agent Context — docker-rutorrent

## Project Overview

Docker image for ruTorrent + rTorrent with controlled source builds (exact refs/checksums and base/frontend digests), Alpine Linux base, and container filesystem/runtime configuration. Builds are not byte-reproducible: packages come from the floating Alpine branch (`apk upgrade`) and CI consumes the exact source inputs configured in `Dockerfile`.

The active ruTorrent application code lives in the fork at:

`/home/dev/Documents/my_projects/ruTorrent`

Do not implement ruTorrent PHP/JS/CSS/plugin behavior changes in this repository. Make those changes in the ruTorrent fork, then build this Docker image against the fork through `RUTORRENT_REPO` / `RUTORRENT_REF`.

The local Codex skill for this repository is `.codex/skills/docker-rutorrent/SKILL.md`. Use it together with this file when deciding whether a change belongs in the Docker image or in the ruTorrent fork.

## Key Directories

| Path | Purpose |
|------|---------|
| `/home/dev/Documents/my_projects/ruTorrent` | Active IvanShift ruTorrent fork. Put ruTorrent PHP/JS/CSS/plugin changes here. |
| `/home/dev/Documents/my_projects/rtorrent` | Local clone of the IvanShift rTorrent fork (`origin` = `https://github.com/IvanShift/rtorrent`, `upstream` = `rakshasa/rtorrent`). Native daemon patches live on its `rutorrent-native` branch; `master` mirrors upstream. |
| `/home/dev/Documents/my_projects/libtorrent` | Local clone of the IvanShift libtorrent fork (`origin` = `https://github.com/IvanShift/libtorrent`, created 2026-10-18; `upstream` = `rakshasa/libtorrent`). Upstream libtorrent PR branches (`up/l<N>-*`) live there; `master` mirrors upstream. The image still builds upstream libtorrent pinned by `LIBTORRENT_VERSION`, not this fork. |
| `rootfs/` | Container filesystem overlay (s6 services, nginx, php-fpm configs) |
| `overrides/rutorrent/` | Removed legacy overlay. Do not recreate it for ruTorrent behavior fixes; use the ruTorrent fork instead. |
| `.tmp/upstream/` | Extracted upstream ruTorrent source, if present, for reference/diffing only |
| `tests/` | CI test entry points and smoke scenarios (`tests/ci`, `tests/shell`, `tests/update-config`, `tests/smoke`) |
| `scripts/` | CI helpers, such as the ruTorrent check gate `verify_rutorrent_checks.py` |
| `packaging/` | APKBUILDs for local packages (MediaInfo, screenshots FFmpeg, UnZip, libxml2, FileBot-only chromaprint and OpenJDK 21), each built in its `*-builder` stage on the shared `apk-builder` signing key. libsndfile uses the stable Alpine package. |
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
- When dependency changes affect the conditional FileBot branch, build and smoke-test both the default image and `FILEBOT=true`. Keep GNU `findutils` FileBot-only: the FileBot cleanup job (`rootfs/usr/local/libexec/filebot-cleanup`, run only by the FileBot images' `filebot-queue` service) uses GNU `find -xtype`; BusyBox `find` rejects it.
- Local security backports replace an Alpine v3.24 package only while v3.24 lacks a published fix; drop each once v3.24 ships the same or a newer version. Take nginx only from the stable (even minor) line. Verify the upstream release signature, update the `*_SHA512` pin, and install every split package from the local files (FFmpeg: `ffmpeg` plus its seven `ffmpeg-lib*` packages) so apk cannot mix in repository versions. FFmpeg 9 uses an application-specific CPU screenshots profile: keep dav1d, libjxl, JPEG/PNG output, scaling/seeking and the virtual lavfi/aresample paths used by media checks. The removed libvpx, external video encoders, hardware/text/network/device stacks are intentional capability reductions documented in README. Rebuild both FileBot chromaprint packages against the matching FFmpeg ABI. Keep libxml2 on the stock PHP ABI unless its consumers are migrated separately.
- The build context is an allowlist (`.dockerignore`): a new `COPY` or bind-mount source needs an entry there, and `tests/ci/test_supplychain_contract.py` requires both lists to match. The image keeps only the fork test files in `scripts/rutorrent-image-tests.list`, which image smoke runs; `tests/ci/test_rutorrent_image_tests.py` requires that list to equal the app test paths `tests/smoke/` references.
- RARLab's `rar_add.htm` links the newest UnRAR source, which can be a beta: on 2026-10-01 `unrarsrc-7.3.1` was RAR 7.30 beta 1. Check `RARVER_BETA` in `unrar/version.hpp` and pin the newest stable release.
- The GeoLite2 database release (`GEOIP2_DB_VERSION`) expires: `P3TERX/GeoLite.mmdb` publishes about every three days and keeps only the two newest releases, so an uncached build of an older pin fails with curl exit code 22. Re-pin to the newest release, with the SHA256 checked by download, in the same change that is pushed. A self-hosted copy must follow the GeoLite2 EULA: replace the database within 30 days of a newer release.
- Give build-time plugins and data files independent, tested refs or checksums. Verify required plugin entry files and exercise data readers during the build; do not silently fall back to a mutable default branch or an unverified remote asset.
- The default rTorrent source is the IvanShift fork, pinned by `RTORRENT_REPO`, `RTORRENT_REF` and `RTORRENT_VERSION`. To ship a new native commit, make it on `rutorrent-native` in `/home/dev/Documents/my_projects/rtorrent`, let the owner push it, then set `RTORRENT_VERSION` to its full SHA; an empty `RTORRENT_REF` selects that SHA, while an explicit ref must resolve to it. Keep shipped commits reachable under immutable owner-maintained tags and do not rewrite their history: the build stops when the ref resolves to another commit. `RTORRENT_BRANCH` selects the build path and the CI tag label, not the source: `v0.16.x` selects the C++20 native build and `v0.9.8` the legacy build. Match the label to `AC_INIT`, and update `LIBTORRENT_BRANCH`/`LIBTORRENT_VERSION` together with the native dependency requirement. A 0.16.x release requires a published ruTorrent ref that accepts that version and its helper/metadata ABI before Docker pins can move. Smoke derives versions from the build inputs; local exact-byte overlays set explicit expected app/native revisions and retain separate byte manifests. Re-pin an expired GeoLite default in the same reviewed release.
- Keep `README.md` build-argument defaults and version summary in sync with Dockerfile pins. Update this file and `.codex/skills/docker-rutorrent/SKILL.md` only when their facts or workflow guidance changed.
- To see what a rebuild would change in unpinned packages, compare `apk info -v` from a built image with the `APKINDEX` of `v3.24/main` and `v3.24/community`.
- After version changes, run `git diff --check`, build the image, capture key runtime versions, and run the tests below.

Use a local tag, container name and volumes of your own. `ivanshift/rutorrent:latest` is the published tag, and `rutorrent`, `rutorrent_config` and `rutorrent_data` are the names of the production example in the README.

```sh
# Build a local image
docker build --tag rutorrent-local:dev .

# What CI runs (.github/workflows/build.yml)
# tests/ci needs host php with the SimpleXML (Debian/Ubuntu php-xml), tokenizer
# and posix extensions: under CI=true or GITHUB_ACTIONS=true a missing php or
# extension fails the PHP-backed tests instead of skipping them (host_php.py).
python3 -m unittest discover -s tests/ci -p 'test_*.py'
tests/shell/run.sh            # syntax, executable-bit, service and logrotate fixtures
docker run --rm --pull=never --entrypoint /bin/sh \
  --mount "type=bind,src=${PWD}/tests/update-config,dst=/tests/update-config,readonly" \
  rutorrent-local:dev /tests/update-config/run.sh
tests/update-config/single-file-mounts.sh rutorrent-local:dev
tests/update-config/signals.sh rutorrent-local:dev
tests/smoke/default.sh rutorrent-local:dev   # also runs identity, conf-persistence, mediainfo and the bootstrap scenarios
tests/smoke/startup-regressions.sh rutorrent-local:dev
tests/smoke/stop-untied.sh rutorrent-local:dev
tests/smoke/log-rotation.sh rutorrent-local:dev
tests/smoke/erasedata-fpm-rename.sh rutorrent-local:dev
tests/smoke/retrackers-metadata.sh rutorrent-local:dev native
tests/smoke/upgrade-permissions.sh rutorrent-local:dev   # executes the README offline repair commands
tests/smoke/runtime-contracts.sh rutorrent-local:dev     # named GID, access log, both FPM pools' argv override, upload limit
python3 tests/smoke/http-runtime.py rutorrent-local:dev --scope watchdog  # frame headers, HTTP_AUTH=false watchdog
tests/smoke/filebot.sh <image built with FILEBOT=true>   # also runs tests/smoke/filebot-queue.sh
tests/smoke/pidfd.sh <FileBot image> linux/amd64
tests/smoke/erasedata-rename.sh <FileBot image> linux/amd64
# Local acceptance only: the default scope also drives the real WebUI (Chrome, Node).
python3 tests/smoke/http-runtime.py rutorrent-local:dev

# Legacy build (RTORRENT_BRANCH=v0.9.8); CI has a required real-daemon job
docker run --rm --pull=never --entrypoint /bin/sh \
  --mount "type=bind,src=${PWD}/tests/update-config,dst=/tests/update-config,readonly" \
  <legacy image> /tests/update-config/legacy-098.sh
tests/smoke/legacy-098.sh <legacy image>
tests/smoke/stop-untied.sh <legacy image>
tests/smoke/retrackers-metadata.sh <legacy image> legacy
tests/smoke/runtime-contracts.sh <legacy image>

# Manual look at a fresh container
docker run --pull=never --name rutorrent-dev -d --stop-timeout 60 \
  -v rutorrent-dev-config:/config -v rutorrent-dev-data:/data rutorrent-local:dev
attempt=0
until docker exec rutorrent-dev rutorrent-healthcheck; do
  attempt=$((attempt + 1))
  test "$attempt" -lt 120 || { docker logs rutorrent-dev; exit 1; }
  test "$(docker inspect -f '{{.State.Running}}' rutorrent-dev)" = true || { docker logs rutorrent-dev; exit 1; }
  sleep 1
done
# Health is loopback-only; other addresses receive 403.
docker exec rutorrent-dev php85 -r 'echo "OK\n";'
docker rm -f -v rutorrent-dev && docker volume rm rutorrent-dev-config rutorrent-dev-data
```

With the default `HTTP_AUTH=true`, `GET /` answers 401 until `gen-http-passwd` creates the first user.

CI concurrency: a pull request keeps one group per ref and cancels its older run; push, schedule and manual runs each get a workflow-level group per event, ref and commit. These groups use GitHub's default single pending slot, which cancels a pending run for the next arrival, so merging them again lets a re-run or a manual run displace a pending push to `master`. Only the publish job has a job-level group: the fixed `publish-rutorrent`, shared by every commit and event, with `queue: max` (up to 100 pending jobs in the order they started waiting; a full queue cancels later arrivals) and `cancel-in-progress: false` (GitHub rejects `queue: max` together with `cancel-in-progress: true`). Publishers therefore run one at a time, and inside that lock `scripts/verify_publish_revision.py` refuses a commit that is no longer `master` before the staging uploads and again before the release tags move. This keeps an older commit from moving the release tags back after a newer `master` has been published. It does not make the `master` read and the registry writes atomic: a publication whose commit stops being `master` after its last check still completes, and the tags reach the new `master` only when that commit's own run publishes. Every job runs on `ubuntu-24.04` (`ubuntu-24.04-arm` for arm64), not a moving `-latest` label. `tests/ci/test_workflow_contract.py` parses the workflow files (`tests/ci/workflow_yaml.py`, a strict subset parser), lexes their run scripts (`tests/ci/shell_scan.py`) and pins the group semantics as GitHub compares them (without case), the single publish group, the runner of every job in every workflow file, each gate as a whole unconditional top-level command with no failure-masking construct, and the revision check before every registry write; it replays interleaved publishers under GitHub's documented queue rules and also runs `tests/shell/run.sh` over stub checks. `tests/ci/test_image_labels.py` freezes the image labels; the arm64 index entry carries no platform variant, which Docker resolves as `v8`.

Smoke containers: shell harnesses start them only through `smoke_run`/`smoke_create` in `tests/smoke/lib.sh` and Python harnesses only through `smoke_harness.run_argv`/`start_container`, which put this run's `io.ivanshift.smoke.owner` label in the same argv; containers are removed only through the owner-checked helpers. A detached start in a shell harness needs a `cleanup()` that calls one of the `lib.sh` removal helpers and is installed for `EXIT`. `tests/ci/test_smoke_resource_scheme.py` enforces this on the lexed and parsed harnesses; its only exception is one exact foreground `--rm` statement in `tests/smoke/startup-regressions.sh`.

### Custom `UID` and `GID`

`torrent:991` is created at build time. Startup remaps this account and group with
`usermod`/`groupmod` when the requested numeric IDs are free, or reuses an existing
group such as `users:100`. If a requested UID belongs to another account, startup
keeps that account and logs a warning. The real-container identity cases in
`tests/smoke/identity.sh` cover default IDs, 1000:1000, 1000:100, and 991:100.

## Runtime Model

- Two build modes. The native build (`RTORRENT_BRANCH=v0.16.x`; published default `v0.16.27`) uses the rTorrent fork with the tinyxml2 XML-RPC backend. The legacy build (`RTORRENT_BRANCH=v0.9.8`, only with matching legacy sources) uses C++17 and xmlrpc-c and writes the marker `/etc/rtorrent-legacy`, which `update-config` and the legacy tests read.
- `HTTP_AUTH` defaults to `true`. Startup stops with exit code 64 on `HTTP_AUTH=false` without `ALLOW_UNAUTHENTICATED_HTTP=true` and on values of `HTTP_AUTH` or `ENABLE_RPC2` other than exactly `true` or `false`.
- Before ordinary ownership changes, startup checks trusted real directories and private journal records with exit code 78. It reads the effective `session.path.set`, checks its ancestors, and protects stop-intent/retrackers/autotools journals there. The erasedata queue `/config/rutorrent/erasedata-jobs` has an empty, single-linked root:root 0400 witness `/config/rutorrent/.erasedata-first-use`. Root-owned application/configuration defaults and private journals are excluded from recursive ownership changes. `tests/smoke/stop-intent-bootstrap.sh` and `tests/smoke/erasedata-queue-bootstrap.sh` pin these refusals; operator repair and `attest-erasedata-queue` usage are in README “Upgrading and troubleshooting”. The legacy marker skips the native session/journal admission block; it does not skip configuration or erasedata queue checks.
- `/rutorrent/conf-dist` is the pristine copy of the fork's `conf/`. Startup seeds files missing from `/config/rutorrent/conf` from it and keeps files the operator changed.
- `update-config` migrates `/config/rtorrent/.rtorrent.rc` and `conf/config.php` on every start (`tests/update-config/run.sh`), after startup attests the rc (`rc-check`) and before it reads the session and download paths. The rc migrator reads logical lines as rTorrent does (an odd run of trailing backslashes joins the next line, comments included; logical lines of 4095 bytes or more are refused), converts CRLF to LF because rTorrent rejects a trailing CR, and in FileBot images appends the `filebot`/`filebot_cleaner` hooks unless an executed `method.set_key` sets or erases that event and key as rTorrent parses it (a computed or refused one also keeps the hook out, with a notice); `log.add_output` lines are deduplicated only across a finite list of daemon commands. The PHP migrator regenerates a `managed defaults` block of `??=` lines for `$topDirectory`, `$scgi_host`, `$scgi_port` and the six managed externals after `config.local.php` and before the final policy block; only proven literals (a literal array, `$pathToExternals['key'] =`/`??=`, plain global scalar assignments) are edited in place. Token-based PHP transformation preserves comments/literals and operator executables; the final image policy defaults to a shared profile (explicit `RU_FORBID_USER_SETTINGS=false` permits per-user profiles) and enforces CSRF checks. Writable single-file mounts have a checked in-place fallback; ordinary directory mounts use atomic replacement. The suite exercises native and legacy transforms in every image by temporarily changing the runtime marker and restoring it.
- Daemons run under the chosen service identity; root retains supervisor/configuration ownership. `/healthz` checks PHP on loopback through its separate FPM health pool, and plugin readiness uses a private Unix socket. Run nginx diagnostics with the daemon identity (`docker exec -u <UID>:<GID> <container> nginx -t`) so diagnostics do not recreate logs with root ownership.

### Reviewed Native Actors and Image Evidence

The daemon launcher fixes `RUTORRENT_NATIVE_PHP=/usr/bin/php85` and `RUTORRENT_NATIVE_APP_ROOT=/rutorrent/app` before dropping to the selected service UID. Worker RPC arguments carry job identity, never executable/script paths. Build normalizes only known immutable program ancestors and fixed actor files and refuses a partial native actor set that startup would reject; startup checks them read-only before root PHP or daemon execution and refuses unsafe operator binds. Executable admission must prove execute access for the selected UID, not merely any execute bit. The fd access check requires Linux 5.8+; failure must remain visible rather than use a pathname fallback.

`build-docker-helpers.sh` compiles ownership-walk, service-runtime and native-actor-paths in the helper builder; runtime has no compiler and keeps no C sources. Descriptor ownership traversal uses pinned parents and entries; at most 16 levels stay pinned, released levels are re-pinned through `..` or reopened by name and must match their admitted (dev, ino), and EMFILE/ENFILE refusals name the descriptor limit. The iterative walk snapshots the entry names of every directory on its current path, ancestors included, so very wide directories cost memory proportional to their name bytes; no deployment memory limit has been measured. Shared disappearances and transient stale entries have bounded skips/retries; restricted traversal temporarily selects exact filesystem UID/GID and one group, then restores root's identity/groups. Private auth/witness/session admission remains strict: root-squashed storage requires preseeded trusted writable metadata, not relaxed ownership.

Local full builds may substitute reviewed named-context exports for native/application fetching. Verify every source byte and mode, retain the narrow Dockerfile override and manifests, and preserve production compile/helper/runtime stages and published default pins. Final gates use the newly compiled packaged binary and application directly; intermediate overlays and O2 binaries remain scoped evidence. In the 2026-10-06 campaign run new daemons only on patched 0.16.25 and application 0.9.8; describe 0.9.8-specific defects without adaptation. Release publication, ARM64/remote CI, licensed FileBot and deployment remain distinct actions/evidence.

### Service and HTTP Contracts

Both cleared FPM pools explicitly pass `RU_PUBLIC_URL`. Empty environment permits config.local `$publicURL`; nonempty environment wins. Loopback health uses a dedicated two-worker pool, separate from fifteen normal workers. Up to 100 files per request remain subordinate to 100MiB total body size.

The PID1 service-runtime wrapper isolates s6 from terminal signals and translates interactive INT into orderly shutdown. rTorrent's down-signal is INT for stopped announces, with a 55s s6 deadline and the configured Docker `--stop-timeout 60` deadline used by the documented lifecycle gates. Requested clean primary-service restarts remain local and reinitialize schedules once; unexpected primary failure terminates the stack. Helper failures restart only their helper. Watchdog publishes protected classified status and resumes only positively identified stopped rTorrent through pidfd; an ambiguous SCGI timeout during valid synchronous work does not authorize termination. Logrotate signals masters through pidfd without numeric-PID fallback. A refused reopen stays pending and is retried each pass. Before moving a master-owned log, logrotate writes `<log>.reopen` in its private archive directory and removes it only after that master's verified reopen succeeds. Every admitted pass reads these markers, so a restart, a later archive-directory repair or a recreated active path cannot lose the reopen. The moved log and its archive stay untouched until the reopen succeeds, and a start without markers signals no master. Password publication syncs the prepared file and parent directory around descriptor-bound rename. Concurrent `gen-http-passwd` runs hold the root 0600 `/run/rutorrent-passwd.lock`, which `service-runtime lock-auth` admits, from reading the current file through publication, after the password prompt.

FileBot images run the `filebot-queue` helper. rTorrent's unchanged `postdl`/`postrm` hook lines only write requests into `/config/filebot/queue` (NUL-separated records, temp+rename; one coalesced cleanup flag, claimed as `.cleanup-claimed` until the cleanup ends). `filebot-queue.php` runs one job at a time as the service UID in its own session, bounded by `FILEBOT_JOB_TIMEOUT` (default 21600 s); a job lasts until its whole process group has ended. The membership scan checks threads of zombie process leaders and treats an unreadable or changing PID census as pending; only kernel absence or a stable zombie-only census permits completion. On a host-wide `/proc`, unrelated process churn can conservatively delay completion; final host unit checks should not compete with mass container creation. TERM/timeout signal the job's process group, KILL after 10 s; after a failed leader the rest of its group is stopped the same way, after a successful one it keeps the job's deadline. A job positively interrupted by orderly shutdown stays queued; an unsettled completion whose worker died has an unknown outcome even if its process group already ended before recovery, and is not replayed automatically. Failures and unknown completions go to `queue/failed/` (newest 50; `<name>.failed` in the queue when `failed/` refuses them) and are never retried automatically. A separate close-on-exec `queue/.worker-lock` serializes each worker through recovery and settlement; jobs do not inherit it. The queue job lock is inherited by the job as fd 3, and the private `queue/.running` records the job's group, leader and worker start times, boot ID and deadline: a restarted worker adopts a job that outlived its worker only after verifying its leader (PID and start time) or a lock-holding member of its group, never signals an unverified group, enforces the job's own deadline, and keeps a job that ended by itself in `failed/` as `outcome=unknown`. An unknown cleanup outcome returns the idempotent cleanup request without replacing a newer flag. A prior-boot completion is also unknown and cannot authorize signalling an old numeric identity. A worker failure restarts only that helper; without `/filebot/filebot.sh` the run script parks.

Changing container authentication/RPC environment requires recreation; rerunning startup with a different process environment is not an in-place toggle. FileBot work runs under the selected service UID, imports only attested license/cache sources and keeps diagnostics classified; a build without a license cannot establish licensed processing.

The watchdog publishes under root-owned private `/run/rutorrent-watchdog`, independently of optional HTTP-auth directories. Its flock descriptor is close-on-exec. A refused publication emits one classified failure and stops its service rather than looping.

The existing logrotate owns four service logs plus `/tmp/errors.log`. Keep selected-UID regular single-link admission, safe sticky `/tmp`, private archive ancestry and finite archives. The 10MiB/hourly threshold is rotation policy, not a hard byte cap.

For iframe upload results, default to SAMEORIGIN and preserve an explicit application DENY. Large result lists belong in the POST response body; bounded relative redirects serve short results. Test actual browser visibility and daemon hashes. In unauthenticated DNS deployments, HTTP 421 diagnostics must name `HTTP_ALLOWED_HOSTS`.

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

## Review Method

Method, not effort, decides what a review finds: on 2026-10-22 three independent reviews of one
ruTorrent commit overlapped on only four of 29 findings. For every review here:

- **Every external command and function the change calls, failing in every way:** a missing
  binary or extension, a non-zero exit, partial output, a function in `disable_functions`, a
  different implementation (the image is Alpine, so busybox and musl behave differently from GNU
  and glibc). Check that the documented fallback fires each way, not only the way the author's
  test models it.
- **Every resource the change acquires already has an owner:** a file under `/config` or
  `/data`, a service UID, a log, a generated configuration. Find the rootfs script or migration
  that owns it before adding another path.
- **Documented is not the same as right:** for a limit, default or migration, ask whether a bad
  value fails open or closed and whether the operator can see it.
- **Run the heaviest legitimate workload the previous image handled** (a large `/data`,
  thousands of torrents, slow or unusual volume filesystems) against every changed timeout or
  limit.
- **Mutate every line of new logic**, and review the failure paths of test fixtures and probes.
- **One instance means a class:** sweep for the rest before reporting, and split review agents
  by method, not only by area.
- **Code hygiene is its own check and its own agent**, with `git grep` or a run as evidence:
  duplication inside the change and against existing rootfs scripts, migrations and test
  helpers; dead code (functions, variables, branches, files and test helpers nothing calls or
  sources); unreachable code (conditions the real startup order cannot meet, fallbacks for a
  binary the image always ships); a hand-written step where an existing script or helper already
  owns it; empty or vacuous tests (a smoke check that asserts nothing, a check that still passes
  with the change reverted, a test file the runner never invokes).
- **Read the change as its reviewer will:** does the stated purpose need every changed line, and is
  every sentence of the commit or PR text true of the code?

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

## Agent Throughput

Follow `~/.agents/skills/agent-throughput/SKILL.md` for builds, waits, gates, reviews and reports, for
Claude and Codex alike. The 2026-10-29 measurement over 47 agents found model turns dominant:
reviewers spent 85 % of their active time in the model and implementers 59 %, and 89 % of tool calls
ran under 3 s while each cost a 10-12 s model turn. So write a round's checks as one script with a
short summary, keep long output in logs, and reuse the shared tools instead of new drivers:
native trees through `/home/dev/Documents/my_projects/rtorrent/.codex/skills/rtorrent-native/rt-tree.sh`
(ccache builder `rtorrent-builder:libtorrent-47114f09-ccache`), ruTorrent upstream-branch gates in
`/home/dev/Documents/my_projects/ruTorrent/.scratch/split25-gates/`, and the fork's `tools/matrix.sh`.
Build the full production image only for final acceptance, not on every round, and do not disable
the BuildKit cache without a reason. Before a full round on a new design, run its decisive probe
(termination, stall, the legitimate path it must not break).
Since 2026-10-29 (owner decision) mechanical agent work -- gates and test suites, byte-identical rebases, mutant batteries, evidence collection, cleanup -- runs at effort `high` (Claude Code: `effort: "high"` on the Agent call; Codex: `-c model_reasoning_effort=high`); design, implementation, review and investigation keep the session default. Rule 11 of `~/.agents/skills/agent-throughput/SKILL.md`.

## Owner's Remediation Workflow (2026-10-07)

During an authorized fix campaign, fix small related defects as they are found.
Record larger findings in the task's single findings ledger and resolve them before
final acceptance. Check for existing owners/helpers and remove confirmed scoped
duplication instead of adding parallel implementations. Verify each behavior change
while fixing it, then run the integrated gates on the frozen candidate. When the
owner requests one commit, aggregate the approved changes into one commit per
affected repository. Update local agent guidance when the workflow changes; keep
ignored instructions and task reports local. If the owner updates the bug lists during execution, reread those lists and include the new findings before freezing the final candidate. This does not authorize push or deploy.


## Полное код-ревью для всех агентов

Правило обязательно для Codex, Claude Code, Gemini/Antigravity и любых других AI-агентов, работающих с проектом. Перед работой прочитать корневой `AGENTS.md` проекта и применимые вложенные инструкции.

- При ревью незакоммиченных изменений, коммита, диапазона коммитов или PR читать каждый изменённый файл целиком, а не только diff hunks.
- Проверять каждый файл отдельно и связи между файлами: owners, call sites, imports, API, backend/frontend-потребителей, существующие helper-ы и тесты. При необходимости расширять ревью на связанный код за пределами diff.
- Искать дублирование внутри изменения и с существующим кодом, неиспользуемый и недостижимый код, параметры, ветки, импорты и helper-ы без продуктовых потребителей, ручные реализации вместо готовых helper-ов. API, которое вызывают только его собственные тесты, также проверять как возможный мёртвый код.
- Проверять пустые и холостые тесты: отсутствие содержательных утверждений, тавтологии, самопроверяющиеся mocks, отсутствие discovery, прохождение после отката продуктовой правки и имена, обещающие больше реальной проверки.
- Искать возможности упрощения и неоправданно сложный код: лишние абстракции и обёртки, глубокую вложенность, избыточные состояния и параметры, повторные проходы и проверки. Для каждой находки предлагать конкретное упрощение с сохранением поведения и контракта; это критерий ревью, а не разрешение менять код в review-only.
- Включать обоснованные мелкие замечания, ошибки документации, комментариев, имён и опечатки. Каждой находке давать точную ссылку, последствие и доказательство; отделять новые дефекты от существовавших до изменения. Не подменять находки неподтверждёнными предположениями.

## Проверки без лишних повторов

- До большого owner-suite проверить штатные runner-ы и закреплённые версии инструментов, выполнить ранние scoped lint/type checks и collection всех затронутых новых и существующих тестовых модулей. Полный static gate сохраняется на итоговый совместимый блок; ранняя обратная связь не заменяет его.
- В плане проверок указать, какое поведение и каких потребителей покрывает каждый gate. Перед повтором назвать продуктовую дельту после последнего успешного запуска и затронутые зависимости/среду. Совместимые исправления объединять перед общим прогоном; после чисто документационных правок выполнять применимые проверки документации. Обязательные owner-gates, RED/GREEN, независимое review и итоговую проверку точного дерева не пропускать.
- Не повторять одну тяжёлую пробу, сборку или полный discovery разными агентами на тех же входах. Передавать готовый результат и его границы; независимый reviewer читает код и получает собственное целевое доказательство, а не просто принимает самоотчёт исполнителя. Дублирующий полный прогон нужен при новой гипотезе, изменившихся входах или недостоверности прежнего результата.
- Одноразовый проверяющий скрипт сначала проверить на малом известном случае штатным runner-ом. Для discovery учитывать наследуемые методы, aliases/facade и полные test ID; не менять ожидаемый набор ради GREEN и не использовать проверяемый manifest как oracle. Ошибка самой пробы отделяется от ошибки продукта.
- В разборе длительности раздельно измерять подготовку, collection, исполнение тестов, hooks, очередь общего ресурса и ожидание результатов. Не складывать параллельные интервалы как wall-clock; промежуток между tool calls не называть временем модели без соответствующей telemetry. Сохранять один содержательный итог и ссылки на него вместо повторных развёрнутых отчётов.
- Число агентов выбирать по независимым проверочным обязанностям и дельте, а не по числу свободных слотов. Готовый продуктовый verdict сообщать сразу; оформление отчёта не удерживает статус review в ожидании. После follow-up повторно проверять изменившийся контур, сохраняя прежние доказательства для неизменившихся входов.
