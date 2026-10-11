# IvanShift/rutorrent

Opinionated ruTorrent + rTorrent container image with a focus on controlled source builds, small footprint, and a prepared ruTorrent fork.

## Features

- Multi-arch image (`linux/amd64`, `linux/arm64`) built on Alpine Linux 3.24.2
- PHP 8.5 with the IvanShift/ruTorrent fork selected by `RUTORRENT_REF`, the rTorrent fork based on 0.16.25, libtorrent 0.16.25, Alpine-provided c-ares, and UnRAR 7.23 built from RARLab's `unrarsrc-7.2.7.tar.gz`
- rTorrent uses the tinyxml2 XML-RPC backend
- MediaInfo/libmediainfo 26.05, built as local APKs against Alpine 3.24 with verified source checksums
- Non-root runtime (`UID` / `GID` configurable), healthcheck-ready, and persistent volumes
- Automatic size-based rotation for nginx access/error logs and the PHP-FPM error log; other application logs require separate monitoring
- Optional FileBot integration (portable 5.3.0) with OpenJDK 21 and chromaprint
- Supply-chain aware build: ruTorrent fetched by an explicit ref, plugins pinned to exact default commits, and a checksum-verified GeoIP database
- Custom plugin/theme directories in persistent `/config` storage
- Image additions: bundled `rutracker_check` handlers for RuTracker, Kinozal, NNMClub, Toloka, tfile, AniDUB and TapochekNet plus build-time fetched `geoip2` and `ratiocolor` plugins

## Tags

- `latest` – standard image built by CI from the exact ruTorrent and rTorrent source commits configured in `Dockerfile`
- `filebot` – the same source inputs with `FILEBOT=true`
- `rtorrent-<major.minor.patch>` / `filebot-rtorrent-<major.minor.patch>` – moving version tags
- `rtorrent-<version>-YYYY.MM.DD-run<run_id>-attempt<run_attempt>` and the corresponding `filebot-` tag – a tested CI snapshot

Use the image digest (`ivanshift/rutorrent@sha256:…`) to retain an exact image. The tag version describes the fork's upstream base; native patches are identified by the source revision labels.

Image labels describe the packaged sources: `org.opencontainers.image.version` and `io.github.ivanshift.rutorrent.revision` carry the ruTorrent commit, `org.opencontainers.image.revision` the commit of this repository, and `io.github.ivanshift.rtorrent.revision` and `io.github.ivanshift.libtorrent.revision` the native source commits beside their release labels. `org.opencontainers.image.licenses` (`MIT`) names this repository's packaging license. The components this Dockerfile builds from source (rTorrent, libtorrent, ruTorrent, mktorrent, dumptorrent and UnRAR) and the GeoLite2 data keep their notices under `/usr/share/licenses`; Alpine packages such as nginx, PHP and FFmpeg record their license in the package database (`apk info --license nginx`). Images from the earlier workflow carried `org.opencontainers.image.version=latest`, the title `docker-rutorrent` and the GitHub repository description instead.

CI publishes the runs of one commit one at a time and reuses four internal staging tags: `candidate-default-amd64`, `candidate-default-arm64`, `candidate-filebot-amd64` and `candidate-filebot-arm64`. Release tags promote the verified digests of the run's own tested archives, and a commit that is no longer `master` is refused before any release tag moves. Earlier run-specific `candidate-<run_id>-<attempt>-<flavor>-<arch>` tags require separate registry maintenance. `python3 scripts/candidate_cleanup_plan.py complete-tags.json --older-than-days 14` prints an offline plan from a complete Docker Hub tag listing; it preserves release tags and the four current staging tags. The planner never changes the registry. Collect every listing page and review the plan before separately authorizing deletion of legacy tags.

## Build

### Build arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `ALPINE_VERSION` | Alpine base image tag; change together with its digest | `3.24.2` |
| `ALPINE_DIGEST` | Expected multi-platform Alpine image index digest | `sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6` |
| `MKTORRENT_VERSION` | mktorrent release tag | `v1.1` |
| `MKTORRENT_COMMIT` | Expected resolved commit for `MKTORRENT_VERSION` | `b20ef699b4ee5ded2f078ead776c7deac969e19a` |
| `DUMP_TORRENT_VERSION` | dump-torrent release tag | `v1.7.0` |
| `DUMP_TORRENT_COMMIT` | Expected resolved commit for `DUMP_TORRENT_VERSION` | `ddf988d3099637c93dc0247854ca711c0a2a0289` |
| `UNRAR_VERSION` | Suffix used in RARLab's `unrarsrc-${UNRAR_VERSION}.tar.gz` filename; `7.2.7` contains UnRAR 7.23 sources | `7.2.7` |
| `UNRAR_SHA256` | Expected SHA256 of the UnRAR source archive | `01d903a7dcf413cb2925696d7796e48e38d471f79bfe7ef3ad2aebf6c12dbefd` |
| `MEDIAINFO_VERSION` | MediaInfo CLI and library release used for the local APK backport | `26.05` |
| `MEDIAINFO_SHA512` | Expected SHA512 of the MediaInfo CLI source archive | `219ca43d78dc84c4f66f9a5140705470a2555d879de26857e96080e47120b156ca56f1b22de6459fe0ea74d4a8b2ae75c9ffc8e55501f8982e7d5b41de0ee75c` |
| `LIBMEDIAINFO_SHA512` | Expected SHA512 of the MediaInfo library source archive | `8eef2b1214d2b512c8d4483f4a8a967cd05f065cb7dd0ec9ac09b2989a2bce0f60a897a7b154e90f2d6f9e3b53ac8bebe7e9c577dfe2afdb37686c027b3a97a7` |
| `FILEBOT` | Include FileBot, OpenJDK, chromaprint and GNU findutils; FFmpeg is in both variants | `false` |
| `FILEBOT_VER` | FileBot portable release tag | `5.3.0` |
| `FILEBOT_SHA256` | Expected SHA256 of the FileBot portable archive | `633d798a917607051b4f61be37b5ad923457050708c7e7eea99c09957583b575` |
| `RUTORRENT_REPO` | ruTorrent fork repository URL | `https://github.com/IvanShift/ruTorrent.git` |
| `RUTORRENT_REF` | ruTorrent fork ref fetched and checked out detached | `c7318e8af76fc87cc6afd649fa974e72a0cf7ef5` |
| `RUTORRENT_COMMIT_SHA` | Expected full commit; provide it separately for a mutable ref | `${RUTORRENT_REF}` |
| `GEOIP2_REPO` | GeoIP2 plugin repository URL | `https://github.com/Micdu70/geoip2-rutorrent.git` |
| `GEOIP2_REF` | GeoIP2 branch, tag, full ref, or commit | `cad8a11b47f02ff75358b7bd9c4137648f5fedd0` |
| `RATIOCOLOR_REPO` | RatioColor plugin repository URL | `https://github.com/Micdu70/rutorrent-ratiocolor.git` |
| `RATIOCOLOR_REF` | RatioColor branch, tag, full ref, or commit | `4aec1988be1e09b44799b71ed4a25751c695a6f2` |
| `GEOIP2_DB_VERSION` | P3TERX GeoLite2 release containing `GeoLite2-Country.mmdb` | `2026.10.10` |
| `GEOIP2_DB_SHA256` | Expected SHA256 of `GeoLite2-Country.mmdb` | `635078e412c4443a2950c2e3f775ccdc400e0a1fea5ffd32e5489e5401a9e51d` |
| `GEOIP2_DB_URL` | Optional controlled database mirror; SHA256 validation still applies | _(empty)_ |
| `LIBTORRENT_BRANCH` | libtorrent compatibility/version label; does not select source | `v0.16.25` |
| `LIBTORRENT_REF` | Optional libtorrent ref; an empty value selects `LIBTORRENT_VERSION` | _(empty)_ |
| `LIBTORRENT_VERSION` | Full SHA that the selected `LIBTORRENT_REF` must resolve to | `8f042ad68ee06e2d92d7f9f6e7df5ef5ccb312fb` |
| `RTORRENT_BRANCH` | CI release label; `v0.9.8` also enables C++17, xmlrpc-c and the legacy runtime marker. Does not select source | `v0.16.25` |
| `RTORRENT_REPO` | rTorrent source repository URL | `https://github.com/IvanShift/rtorrent.git` |
| `RTORRENT_REF` | Optional rTorrent ref; an empty value selects `RTORRENT_VERSION` | _(empty)_ |
| `RTORRENT_VERSION` | Full SHA that the selected `RTORRENT_REF` must resolve to | `b51b24221e783652eeb895604acd58bd26dbb2b8` |
| `STRICT_WERROR` | Treat selected warnings as errors during C++ builds | `true` |
| `GEOIP2_COMMIT_SHA` | Explicit expected GeoIP2 commit; required when its ref is mutable | _(empty)_ |
| `RATIOCOLOR_COMMIT_SHA` | Explicit expected RatioColor commit; required when its ref is mutable | _(empty)_ |
| `BUILD_DATE` | OCI creation timestamp; CI supplies it | _(empty)_ |
| `VCS_REF` | Docker repository revision label; CI supplies it | _(empty)_ |

### Standard build

```sh
docker build --tag ivanshift/rutorrent:latest https://github.com/IvanShift/docker-rutorrent.git
```

### FileBot build

```sh
docker build --tag ivanshift/rutorrent:filebot \
  --build-arg FILEBOT=true \
  https://github.com/IvanShift/docker-rutorrent.git
```

### Pinned ruTorrent ref build

```sh
docker build --tag ivanshift/rutorrent:ci \
  --build-arg RUTORRENT_REF="0123456789abcdef0123456789abcdef01234567" \
  https://github.com/IvanShift/docker-rutorrent.git
```

### Native rTorrent fork build

Set the source repository and ref together with the full expected commit. The build stops before compilation if the fetched commit differs. The native build accepts `RTORRENT_BRANCH=v0.16.x`; the published default is `v0.16.25`. Keep the label aligned with the reviewed fork base: it labels CI tags and keeps the C++20/tinyxml2 build path. It does not select the source. Setting it to `v0.9.8` activates the old C++17/xmlrpc-c build path and `/etc/rtorrent-legacy` runtime marker; use that setting only with matching legacy source and dependencies. The image records the native commit in `io.github.ivanshift.rtorrent.revision` and the ruTorrent commit in `io.github.ivanshift.rutorrent.revision`; `/usr/share/build-info/rutorrent-revision` contains the resolved ruTorrent commit. Inspect both labels with `docker image inspect --format '{{json .Config.Labels}}' <image>`.

```sh
docker build --tag ivanshift/rutorrent:native-review \
  --build-arg RTORRENT_REPO="https://github.com/example/rtorrent.git" \
  --build-arg RTORRENT_REF="0123456789abcdef0123456789abcdef01234567" \
  --build-arg RTORRENT_VERSION="0123456789abcdef0123456789abcdef01234567" \
  --build-arg RUTORRENT_REF="89abcdef0123456789abcdef0123456789abcdef" \
  .
```

Both commits must be reachable from their configured Git remotes. To test unpublished local commits, expose the corresponding clone through a Git server reachable by the build and pass that URL as `RTORRENT_REPO` or `RUTORRENT_REPO`. A host `file://` URL cannot access host files from the build container. Use a separate local image tag and keep images with temporary source URLs local.

### Custom plugin ref build

Repository and ref are independent, so a maintained fork or exact test commit can be selected without editing the Dockerfile:

```sh
docker build --tag ivanshift/rutorrent:custom-plugins \
  --build-arg GEOIP2_REPO="https://github.com/example/geoip2-rutorrent.git" \
  --build-arg GEOIP2_REF="0123456789abcdef0123456789abcdef01234567" \
  --build-arg RATIOCOLOR_REPO="https://github.com/example/rutorrent-ratiocolor.git" \
  --build-arg RATIOCOLOR_REF="89abcdef0123456789abcdef0123456789abcdef" \
  https://github.com/IvanShift/docker-rutorrent.git
```

The build validates source identities rather than trusting names alone: the rTorrent repository/ref and libtorrent branch must resolve to their corresponding expected commits, as must the mktorrent/dump-torrent tags. The default ruTorrent and plugin refs are exact commits; the GeoLite2, UnRAR, and optional FileBot downloads must match their configured SHA256 values. Plugin stages also verify their required entry files, and the image verifies a GeoLite2 lookup after installation. Git source transfers use at most three 180-second attempts with a 30-second low-speed cutoff. The FileBot download allows three retries, a 15-second connection limit, a 180-second limit per attempt and a 600-second retry budget; checksum verification remains mandatory.

For reproducible source inputs, use exact commits and matching checksums. The `GEOIP2_COMMIT_SHA` and `RATIOCOLOR_COMMIT_SHA` arguments provide independent expected source identities for mutable refs; the build rejects mutable refs without a full expected SHA. Docker cache does not detect when a remote branch or tag moves, so rebuild the corresponding `geoip2-source` or `ratiocolor-source` stage without cache when deliberately using a mutable ref. These protections do not guarantee byte-identical images: the package repositories (including `apk upgrade`) and builder environment can change over time. The Alpine image index and Dockerfile frontend are pinned by verified digests. Change `ALPINE_VERSION` and `ALPINE_DIGEST` together for another base; mirror APK inputs when full image reproducibility is required.

MediaInfo is packaged locally because Alpine 3.24 provides the older 26.01 release. The `mediainfo-builder` stage uses the recipes in `packaging/`, verifies both upstream source archives, and builds matching CLI/library APKs for the target architecture. The final image installs these APKs through `apk`, so their files and shared-library dependencies remain managed by the package manager. Its remaining dependencies come from the stable Alpine repositories; no edge repository is enabled. The build generates a temporary APK signing key: only public keys are mounted for installation, and the private key stays in the build stage. Updating MediaInfo requires changing its version and both source checksums together.

## Runtime configuration

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` / `GID` | User/group IDs used by rTorrent & services | `991` |
| `PORT_RTORRENT` | TCP listening port for rTorrent; decimal 1–65535 | `45000` |
| `MODE_DHT` | DHT mode (`off`, `on`, `auto`, `disable`) | `off` |
| `PORT_DHT` | UDP DHT port; decimal 1–65535 | `6881` |
| `PEER_EXCHANGE` | Enable PEX (`yes` / `no`) | `no` |
| `DOWNLOAD_DIRECTORY` | Main downloads directory; an absolute path below `/data` | `/data/downloads` |
| `CHECK_PERM_DATA` | Recursively assign ordinary `/data` working files to `UID:GID`; exactly `true` or `false`. Trust checks still apply | `true` |
| `HTTP_AUTH` | Enable HTTP authentication; exactly `true` or `false` | `true` |
| `ALLOW_UNAUTHENTICATED_HTTP` | Explicit opt-out required with `HTTP_AUTH=false`; use only on a trusted, isolated network | `false` |
| `ENABLE_RPC2` | Enable the rTorrent XML-RPC endpoint at `/RPC2`; must be exactly `true` or `false` | `false` |
| `RU_PUBLIC_URL` | Public HTTP(S) origin and optional application prefix used for generated URLs | _(empty)_ |
| `RU_PROFILE_MASK` | Mode of the profile, temporary and log entries that ruTorrent creates; three or four octal digits keeping owner `rwx`. Files receive it without execute bits | `0700` |
| `RUTORRENT_WATCHDOG` | Observe service readiness; exactly `true` or `false` | `true` |
| `RUTORRENT_WATCHDOG_START_SECONDS` | Watchdog startup allowance; decimal 0–3600 seconds | `600` |
| `RUTORRENT_WATCHDOG_INTERVAL_SECONDS` | Watchdog observation interval; decimal 1–300 seconds | `15` |
| `RUTORRENT_WATCHDOG_FAILURES` | Consecutive observations before a persistent-failure diagnostic; decimal 1–100 | `3` |

`HTTP_AUTH` defaults to `true` during startup when omitted; an explicitly empty value is rejected.

`PORT_RTORRENT`, `MODE_DHT`, `PORT_DHT`, `PEER_EXCHANGE` and `DOWNLOAD_DIRECTORY` seed the image-provided `.rtorrent.rc` when `/config/rtorrent/.rtorrent.rc` is first created. After that, edit the persisted file to change these values. Startup reads its effective `directory.default.set` and aligns ruTorrent with it, warning if `DOWNLOAD_DIRECTORY` differs. On every start `DOWNLOAD_DIRECTORY` must be an absolute literal path: control characters, traversal and unsupported quoting exit with status 64. A value that seeds a new rc must also name a directory below `/data`; otherwise startup exits with status 64 before the rc is written. Once the rc exists, its `directory.default.set` must stay below `/data/`, and a `DOWNLOAD_DIRECTORY` elsewhere only produces that warning.

`RU_PROFILE_MASK` defaults to `0700`: nginx, PHP-FPM, rTorrent and the plugin workers all run as `UID`, so no group or other user needs access. Files such as `share/settings/loginmgr.dat`, which stores tracker credentials, become `0600` when ruTorrent next writes them. `0777` restores the upstream default, and a value without owner `rwx` exits with status 64. A persisted `conf/config.php` from an older image that still assigns `$profileMask = 0777;` itself ignores the variable: check it with `docker exec rutorrent grep -n profileMask /config/rutorrent/conf/config.php` and replace that assignment with `$profileMask = $_ENV['RU_PROFILE_MASK'] ?? 0777;`, which reads the variable, or set the mask there directly.

### Additional variables when FileBot enabled

| Variable | Description | Default |
|----------|-------------|---------|
| `FILEBOT_LICENSE` | Path to license file (`/config/filebot/...`) | _(required)_ |
| `FILEBOT_RENAME_METHOD` | File renaming strategy | `symlink` |
| `FILEBOT_LANG` | Language preference | `en` |
| `FILEBOT_CONFLICT` | Conflict handling (`skip`, `override`, …) | `skip` |

### Volumes

- `/data` – downloads, watch folders, media symlinks
- `/config` – ruTorrent, rTorrent, FileBot configuration

Common subdirectories (auto-created on first start):

- `/data/.watch` – auto-load torrents
- `/data/.session` – rTorrent session files
- `/data/downloads` – active downloads
- `/data/media` – FileBot output (when enabled)
- `/config/rtorrent` – `.rtorrent.rc` and overrides
- `/config/rutorrent/conf` – ruTorrent global config
- `/config/rutorrent/share` – ruTorrent user data/cache
- `/config/custom_plugins` / `/config/custom_themes` – custom extensions; bundled names cannot be replaced
- `/config/filebot/*` – FileBot license and scripts
- `/config/rutorrent/erasedata-jobs` – protected deletion jobs (0700), with `.erasedata-first-use` as its empty root-owned witness (0400)
- `/data/.session/.rutorrent-stop-intents`, `.retrackers-migration`, `.autotools-file-jobs` – private native/plugin journals; native Move queue records live as `<HASH>.move` beside stop claims, and payload recovery journals stay in `.autotools-file-jobs`; the session location follows `session.path.set`

### Persistent configuration and migration

`/rutorrent/conf-dist` is the pristine configuration shipped by the selected fork. Startup restores missing files into `/config/rutorrent/conf` and preserves existing files; removing a file intentionally does not keep it absent. Existing `xmlrpc_proxy.php` is preserved, while new volumes use the image policy until the file is explicitly installed. Compare an operator file against the image copy with:

```sh
docker exec rutorrent diff /rutorrent/conf-dist/plugins.ini /config/rutorrent/conf/plugins.ini
```

The preserved `config.php` is then migrated on every start. The image sets `$topDirectory` from the persisted download directory, uses the internal SCGI socket by default, updates `/RPC` in `$XMLRPCMountPoint`, and fills missing/default paths for PHP, curl, ffmpeg, unrar, sox and mediainfo. Custom executable wrappers and PHP expressions are preserved. The removed `dumptorrent` setting is not added. `RU_SCGI_HOST` and `RU_SCGI_PORT` override only PHP's connection settings; they do not move the daemon, scheduler or healthcheck from `/run/rtorrent/rtorrent.sock`, so use them only with a deliberately coordinated runtime configuration.

Use `/config/rutorrent/conf/config.local.php` for supported operator settings such as a narrower `$topDirectory`. Image policy enables CSRF checks after that include and defaults to a shared profile. `$enableCSRFCheck=true` remains enforced; set `RU_FORBID_USER_SETTINGS=false` explicitly for per-user profiles after inspecting existing data (see HTTP authentication). Keep the PHP file valid; a parse/lint failure aborts startup without modifying its original contents.

`update-config` also migrates old rTorrent command names and removes deprecated no-op settings. Its native and legacy rules differ so that 0.9.8 limits remain valid; native-only image defaults are retained as reversible comments when switching modes. Keep `config.php` and `.rtorrent.rc` as regular readable files. Writable directory mounts allow atomic file replacement with owner and permissions retained. A writable single-file bind mount is updated in place when rename is unavailable; ordinary shutdown signals are deferred until its validated write or rollback is complete. SIGKILL and power loss cannot make that fallback atomic; unchanged canonical files also work in read-only directories. A read-only file that needs a change or a symlink in place of either file is refused with a clear diagnostic. Back up operator configuration before changing image versions.

The image provides `/usr/local/bin/mktorrent`. Updated fork sources select it automatically when it is executable, setting both `$useExternal = 'mktorrent';` and its absolute `$pathToCreatetorrent`; otherwise they use the internal builder. `$useInternalHybrid=true` retains the internal builder for hybrid torrents. Older pinned fork sources still default to the internal builder. To change either choice, copy the plugin's complete `conf.php` from the image and mount the configured copy read-only at `/rutorrent/app/plugins/create/conf.php`. Set `$useExternal = false;` for the internal builder, or configure another supported creator and its full path. Keep the remaining settings and make the file readable by the chosen UID.

### Fork Changes

The Docker build fetches the prepared ruTorrent fork by `RUTORRENT_REF` and checks its resolved commit against `RUTORRENT_COMMIT_SHA`. The default repository and exact source identity are listed in the build-argument table. It no longer copies `overrides/rutorrent` over the downloaded tree and no longer applies `sed` patches to ruTorrent files. Third-party plugins are fetched in independent source stages at exact default commits, then copied into the cleaned runtime tree without VCS metadata.

#### `rutracker_check`

The selected fork bundles handlers for RuTracker, Kinozal, NNMClub, Toloka, tfile, AniDUB and TapochekNet. Its source routes a recognized comment through the registered handlers before trying announce-based fallback.

- Torrent replacement prepares persistent cleanup jobs for obsolete paths and removes eligible empty directories through the protected deletion queue.
- NNMClub uses the current torrent's typed announce credentials for tracker scrape checks, then a guest topic/download fallback when needed. Replacement requires validated torrent bytes and a usable account credential; challenge and network failures remain visible retryable results.
- Requests use the fork's configured browser User-Agent. Tracker availability and anti-bot behavior remain dependent on the live service.

These describe the [pinned default checker source](https://github.com/IvanShift/ruTorrent/tree/c7318e8af76fc87cc6afd649fa974e72a0cf7ef5/plugins/rutracker_check); changing `RUTORRENT_REF` selects that revision's plugin behavior.

#### Build-Time Plugins

- **`geoip2`**: Fetched from `GEOIP2_REPO` at `GEOIP2_REF`. Its bundled 2023 database is replaced with checksum-verified `GeoLite2-Country.mmdb` from the versioned [P3TERX/GeoLite.mmdb](https://github.com/P3TERX/GeoLite.mmdb) release.
- **`ratiocolor`**: Fetched from `RATIOCOLOR_REPO` at `RATIOCOLOR_REF`. The default is the exact Micdu70 commit listed in the build-argument table.

The GeoLite2 database source and license notice are retained at `/usr/share/licenses/GeoLite2/NOTICE` in the image. Updating `GEOIP2_DB_VERSION` also requires updating `GEOIP2_DB_SHA256`; the build fails instead of accepting an unverified or missing database. The upstream release retains only the two newest database versions, so an old pin can disappear and a clean build can fail with 404. Refresh both pins before an uncached build, or use a controlled `GEOIP2_DB_URL` mirror with the same checksum. Follow the database license requirements when maintaining a mirror.

#### Upstream Compatibility

General rTorrent 0.16.x / tinyxml2 / trusted `httprpc` compatibility is handled by upstream ruTorrent 5.3.x. The Docker image does not maintain a separate compatibility overlay for `xmlrpc.php`, `httprpc`, `getplugins.php`, or generic plugin command aliases.

### Log Rotation

The image includes one automatic log rotation service for nginx, PHP-FPM and ruTorrent application logs:

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_SIZE` | Positive decimal size threshold in bytes; 1–999999999 | `10485760` (10MB) |
| `SLEEP_SECS` | Positive decimal interval in seconds; 1–999999999 | `3600` (1 hour) |

The service checks three `/tmp/nginx-*.log` files, `/tmp/php_error.log` and the default ruTorrent application log `/tmp/errors.log`. Invalid `MAX_SIZE` or `SLEEP_SECS` values produce a warning and use the defaults. Logs redirected to stdout, application logs moved by `RU_LOG_FILE` or `$log_file`, rTorrent and FileBot logs are not covered. The size threshold is checked once per interval: an active log can grow beyond `MAX_SIZE` between checks. Reduce `SLEEP_SECS` when a shorter growth window is needed.

Archives are stored in `/tmp/rutorrent-logrotate`, a directory owned by the selected service UID with mode `0700`. For example, `/tmp/errors.log` rotates to `errors.log.1` there; its previous rotation becomes `errors.log.1.gz`, replacing the older compressed archive. Each log retains the active file and at most two archives. Application logging resumes by opening a new file on the next line; nginx and PHP-FPM receive a verified reopen signal. Unsafe log or archive types, owners, links or parent permissions refuse rotation with a classified message once per path and reason. Checks continue: a repaired path logs `logrotate: resumed …` and rotates again, and a later recurrence is reported again. Existing archives beside the original log paths are left in place.

### Ports

- `8080/tcp` – ruTorrent UI
- `45000/tcp` (configurable via `PORT_RTORRENT`)
- `6881/udp` – DHT only when enabled; use `45000/udp` instead if DHT is configured on the listen port

## FAQ

- **Can I use one port for everything (peers + DHT)?** Yes. Set `network.listen.port.range.set = 45000-45000`, set `network.listen.port.random.set = no`, and set `dht.override_port.set` to the same number. Expose the listen port as TCP and the enabled DHT port as UDP. For an existing volume, edit `.rtorrent.rc`; environment variables seed only new files.
- **Listen port vs DHT port — what’s the difference?** The listen port (`network.listen.port.range.set`) is where other peers connect to you for torrent data; it uses TCP; this build does not provide uTP. `network.listen.port.random.set` controls whether rTorrent randomly selects a port from that range. The DHT port (`dht.override_port.set`) is UDP-only and used to talk to the distributed hash table for trackerless peer discovery. They can be the same (simpler firewall/NAT rules) or different if you need to split traffic; open the listen port as TCP and the DHT port as UDP when DHT is enabled.

## Usage

### Default launch

Use a 60-second Docker shutdown deadline; s6 gives rTorrent 55 seconds inside it.
For Docker Compose, set the deadline on the service:

```yaml
services:
  rutorrent:
    image: ivanshift/rutorrent:latest
    stop_grace_period: 60s
```

```sh
docker run --name rutorrent -d --stop-timeout 60 \
  --dns 1.1.1.1 \
  --dns 8.8.8.8 \
  -p 8080:8080 \
  -p 45000:45000 \
  -v rutorrent_config:/config \
  -v rutorrent_data:/data \
  ivanshift/rutorrent:latest

docker exec -it rutorrent gen-http-passwd
```

### Simple launch

```sh
docker run --name rutorrent -d --stop-timeout 60 \
  --dns 1.1.1.1 \
  --dns 8.8.8.8 \
  -e UID=1000 \
  -e GID=1000 \
  -p 8080:8080 \
  -p 45000:45000 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/data:/data \
  ivanshift/rutorrent:latest

docker exec -it rutorrent gen-http-passwd
```

UI: <http://localhost:8080>

The examples set public DNS servers for tracker lookups. Use resolvers reachable from your network if these are unavailable; `rutracker_check` needs working DNS for `rutracker.org`.

`UID` and `GID` select the numeric identity used by rTorrent. Startup remaps the built-in `torrent` account and group when those IDs are free, or reuses an existing group such as `users:100`; a group name is resolved once and services receive its numeric ID. Startup adjusts ownership of ordinary working paths and, by default, `/data`; protected journals, root configuration and witnesses are excluded from recursive ownership changes. Before first use, `/config`, `/config/rutorrent`, `/data` and the session directory must be real directories owned by root or the selected `UID`. Their permissions and private journal entries are checked before ownership changes. `CHECK_PERM_DATA=false` skips the data ownership sweep and does not disable trust checks. See “Upgrading and troubleshooting” for repairs and read-only mounts.

### FileBot launch

Place the license in persistent storage before starting. Startup validates real trusted parent directories and a single-linked regular license, then imports it as the selected UID. `/data/media` is assigned to that UID even with `CHECK_PERM_DATA=false`. Root-squashed mounts require host provisioning of the selected user's access. The example uses `UID:GID=1000:1000`; create trusted parent directories without group/world write permissions (use your own paths and IDs):

```sh
sudo install -d -o 1000 -g 1000 -m 0755 /mnt/docker/rutorrent/config /mnt/docker/rutorrent/data
sudo install -d -o 1000 -g 1000 -m 0700 /mnt/docker/rutorrent/config/filebot
sudo install -o 1000 -g 1000 -m 0600 ./FileBot_License.psm /mnt/docker/rutorrent/config/filebot/FileBot_License.psm
```

```sh
docker run --name rutorrent-filebot -d --stop-timeout 60 \
  -e UID=1000 \
  -e GID=1000 \
  -e FILEBOT_LICENSE=/config/filebot/FileBot_License.psm \
  -p 9080:8080 \
  -p 45000:45000 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/data:/data \
  ivanshift/rutorrent:filebot

docker exec -it rutorrent-filebot gen-http-passwd
```

### HTTP authentication

New and upgraded containers require HTTP authentication by default, including deployments that previously omitted `HTTP_AUTH`. Before a password file exists, a request without credentials returns 401 and a request with credentials returns 403. Create or update the user with the command shown after the launch examples:

```sh
docker exec -it rutorrent gen-http-passwd
```

The command requires trusted root-owned `/config`, `/config/nginx` and `/config/nginx/passwd` ancestors. Startup keeps the password directory as `root:GID 0750` and the file as `root:GID 0640`; nginx reads it through its service group. Existing password paths must be regular and single-linked. The command writes a password hash into `/config/nginx/passwd/rutorrent_passwd`; reusing `/config` preserves it and nginx reads updates without a restart. HTTP Basic authentication requires TLS when the network is untrusted. For root-squashed `/config/nginx` storage, provision and update the `root:GID 0640` password file offline from the trusted host/server administrator; the in-container command cannot publish into a root-only squashed tree.

An installation that explicitly sets `HTTP_AUTH=false` must remove that override or set `ALLOW_UNAUTHENTICATED_HTTP=true`. This exposes stored tracker credentials and torrent controls to clients that can reach port 8080. Keep this mode confined to trusted clients. In this mode nginx accepts IP-literal Hosts and `localhost`; set `HTTP_ALLOWED_HOSTS` to a comma-separated list of exact DNS names used by your browser or trusted proxy. Other names receive 421, which prevents a foreign DNS name from becoming a trusted origin after rebinding. `/tmp/nginx-rutorrent-access.log` uses the standard `combined` format; a request refused this way, and in this mode also nginx's own 400 for a missing or malformed Host, appends one quoted field, `"host-rejected-configure-HTTP_ALLOWED_HOSTS"`. Origin/CSRF checks remain enabled, and the image defaults to a shared ruTorrent profile (`$forbidUserSettings=true`). Set `RU_FORBID_USER_SETTINGS=false` explicitly to retain per-user profiles; `retrackers` then refuses shared-daemon operation in those profiles. Startup warns when existing `share/users/*/settings` data is hidden by the shared default. To undo an earlier forced shared-profile migration, set this override and inspect the saved `conf/config.php` and `config.local.php` before restarting.

`/php/getplugins.php` is a WebUI AJAX bootstrap endpoint. A direct GET client must send
`X-Requested-With: XMLHttpRequest`; a plain authenticated `curl` request receives 403.
State-changing WebUI requests must carry an `Origin` or `Referer` matching the URL's scheme, host and port. A reverse proxy must preserve the external `Host` (including its port), set `X-Forwarded-Proto` to the external scheme, and overwrite that header rather than accept a client-supplied value. Forwarding headers should come only from the trusted proxy. Test a normal WebUI upload after enabling TLS; an origin mismatch returns 403. For explicitly trusted additional browser origins, updated fork sources accept `RU_TRUSTED_ORIGINS` as a comma-separated list of complete HTTP(S) origins (scheme, host and port), or exact browser-extension origins. Wildcards are refused. This environment value is passed to PHP only when provided; standalone deployments can use the fork's `$trustedOrigins` array in `config.local.php`.
`RU_PUBLIC_URL` supplies an absolute URL such as `https://torrent.example/app/` when the trusted proxy rewrites the Host or application prefix. It accepts HTTP(S), an optional port and an optional path prefix; credentials, query strings, fragments, whitespace, control characters and backslashes are refused. An empty environment value preserves `$publicURL` from `config.local.php`; when both are empty, generated URLs use the validated direct Host, one `X-Forwarded-Proto` value and the exact application script suffix; `X-Forwarded-Host` is ignored. This setting does not grant a browser origin access to CSRF-protected requests.

Uploads accept up to 100 files per request within the combined 100M nginx/PHP request-body limit. The private health route uses a separate two-worker PHP-FPM pool with a two-second upstream response limit, so ordinary long-running PHP work does not consume its workers. Ordinary requests retain their existing execution policy.

`/healthz-plugins` is denied on the public listener. The container scheduler uses a private Unix socket, which a reverse proxy must not expose.

### rTorrent RPC2 endpoint

`/RPC2` is inaccessible by default. It is the fork's filtering XML-RPC proxy. Enable it only for clients that need it, and protect it with HTTP authentication:

```sh
docker run --name rutorrent-rpc2 -d --stop-timeout 60 \
  -e ENABLE_RPC2=true \
  -e HTTP_AUTH=true \
  -p 8080:8080 \
  -v rutorrent_config:/config \
  -v rutorrent_data:/data \
  ivanshift/rutorrent:latest

docker exec -it rutorrent-rpc2 gen-http-passwd
```

With the default `sanitize` policy, command execution, method/schedule changes, session/default-directory changes and local-path loads are rejected with HTTP 403 and XML-RPC fault `-501`. The daemon can additionally reject a command with HTTP 200 and fault `-507` (`not allowed for untrusted connections`).

The native daemon returns metadata values containing XML-forbidden bytes, invalid UTF-8 or carriage returns as standard XML-RPC base64, preserving their exact bytes. Remote clients must accept the protocol's binary value type; ordinary XML-safe UTF-8 remains a string. Unsafe custom-map keys cause a classified fault for the whole map instead of malformed XML; individual keys remain readable through `d.custom.keys` and `d.custom`.

New volumes use the image's proxy policy. An old `/config/rutorrent/conf/xmlrpc_proxy.php` remains active; inspect it before retaining it. To install the image policy explicitly:

```sh
docker exec rutorrent-rpc2 cp /rutorrent/conf-dist/xmlrpc_proxy.php /config/rutorrent/conf/xmlrpc_proxy.php
```

Enabling `ENABLE_RPC2=true` with `HTTP_AUTH=false` still permits torrent controls to reachable clients. Startup warns about this combination; confine it to trusted clients.

### Custom plugins/themes

Place each custom plugin in its own directory under `/config/custom_plugins`, with the plugin's entry files directly inside that directory. Place each theme in its own directory under `/config/custom_themes`; a repository containing several themes must be unpacked into separate theme directories. Bundled plugin/theme names are reserved and custom directories do not replace them. Install only code you trust: server-side plugins run as the service user and can read torrent and tracker settings.

The image includes `geoip2` and `ratiocolor`; cloning them again into the custom directory is unnecessary. See the startup diagnostics when a custom name collides or an entry directory is invalid.

## Upgrading and troubleshooting

Stop every container using the same volumes and back them up before maintenance. Use `docker stop --timeout 60 rutorrent` (or `--stop-timeout 60` when creating the container) to give rTorrent time to save its session; `-t 60` is the same option on older Docker clients, which predate `--timeout`. If `HTTP_AUTH=false` and the browser or proxy uses a DNS name, set `-e HTTP_ALLOWED_HOSTS=<name>` (comma-separated exact names) when recreating the container. An omitted name receives HTTP 421 on every path; IP literals and `localhost` are accepted. After an image update, reload the UI without browser cache if it still serves an older JavaScript bundle.

Session and queue storage requires POSIX owners, permissions, regular files and atomic rename semantics. For root-squashed storage, a host administrator must preseed the complete configuration and private state through a trusted nonsquashed setup before mounting it here. This includes private session/journal directories and records, the protected queue and its root-owned witness, and root-owned authentication metadata. Actual NFS mount behavior must be verified for the deployment. The selected UID needs read/write access to its working files. Startup uses a descriptor-based ownership helper and can traverse eligible working files as that UID when root-squash blocks root; foreign private records and unsafe ancestors are refused. Ordinary shared files with immutable or read-only ownership restrictions produce bounded warnings, while private admission failures abort startup. Linux 5.4 or newer is required for the native background-task cancellation helper. Data-directory rename operations use `renameat2(RENAME_NOREPLACE)` when supported. On filesystems such as 9p that return `EINVAL`, `ENOSYS` or `EOPNOTSUPP` for this flag, the shared helper checks that the destination is absent and uses ordinary rename under the caller's lock. Processes sharing the service UID are trusted; keep untrusted writers outside those directories. The startup probe reports native support with status 0 and usable locked fallback with status 5; fallback is informational and leaves file operations available. Status 3 (`rename-noreplace-unsupported`: even a plain rename was refused, or the probe saw an entry replaced) warns that erasedata removal, datadir moves and AutoTools Move refuse work on that filesystem; other statuses are reported with their classified reason. FAT/exFAT, incompatible network mounts and older kernels may not provide these properties; keep the session and protected journals on suitable local storage.

Images built with the native retrackers dispatcher mark both fixed workers (`plugins/retrackers/update.php` and `dispatch.php`) as required. Startup checks their real ancestors and single-linked readable leaves as the selected service identity before configuration changes and again after custom plugin installation. It also requires the fixed `/usr/bin/php85` and its bootstrap ancestors to remain root-owned, safe and executable by that identity. Mounted older or empty application trees cannot disable this requirement; startup refuses unsafe mounts without changing their modes. The image build normalizes only the known immutable PHP/worker paths. This admission requires Linux 5.8 or newer and a runtime security policy permitting [`faccessat2`](https://man7.org/linux/man-pages/man2/access.2.html); an unavailable access check refuses visibly. The Linux 5.4 cancellation-helper requirement above remains separate.


| Exit / symptom | Diagnostic | Action |
|---------------|------------|--------|
| 64 | `HTTP_AUTH`, `ENABLE_RPC2`, `CHECK_PERM_DATA` or `RU_FORBID_USER_SETTINGS` must be exactly true or false; invalid UID, GID, port, DHT/PEX, download directory or `RU_PROFILE_MASK` value | Correct the named environment value. `HTTP_AUTH=false` also requires `ALLOW_UNAUTHENTICATED_HTTP=true`. |
| 78 | `Cannot attest …` / unsafe or foreign-owned directory | Inspect the named path, owner and mode outside the application container. The message names its repair: the offline permissions command below for volume paths, the attestation command for the erasedata witness, or recreating the container for image files. |
| 78 / erasedata unavailable | Missing/unsafe `.erasedata-first-use`, missing `erasedata-jobs`, or legacy queue needs operator attestation | Inspect pending jobs while stopped, then follow “Attesting the erasedata queue”. |
| Nonzero migration exit | `Unable to update old configurations (exit …)` | Read the preceding `update-config` diagnostic. Repair invalid syntax or provide writable regular files; startup does not launch services after a migration error. |
| 401 / 403 before first user | HTTP authentication has no provisioned password file | Run `docker exec -it rutorrent gen-http-passwd`. |
| 421 at every path with `HTTP_AUTH=false` | DNS Host is absent from `HTTP_ALLOWED_HOSTS` | Recreate with the exact browser/proxy DNS names in `HTTP_ALLOWED_HOSTS`; keep origin checks enabled. |
| 403 during WebUI POST | Origin/Referer mismatch | Correct the trusted reverse proxy's external Host and scheme. |
| 403 / fault -501 at `/RPC2` | Proxy policy refused the command | Check `conf/xmlrpc_proxy.php`; keep executable and filesystem controls restricted. |
| HTTP 200 / fault -507 at `/RPC2` | Native untrusted-connection policy refused the command | Use a supported remote command; this is a daemon policy rejection. |

### Upgrading an existing installation

The commands use the container name `rutorrent` and the Windows Docker Desktop layout of the examples below (`D:/ruTorrent/config`, `D:/ruTorrent/data` and `D:/Downloads`); substitute your names, mounts, image tag and identity. Run them from `cmd.exe`. The `docker exec` lines contain no `$` or `%`, so they also work unchanged in POSIX shells.

1. While the old container and its daemon still run, clear retrackers recovery markers; otherwise the new image disables retrackers with `receipt-ledger-corrupt`. Images built before the tool moved on 2026-09-30 keep it under `tasks/`, newer images under `tools/`. This shows which one the old container has:

   ```bat
   docker exec rutorrent sh -c "ls /rutorrent/app/*/retrackers-clear-markers.php"
   ```

   With the `tasks/` tool, review its report, then clear all reported markers:

   ```bat
   docker exec -u torrent rutorrent php85 /rutorrent/app/tasks/retrackers-clear-markers.php
   docker exec -u torrent rutorrent php85 /rutorrent/app/tasks/retrackers-clear-markers.php apply
   ```

   The `tools/` tool prints each marked hash with its fingerprint. Pass the reviewed pairs from that report to `docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-clear-markers.php apply <HASH> <FINGERPRINT> [<HASH> <FINGERPRINT> ...]`; every pair is checked against the run's own report before anything is cleared, and each marker is then cleared and read back on its own. While retrackers runs in that daemon, for example after a rollback, it refuses with `receipt ledger rr.receipts.v1 is not empty` and changes nothing; continue then and clear the markers in the new container (step 7). Repeat this on-disk check until both counts are `0` and `0`, because an old daemon may acknowledge `session.save` before its asynchronous writes finish:

   ```bat
   docker exec rutorrent sh -c "grep -l 'retrackers-recovery[1-9]' /data/.session/*.torrent.rtorrent | wc -l; grep -l 'retrackers-recovery[1-9][0-9]*:v1:original' /data/.session/*.torrent.rtorrent | wc -l"
   ```

   Use the actual session path if customized. Do not delete markers directly from session files while the daemon is running.

2. Older images may stop without saving their session. Save explicitly through the trusted SCGI socket (`session.save` is blocked on untrusted HTTP RPC); the answer contains `<i8>0</i8>`. Wait several seconds, then check that no `*.new` session file remains (expected output `0`):

   ```bat
   docker exec -u torrent rutorrent php85 -r "define('S', stream_socket_client('unix:///run/rtorrent/rtorrent.sock')); define('B', '<methodCall><methodName>session.save</methodName></methodCall>'); define('H', 'CONTENT_LENGTH'.chr(0).strlen(B).chr(0).'SCGI'.chr(0).'1'.chr(0)); fwrite(S, strlen(H).':'.H.','.B); echo strstr(stream_get_contents(S), '<?xml'), PHP_EOL;"
   docker exec rutorrent sh -c "find /data/.session -maxdepth 1 -name '*.new' | wc -l"
   ```

3. Record the old container's run configuration, stop it and keep it for a rollback. The first command prints its environment; the second prints its host configuration with published ports (`PortBindings`, for example `6881/udp` with `MODE_DHT=on`), DNS servers (`Dns`), mounts (`Binds`) and restart policy (`RestartPolicy`). Save both outputs: step 6 and a rollback need them. Then disable its restart policy so that a Docker or host restart cannot start it next to the new container:

   ```bat
   docker inspect --format "{{json .Config.Env}}" rutorrent
   docker inspect --format "{{json .HostConfig}}" rutorrent
   docker stop --timeout 60 rutorrent
   docker rename rutorrent rutorrent-old
   docker update --restart no rutorrent-old
   ```

4. Back up the stopped volumes, keeping Linux owners and modes where the backup tool can. A restore without that metadata needs the permissions command and then the attestation command before the next start. If it also lost the owners, so that every entry is `root:root` (as a copy onto Docker Desktop `drvfs` volumes mounted with `uid=0;gid=0` produces), run the root-owner command before them. All three are in “Repairing an existing profile or changing UID/GID”.

5. Repair permissions offline with the inspection and permissions commands in “Repairing an existing profile or changing UID/GID”. Repeat this step after every return from an older image.

6. Create the new container with the old options recorded in step 3 (environment, published ports, DNS servers, mounts and restart policy) and a 60-second stop deadline. The example shows only the default layout. With `HTTP_AUTH=false`, also pass `ALLOW_UNAUTHENTICATED_HTTP=true` and, for access by DNS name, `HTTP_ALLOWED_HOSTS=<name>`:

   ```bat
   docker run -d --name rutorrent --restart unless-stopped --stop-timeout 60 -p 8080:8080 -p 45000:45000 -e UID=991 -e GID=991 -v D:/ruTorrent/config:/config -v D:/ruTorrent/data:/data -v D:/Downloads:/data/downloads ivanshift/rutorrent:latest
   ```

7. Check the new container. After its first health check the status line reads `running restarts=0 health=healthy`, and the log contains no `Cannot attest`. If retrackers reports `receipt-ledger-corrupt` in the WebUI or in the container's application log (`/tmp/errors.log`, unless `RU_LOG_FILE` or `$log_file` in `config.php` names another file), clear the markers here: run the report below, pass its pairs to `docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-clear-markers.php apply <HASH> <FINGERPRINT> [<HASH> <FINGERPRINT> ...]`, and reload the WebUI; retrackers starts without a container restart. A restart changes the fingerprints, so report again after one.

   ```bat
   docker inspect -f "{{.State.Status}} restarts={{.RestartCount}} health={{.State.Health.Status}}" rutorrent
   docker logs --tail 40 rutorrent
   docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-clear-markers.php
   ```

   Besides the progress lines, the log can contain these expected messages: `Warning: legacy erasedata profile…` until attestation, the unauthenticated-HTTP warnings with `HTTP_AUTH=false` (the DNS-name warning only without `HTTP_ALLOWED_HOSTS`), `Migrated … changed lines` with a count that depends on the input rc, and the rename probe result for the download filesystem (`using locked rename fallback` is informational, for example on 9p). `plugin-schedules: plugin initialization reported failed requirements plugins=retrackers` means that retrackers did not start; while the report lists recovery markers, it is the `docker logs` sign of the `receipt-ledger-corrupt` refusal above, so clear them as described. A `Cannot attest` refusal, `does not support rename-noreplace` or repeated `native-capture-retry-pending` messages need investigation.

   To apply the configured retrackers to torrents already loaded before this upgrade, review the dry run, then enqueue the reviewed candidates. The tool changes no payload and uses the normal native queue. The tool follows the configured private-torrent policy: private torrents are excluded when `dontAddPrivate` is enabled. Torrents already carrying all configured retrackers and active file operations are excluded; `--limit=50` is the default. Select a smaller batch or explicit hashes when needed:

   ```sh
   docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/retrackers-apply-existing.php
   docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/retrackers-apply-existing.php --apply --limit=50
   ```

8. Once a rollback is no longer likely, attest the erasedata queue as described in “Attesting the erasedata queue”. Keep the old container and the volume backup until the new profile has been checked.

Erasedata manifests carrying `path_bytes: "daemon"` contain raw path bytes; older collectors cannot decode this format and retain the job as incomplete. Drain admitted deletion jobs with the new image before rollback, checking that no pending, temporary, list or journal obligations remain. If a job cannot finish, preserve the whole queue, profile, session and payload, and return to the new image for recovery. Never strip the format marker or delete queue records to force progress. Datadir version 3 journals likewise require the new image; finish or cancel those operations before switching.

To roll back, remove the new container, give the old one back the restart policy recorded in step 3 (`RestartPolicy`; the example restores `unless-stopped`) and start it:

```bat
docker stop --timeout 60 rutorrent
docker rm rutorrent
docker rename rutorrent-old rutorrent
docker update --restart unless-stopped rutorrent
docker start rutorrent
```

To upgrade again after a rollback, repeat steps 1–7. In step 1 use the old image's `tasks/` tool when it has one; otherwise clear the markers in the new container as in step 7. If the protected queue was attested before, rerun the attestation while both containers are stopped and before step 6, because old startup can change the witness owner to the service UID.

### Repairing an existing profile or changing UID/GID

Stop every container using these mounts first. Inspect trusted storage before changing it; POSIX ownership and modes must persist on the backing filesystem. The effective session and default download paths must be literal paths below `/data/`. The offline permissions command below repairs in one pass every path that startup or the erasedata queue admits: `/config`, `/config/rutorrent`, `/config/rtorrent` and its rc file, `/config/custom_plugins`, `/config/custom_themes`, all directories under `conf` and `share` (including `conf/users/*` and `share/users/*/settings`) and the files under `share`, `/config/nginx` (`HTTP_AUTH=true`), `/config/filebot` (FileBot), legacy and protected erasedata queues and datadir journals, including per-user profiles (directories 0700, files 0600), `/data`, the session directory with its records and private journals, download directories, and erasedata capture workspaces below `/data` (`.erasedata-entry-*`, `.erasedata-rmdir-*` and `.erasedata-recovery-*` directories 0700, with their own `.initialized` and `.name` files 0600; the captured data inside keeps its modes). It only sets modes: it never changes owners or the erasedata witness. Keep authentication ancestors root-owned and the empty first-use witness `root:root 0400`.

Pass the application container's `UID` and `CHECK_PERM_DATA` to the command. With `CHECK_PERM_DATA=true`, every download directory loses group/other write and startup then assigns it to `UID:GID`. With `CHECK_PERM_DATA=false`, startup does not take ownership of `/data`, so only the download root and directories already owned by `UID` lose group/other write; directories of another owner, such as those created from Windows, keep their modes and stay writable for the service.

At and below the configured top directory (`$topDirectory`, the `DOWNLOAD_DIRECTORY` root, `/data/downloads/` by default), datadir and every AutoTools operation (Move, Copy, HardLink and SoftLink) accept group/other-writable folders, such as those made later in Windows Explorer, when root or the service UID owns them; they need no new permissions repair. A foreign owner, or a path that leaves the tree through a symlink, is refused. Above that directory, including `/data` itself, the strict rule remains: no group/other write unless the sticky bit is set. Changing `$topDirectory` in `config.local.php` moves this boundary together with the folders offered in the WebUI; a top that resolves to `/` is not a boundary, so the strict rule then applies everywhere. On a daemon with native file-job support, AutoTools Move enters a durable queue. The native dispatcher waits for retrackers or another file operation to release the torrent, then grants the Move worker its stop claim. Retrackers and Move share eight worker slots plus one dispatcher, with at most two Move workers. The worker preserves the torrent's original run state, including a later Stop; interrupted work resumes from its payload journal. A waiting job survives a daemon restart.

On the current native daemon, a successful re-add of the same torrent permanently cancels its previous deferred permission to delete data. While a deletion worker or one of its filesystem helpers is still operating, adding that hash returns a visible busy refusal; retry after the operation finishes. Removal jobs created before this native permission format are retained for manual review and cannot authorize a new deletion automatically. Stop the previous container before upgrading: already running workers cannot acquire the new protection through an application hot-sync.

Unavailable RPC, inaccessible paths and uncertain recovery state retain the exact removal record and any remaining payload or recovery data. A lost completion reply retains metadata for reconciliation even after payload removal. The application log names the job and classified cause. Unchanged obstacles use a bounded retry delay of up to 60 seconds and at most eight worker launches per collection pass; repairing the cause allows a later pass to continue. Unknown or corrupt authority requires inspection of the named record. Keep pending manifests and protected recovery directories together; deleting a retry hint does not cancel an operation or authorize removal.

Stock rTorrent 0.9.8 retains its previous Remove with data behavior. It cannot enforce this re-add protection: a remaining deferred deletion followed by re-adding the same hash and a later ordinary Remove can still delete that torrent's data. This is an explicitly retained compatibility limitation; do not re-add that hash while its old deletion remains unresolved. Ordinary Remove remains available on both versions.

For the Windows Docker Desktop layout, execute from `cmd.exe`, using the installation's actual mounts. Do not use PowerShell: it expands `$d` inside the double-quoted script to an empty string, so the permissions command walks the whole container filesystem instead of the listed paths. Inspect links first; a link or unexpected owner requires manual review, not this permissions pass:

```bat
docker run --rm --stop-timeout 60 --network none -v D:/ruTorrent/config:/config -v D:/ruTorrent/data:/data -v D:/Downloads:/data/downloads alpine:3.24.2 sh -c "find /config /data/.session /data/downloads -type l -print"
```

Then repair the inspected layout:

```bat
docker run --rm --stop-timeout 60 --network none -e UID=991 -e CHECK_PERM_DATA=true -v D:/ruTorrent/config:/config -v D:/ruTorrent/data:/data -v D:/Downloads:/data/downloads alpine:3.24.2 sh -c "chmod 0755 /config /config/rutorrent /data && for d in /config/rtorrent /config/custom_plugins /config/custom_themes /config/rutorrent/conf /config/rutorrent/share /config/nginx /config/filebot; do if [ -d $d ]; then find $d -type d -exec chmod go-w {} +; fi; done && for d in /config/rtorrent/.rtorrent.rc /config/rutorrent/share /config/nginx /config/filebot; do if [ -e $d ]; then find $d -type f -exec chmod go-w {} +; fi; done && if [ -d /config/rutorrent/share ]; then find /config/rutorrent/share -type d \( -name erasedata -o -name datadir-jobs -o -path \*/datadir-jobs/\* \) -exec chmod 0700 {} + && find /config/rutorrent/share -type f \( -path \*/erasedata/\* -o -path \*/datadir-jobs/\* \) -exec chmod 0600 {} +; fi && if [ -d /config/rutorrent/erasedata-jobs ]; then find /config/rutorrent/erasedata-jobs -type d -exec chmod 0700 {} + && find /config/rutorrent/erasedata-jobs -type f -exec chmod 0600 {} +; fi && if [ -d /data/.session ]; then chmod 0700 /data/.session && find /data/.session -type f -exec chmod go-w {} + && find /data/.session -mindepth 1 -maxdepth 1 -type d -name .\* -exec chmod 0700 {} + && find /data/.session -mindepth 2 -type f -exec chmod 0600 {} +; fi && if [ -d /data/downloads ]; then if [ x$CHECK_PERM_DATA = xfalse ]; then chmod go-w /data/downloads && find /data/downloads -mindepth 1 -type d -user ${UID:-991} -exec chmod go-w {} +; else find /data/downloads -type d -exec chmod go-w {} +; fi; fi && find /data -type d \( -name .erasedata-entry-\* -o -name .erasedata-rmdir-\* -o -name .erasedata-recovery-\* \) -exec chmod 0700 {} + && find /data -type f \( -name .initialized -o -name .name \) -path \*/.erasedata-\*/\* -not -path \*/.erasedata-\*/\*/\* -exec chmod 0600 {} + && for d in /config /config/rutorrent /config/rutorrent/conf /config/rutorrent/share /config/rutorrent/share/settings /config/rtorrent /data /data/.session /data/downloads; do if [ -e $d ]; then ls -ldn $d; fi; done && for d in /config/custom_plugins /config/custom_themes /config/rutorrent/conf/users /config/rutorrent/share/users /config/rutorrent/erasedata-jobs /config/nginx /config/filebot; do if [ -d $d ]; then find $d -maxdepth 2 -type d -exec ls -ldn {} +; fi; done"
```

On a Linux host, for example with `UID:GID=1000:1000`, first inspect the exact paths with `ls -ldn`. Root or the selected UID may own them; files of another or a previous UID need the UID change command below. Then run the same shell body with POSIX quoting:

```sh
docker run --rm --stop-timeout 60 --network none -e UID=1000 -e CHECK_PERM_DATA=true \
  -v /mnt/docker/rutorrent/config:/config -v /mnt/docker/rutorrent/data:/data \
  alpine:3.24.2 sh -c 'chmod 0755 /config /config/rutorrent /data && for d in /config/rtorrent /config/custom_plugins /config/custom_themes /config/rutorrent/conf /config/rutorrent/share /config/nginx /config/filebot; do if [ -d $d ]; then find $d -type d -exec chmod go-w {} +; fi; done && for d in /config/rtorrent/.rtorrent.rc /config/rutorrent/share /config/nginx /config/filebot; do if [ -e $d ]; then find $d -type f -exec chmod go-w {} +; fi; done && if [ -d /config/rutorrent/share ]; then find /config/rutorrent/share -type d \( -name erasedata -o -name datadir-jobs -o -path \*/datadir-jobs/\* \) -exec chmod 0700 {} + && find /config/rutorrent/share -type f \( -path \*/erasedata/\* -o -path \*/datadir-jobs/\* \) -exec chmod 0600 {} +; fi && if [ -d /config/rutorrent/erasedata-jobs ]; then find /config/rutorrent/erasedata-jobs -type d -exec chmod 0700 {} + && find /config/rutorrent/erasedata-jobs -type f -exec chmod 0600 {} +; fi && if [ -d /data/.session ]; then chmod 0700 /data/.session && find /data/.session -type f -exec chmod go-w {} + && find /data/.session -mindepth 1 -maxdepth 1 -type d -name .\* -exec chmod 0700 {} + && find /data/.session -mindepth 2 -type f -exec chmod 0600 {} +; fi && if [ -d /data/downloads ]; then if [ x$CHECK_PERM_DATA = xfalse ]; then chmod go-w /data/downloads && find /data/downloads -mindepth 1 -type d -user ${UID:-991} -exec chmod go-w {} +; else find /data/downloads -type d -exec chmod go-w {} +; fi; fi && find /data -type d \( -name .erasedata-entry-\* -o -name .erasedata-rmdir-\* -o -name .erasedata-recovery-\* \) -exec chmod 0700 {} + && find /data -type f \( -name .initialized -o -name .name \) -path \*/.erasedata-\*/\* -not -path \*/.erasedata-\*/\*/\* -exec chmod 0600 {} + && for d in /config /config/rutorrent /config/rutorrent/conf /config/rutorrent/share /config/rutorrent/share/settings /config/rtorrent /data /data/.session /data/downloads; do if [ -e $d ]; then ls -ldn $d; fi; done && for d in /config/custom_plugins /config/custom_themes /config/rutorrent/conf/users /config/rutorrent/share/users /config/rutorrent/erasedata-jobs /config/nginx /config/filebot; do if [ -d $d ]; then find $d -maxdepth 2 -type d -exec ls -ldn {} +; fi; done'
```

The command sets the session directory to `0700`, which also clears setgid. For a customized session path, repair that path and inspect all its ancestors. Private journal directories need mode 0700, their regular records 0600, the selected UID or root as owner, and no hard links or symlinks. Exclude root-protected authentication paths and the witness when changing ordinary data ownership: `.erasedata-first-use` must remain root:root 0400, empty and single-linked. The permissions command never changes the witness; attestation restores it after validating the queues. Read-only mounted working files must already have their required identity (password file: `root:GID 0640`) because startup cannot change their owners.

After restoring a backup without Linux metadata (every entry `0777`, as a Windows copy can produce), run the permissions command and then the attestation command before starting: startup refuses the restored witness until attestation recreates it. An interrupted erasedata capture is also bound to the inode numbers of its workspace, so after a copying restore erasedata may keep it for manual review (`private root retained: unsigned or changed`) instead of resuming it; the repaired modes keep its data private meanwhile.

A copy onto Docker Desktop `drvfs` volumes mounted with `uid=0;gid=0` loses the owners as well: every entry becomes `root:root`, and startup refuses the session with `root or foreign-owned ordinary session data`. After inspecting the restored tree, first give the root-owned entries back to the service identity with this root-owner command, then run the permissions and attestation commands. It leaves the authentication tree, which startup keeps `root:GID`, and the witness out of the pass by their exact paths, and gives `/config` itself back to root:

```bat
docker run --rm --stop-timeout 60 --network none --entrypoint sh -v D:/ruTorrent/config:/config -v D:/ruTorrent/data:/data -v D:/Downloads:/data/downloads alpine:3.24.2 -ec "find /config /data -user 0 -not -path /config/nginx -not -path /config/nginx/\* -not -path /config/rutorrent/.erasedata-first-use -exec chown -h 991:991 {} +; chown 0:991 /config; if [ -d /config/nginx ]; then find /config/nginx -exec chown -h 0:991 {} +; fi"
```

On a Linux host, run the same body with POSIX quoting and your identity, for example `1000:1000`:

```sh
docker run --rm --stop-timeout 60 --network none --entrypoint sh \
  -v /mnt/docker/rutorrent/config:/config -v /mnt/docker/rutorrent/data:/data \
  alpine:3.24.2 -ec 'find /config /data -user 0 -not -path /config/nginx -not -path /config/nginx/\* -not -path /config/rutorrent/.erasedata-first-use -exec chown -h 1000:1000 {} +; chown 0:1000 /config; if [ -d /config/nginx ]; then find /config/nginx -exec chown -h 0:1000 {} +; fi'
```

It changes every other root-owned entry below `/config` and `/data`. Use it only after such a restore, and narrow its paths when root must keep other data there, for example with `CHECK_PERM_DATA=false`.

When intentionally changing `991:991` to `1000:1000`, change only entries of the old UID, including existing protected erasedata queue profiles. This is an offline operation over inspected trusted paths; stop first and reject or remove unexpected symlink ancestors. Keep the witness and authentication tree out of the pass; if an older image left the witness owned by the old UID, attest with that old identity before the change:

```sh
docker run --rm --stop-timeout 60 --network none --entrypoint sh \
  -v /mnt/docker/rutorrent/config:/config -v /mnt/docker/rutorrent/data:/data \
  alpine:3.24.2 -ec 'find /config /data -user 991 -not -path /config/nginx -not -path "/config/nginx/*" -not -path /config/rutorrent/.erasedata-first-use -exec chown -h 1000:1000 {} +; chown 0:1000 /config; if [ -d /config/nginx ]; then find /config/nginx -exec chown -h 0:1000 {} +; fi'
```

Then use `UID=1000 GID=1000` in the application container and inspect all repaired paths before starting. After a restore that left the entries owned by root, use the root-owner command above instead; inspect root-owned session records before giving them to the daemon.

### Attesting the erasedata queue

To migrate empty legacy erasedata queues, first inspect `/config/rutorrent/share/settings/erasedata` and each `/config/rutorrent/share/users/*/settings/erasedata`. Pending `.list`, `.pending`, protected jobs or unexpected entries need manual review; the command refuses nonempty queues and never executes their contents. Legacy settings parents must have no group/world write, queue directories no group/world access, and private key files no group/world access; the offline permissions command sets these modes. After that inspection, run a disposable offline container with the same `/config` volume and numeric identity while every container using it is stopped. For the Windows layout and `991:991`:

```bat
docker run --rm --stop-timeout 60 --name rutorrent-queue-maintenance --entrypoint attest-erasedata-queue -e UID=991 -e GID=991 -v D:/ruTorrent/config:/config ivanshift/rutorrent:latest --attest-clean-legacy-queue
```

On a Linux host:

```sh
docker stop --timeout 60 rutorrent
docker run --rm --stop-timeout 60 --name rutorrent-queue-maintenance \
  --entrypoint attest-erasedata-queue \
  -e UID=1000 -e GID=1000 \
  -v /mnt/docker/rutorrent/config:/config \
  ivanshift/rutorrent:latest --attest-clean-legacy-queue
docker start rutorrent
```

Use the actual identity, mount and image of your installation. The command retires only idle old queues after validating their empty lock files (`scheduler.lock`, `.drain-state.lock`, `.drain-worker.lock`, `<HASH>.lock`), disarmed `.drain-state` with an empty journal, and optional matching `.private-root-key`/`.private-root-key-state`; protected queue controls are retained during witness repair. It creates `erasedata-jobs` as UID:GID 0700 and recreates the witness as root:root 0400 when it is absent or an empty single-link regular file owned by root or the selected UID, whatever its group or permission bits; startup names this command for exactly those witness defects. A witness of another type or owner, with content or with extra links needs inspection: both startup and the command then ask to remove that entry while stopped, only when it and the queues are trusted, and to rerun the command. It refuses links, foreign ownership, active drain state, pending jobs and unrecognized control files. If it refuses, inspect its named path and preserve pending jobs; restarting or disabling `CHECK_PERM_DATA` does not attest them. Do not substitute an unconditional `chown` or delete pending jobs.

### Retracker settings admission

Saving retracker additions trims surrounding ASCII whitespace and accepts only lowercase `http://`, `https://` or `udp://` schemes. Invalid settings refuse the entire change with a classified field or tier/entry reason; previous settings and the running profile remain intact. Correct an unsupported or noncanonical addition left in older stored settings before retrying that job. Comparisons with existing metainfo use the daemon loader's whitespace rules while preserving the original torrent bytes and tiers.

### Resetting an exhausted retrackers job

With the native retrackers queue, the first deferral of a job records attempt 1 of 6, also when the job only waits. A public torrent that is paused, still hashing or briefly unreachable over SCGI waits and is tried again later; a wait keeps the count. Every refused capture or preflight adds one attempt, and the sixth makes the job terminal: after six refusals, or five when the job first waited. A worker that dies after capturing the torrent counts as a refused capture: rTorrent rolls the capture back and the job waits before its next attempt, so a torrent whose worker always fails ends terminal instead of being stopped and started again and again. The torrent keeps working without the added retrackers. Whenever no tracker message is shown, its message reads `retrackers: retries exhausted; …` until the job is reset. PHP preflight refusals that exhaust retries use `queue-retry-exhausted` in the application log (`/tmp/errors.log`, unless `RU_LOG_FILE` or `$log_file` names another file). Terminal refusals after a native crash or recovery use the rTorrent error log and persistent torrent message. The WebUI terminal notice supplies the reset command for either path in the `docker exec -u torrent <container> /usr/bin/php85 /rutorrent/app/tools/retrackers-retry-reset.php …` form; replace `<container>` with your container name, as below. After fixing the cause, list the terminal jobs, review one with a dry run, then reset it:

```sh
docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-retry-reset.php
docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-retry-reset.php <HASH>
docker exec -u torrent rutorrent php85 /rutorrent/app/tools/retrackers-retry-reset.php <HASH> --apply
```

The queue belongs to the ruTorrent profile, not to the HTTP login. With the image's default shared profile (`RU_FORBID_USER_SETTINGS` unset or `true`), every login uses one unnamed profile: run the tool without `--user`. A `--user=<login>` given anyway selects the same shared profile, and the tool says so. Only with `RU_FORBID_USER_SETTINGS=false` does `--user=<login>` name that login's own profile; the log line and the WebUI notice then include it. The dispatcher picks up a reset job within seconds. Removing and re-adding the torrent also queues it again.

### AutoTools Move queue and recovery

Move jobs waiting for source readiness or a transport reply retry without consuming a failure attempt. Preflight, storage and worker-exit failures count toward six attempts; exhausted jobs stay visible in the torrent message and WebUI. Repair the named cause, list jobs, review the selected hash, then reset it:

```sh
docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/autotools-move-reset.php
docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/autotools-move-reset.php <HASH>
docker exec -u torrent rutorrent /usr/bin/php85 /rutorrent/app/tools/autotools-move-reset.php <HASH> --apply
```

A held stop claim or ambiguous payload journal needs recovery first; a reset does not discard ownership or authorize repeating a rename or notification. Use the Set directory dialog only after the existing operation has released its claim. Keep the queue and payload journal together during backup.

Without native file-job support, including rTorrent 0.9.8, AutoTools Move uses upstream mode. The WebUI warns that it runs synchronously and has no crash recovery or exclusion against retrackers reloads. A cross-filesystem copy can block WebUI/RPC for its duration; interruption can leave data split between folders. Copy, HardLink and SoftLink retain their existing behavior. An unknown capability reply pauses Move visibly until support can be verified.

### Values damaged by the old string escaping

Before this release, ruTorrent read every string from rTorrent with each `"` turned into `\"` and each `\` into `\\`. Code that wrote such a value back stored the damage in rTorrent: a label saved again (AutoTools `{DIR}`, an edit, a rutracker_check replacement) can hold `%5C%22` or `%5C%5C`, and a directory set from a read value (a rutracker_check replacement, a datadir or AutoTools move, the datadir dialog) can name a folder with a literal `\"` or a doubled `\\` that you never chose. The update reads and writes the daemon's own bytes from now on, but it does not rewrite stored values. To inspect candidates, run the read-only scan; it prints one classified line per finding and changes nothing:

```sh
docker exec -u torrent rutorrent php85 /rutorrent/app/tools/escaped-values-scan.php
```

Every finding is a candidate for inspection, not proof of corruption or duplicated payload. Literal quotes and backslashes can be legitimate names, and an unescaped-looking neighbor may belong to another torrent. `escaped-path-both-exist` only says both paths exist; `escaped-path-legitimate-or-escaped` deliberately leaves the origin undecided. Check the torrent metadata and actual files before changing a label or directory. The tool never repairs, renames or deletes them.

The scan also lists unreferenced empty directories with escaping-like names immediately in or beside each torrent directory as `escaped-empty-directory`. It does not recurse or follow symlinks, so this is not a complete inventory of old empty folders across the download tree. Exit status `0` means the read-only scan completed, including when candidates were found; add `--fail-on-candidates` to return `1` for candidates in an explicit audit. Status `2` means a usage error or an unreadable daemon. Use `--user=NAME` for an individual ruTorrent profile when per-user settings are enabled.

### Shutdown and health

On every start, migration appends `method.set_key = event.system.shutdown,docker_rutorrent_early_save,"session.save="` to the persisted rc when no equivalent shutdown save hook is present; the image template carries no copy, so a new rc receives it the same way. Existing shutdown commands and their keys are preserved. To opt out, comment that line out: a commented image hook is kept as it is and not added again. The hook saves before stopped announces can consume the deadline; the 60-second Docker timeout still permits orderly tracker shutdown. The image's patched 0.16.25 daemon also saves natively during shutdown; it reuses the saves the hook has already queued, so each torrent's session files are built and written once. The hook is kept because a rollback to an older image (0.16.24 or legacy 0.9.8) relies on it: without it those daemons may stop without saving changes made shortly before a slow shutdown. Legacy compatibility of the hook is checked on 0.9.8.

The PID 1 service wrapper isolates the s6 supervisor session, reaps orphaned children and translates Docker termination or terminal Ctrl-C into directed shutdown. Use `docker stop --timeout 60` or `docker restart --timeout 60`; rTorrent receives SIGINT for normal shutdown, with a 55-second service deadline. `s6-svc -r /etc/s6.d/<service>` restarts that service cleanly. Helper-service crashes are logged and restarted locally; an unexpected nginx, PHP-FPM or rTorrent crash still exits the stack with status 1.

Docker health status is diagnostic; Docker Engine does not restart a merely unhealthy container. The watchdog writes `/run/rutorrent-watchdog/.service-health.json` and emits persistent-failure diagnostics after the configured startup allowance and consecutive failures. It resumes only a positively identified stopped rTorrent process through a pidfd. Busy or SCGI-unresponsive processes are preserved for inspection, since long synchronous file work can resemble a deadlock. Automatic deadlock reset requires a separate operational policy. Watchdog metadata lives in a protected root-owned runtime directory and does not depend on HTTP authentication or `/config/nginx`; keep root-squashed authentication updates offline. A watchdog lock/publication refusal is logged and leaves the daemon stack alive. On Windows, use a current Docker Desktop release that honors stop timeouts (the reviewed minimum was 4.86).

## Image internals

- Docker-owned ownership and service-supervision helpers are compiled independently of the selected ruTorrent native-helper manifest.
- ruTorrent and third-party plugin sources have dedicated cacheable stages; native tools and MediaInfo also download verified sources in their respective builder stages.
- ruTorrent and third-party plugins are fetched by explicit refs; plugin defaults resolve to exact commits.
- The GeoIP database is selected by release version and guarded by a mandatory SHA256 checksum.
- rTorrent and libtorrent are compiled with optional `-Werror` controls (`STRICT_WERROR` arg).
- FileBot is optional; its verified archive is downloaded in the final stage when `FILEBOT=true`, with three retries, a 15-second connection limit, a 180-second transfer limit and a 600-second retry budget. Startup validates a trusted single-linked regular `/config/filebot/postdl` hook and attempts to restore its owner-executable bit as the selected UID, warning if that fails. The public `postdl` link always targets the immutable dispatcher, which runs an executable custom hook or the packaged default; removing the custom hook restores the default automatically.
- Runtime image stays small: only runtime packages and healthcheck dependencies are installed.
- The `nginx` command wrapper runs root-issued diagnostics and reloads as the selected service identity; this keeps its temp directories writable by the daemon.
- Healthcheck runs every 60 seconds, has a 10-minute initialization period, and verifies the rTorrent process/socket plus nginx and PHP through the loopback `/healthz` endpoint. Each probe has a bounded timeout.

## License

The build scripts in this repository use the [MIT License](LICENSE). Software bundled in the image, including ruTorrent, rTorrent, libtorrent, FFmpeg and UnRAR, keeps its own licenses. Component license texts and source notices are retained in the image; the MIT license does not cover those components.
