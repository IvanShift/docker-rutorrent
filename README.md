# IvanShift/rutorrent

Opinionated ruTorrent + rTorrent container image with a focus on controlled source builds, small footprint, and a prepared ruTorrent fork.

## Features

- Multi-arch image (`linux/amd64`, `linux/arm64`) built on Alpine Linux 3.24.2
- PHP 8.5 with the IvanShift/ruTorrent fork selected by `RUTORRENT_REF`, the rTorrent fork based on 0.16.24, libtorrent 0.16.24, Alpine-provided c-ares, and UnRAR 7.23 built from RARLab's `unrarsrc-7.2.7.tar.gz`
- rTorrent uses the tinyxml2 XML-RPC backend for faster ruTorrent plugin calls
- MediaInfo/libmediainfo 26.05, built as local APKs against Alpine 3.24 with verified source checksums
- Non-root runtime (`UID` / `GID` configurable), healthcheck-ready, and persistent volumes
- Automatic size-based rotation for nginx access/error logs; other application logs require separate monitoring
- Optional FileBot integration (portable 5.3.0) with OpenJDK 21 and chromaprint
- Supply-chain aware build: ruTorrent fetched by an explicit ref, plugins pinned to exact default commits, and a checksum-verified GeoIP database
- Custom plugin/theme directories in persistent `/config` storage
- Image additions: custom forked `rutracker_check` behavior for RuTracker/NNMClub plus build-time fetched `geoip2` and `ratiocolor` plugins

## Tags

- `latest` – standard image built by CI from the exact ruTorrent and rTorrent source commits configured in `Dockerfile`
- `filebot` – the same source inputs with `FILEBOT=true`
- `rtorrent-<major.minor.patch>` / `filebot-rtorrent-<major.minor.patch>` – moving version tags
- `rtorrent-<version>-YYYY.MM.DD-run<run_id>-attempt<run_attempt>` and the corresponding `filebot-` tag – a tested CI snapshot

Use the image digest (`ivanshift/rutorrent@sha256:…`) to retain an exact image. The tag version describes the fork's upstream base; native patches are identified by the source revision labels.

## Build

### Build arguments

| Argument | Description | Default |
|----------|-------------|---------|
| `ALPINE_VERSION` | Alpine base image tag | `3.24.2` |
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
| `RUTORRENT_REF` | ruTorrent fork ref fetched and checked out detached | `d108dddd286cd5eca9173b91b2cdee64adb27e6a` |
| `RUTORRENT_COMMIT_SHA` | Expected full commit; provide it separately for a mutable ref | `${RUTORRENT_REF}` |
| `GEOIP2_REPO` | GeoIP2 plugin repository URL | `https://github.com/Micdu70/geoip2-rutorrent.git` |
| `GEOIP2_REF` | GeoIP2 branch, tag, full ref, or commit | `cad8a11b47f02ff75358b7bd9c4137648f5fedd0` |
| `RATIOCOLOR_REPO` | RatioColor plugin repository URL | `https://github.com/Micdu70/rutorrent-ratiocolor.git` |
| `RATIOCOLOR_REF` | RatioColor branch, tag, full ref, or commit | `4aec1988be1e09b44799b71ed4a25751c695a6f2` |
| `GEOIP2_DB_VERSION` | P3TERX GeoLite2 release containing `GeoLite2-Country.mmdb` | `2026.09.28` |
| `GEOIP2_DB_SHA256` | Expected SHA256 of `GeoLite2-Country.mmdb` | `a4c816daf2837559ce2a6470ae59e285ac4666e4535315b4b22adbbf66ef229c` |
| `GEOIP2_DB_URL` | Optional controlled database mirror; SHA256 validation still applies | _(empty)_ |
| `LIBTORRENT_BRANCH` | libtorrent release tag used for source checkout | `v0.16.24` |
| `LIBTORRENT_VERSION` | Expected resolved libtorrent source commit | `90248b01da400b2d89b5caa625e9bdc143fe7e86` |
| `RTORRENT_BRANCH` | CI release label; `v0.9.8` also enables C++17, xmlrpc-c and the legacy runtime marker. Does not select source | `v0.16.24` |
| `RTORRENT_REPO` | rTorrent source repository URL | `https://github.com/IvanShift/rtorrent.git` |
| `RTORRENT_REF` | Optional rTorrent ref; an empty value selects `RTORRENT_VERSION` | _(empty)_ |
| `RTORRENT_VERSION` | Full SHA that the selected `RTORRENT_REF` must resolve to | `7cda3832da795c7ee5e2a0d6bd0438191d2fac1c` |
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

Set the source repository and ref together with the full expected commit. The build stops before compilation if the fetched commit differs. Keep `RTORRENT_BRANCH=v0.16.24` for this current-master native build: it labels CI tags and keeps the C++20/tinyxml2 build path. It does not select the source. Setting it to `v0.9.8` activates the old C++17/xmlrpc-c build path and `/etc/rtorrent-legacy` runtime marker; use that setting only with matching legacy source and dependencies. The image records the native commit in `io.github.ivanshift.rtorrent.revision` and the ruTorrent commit in `io.github.ivanshift.rutorrent.revision`; `/usr/share/build-info/rutorrent-revision` contains the resolved ruTorrent commit. Inspect both labels with `docker image inspect --format '{{json .Config.Labels}}' <image>`.

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

The build validates source identities rather than trusting names alone: the rTorrent repository/ref and libtorrent branch must resolve to their corresponding expected commits, as must the mktorrent/dump-torrent tags. The default ruTorrent and plugin refs are exact commits; the GeoLite2, UnRAR, and optional FileBot downloads must match their configured SHA256 values. Plugin stages also verify their required entry files, and the image verifies a GeoLite2 lookup after installation.

For reproducible source inputs, use exact commits and matching checksums. The `GEOIP2_COMMIT_SHA` and `RATIOCOLOR_COMMIT_SHA` arguments provide independent expected source identities for mutable refs; the build rejects mutable refs without a full expected SHA. Docker cache does not detect when a remote branch or tag moves, so rebuild the corresponding `geoip2-source` or `ratiocolor-source` stage without cache when deliberately using a mutable ref. These protections do not guarantee byte-identical images: the Alpine base tag, package repositories (including `apk upgrade`), and builder environment can change over time. Pin or mirror those external inputs too when full image reproducibility is required.

MediaInfo is packaged locally because Alpine 3.24 provides the older 26.01 release. The `mediainfo-builder` stage uses the recipes in `packaging/`, verifies both upstream source archives, and builds matching CLI/library APKs for the target architecture. The final image installs these APKs through `apk`, so their files and shared-library dependencies remain managed by the package manager. Its remaining dependencies come from the stable Alpine repositories; no edge repository is enabled. The build generates a temporary APK signing key: only public keys are mounted for installation, and the private key stays in the build stage. Updating MediaInfo requires changing its version and both source checksums together.

## Runtime configuration

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` / `GID` | User/group IDs used by rTorrent & services | `991` |
| `PORT_RTORRENT` | TCP listening port for rTorrent; decimal 1–65535 | `45000` |
| `MODE_DHT` | DHT mode (`off`, `on`, `auto`) | `off` |
| `PORT_DHT` | UDP DHT port; decimal 1–65535 | `6881` |
| `PEER_EXCHANGE` | Enable PEX (`yes` / `no`) | `no` |
| `DOWNLOAD_DIRECTORY` | Main downloads directory | `/data/downloads` |
| `CHECK_PERM_DATA` | Recursively assign ordinary `/data` working files to `UID:GID`; exactly `true` or `false`. Trust checks still apply | `true` |
| `HTTP_AUTH` | Enable HTTP authentication; exactly `true` or `false` | `true` |
| `ALLOW_UNAUTHENTICATED_HTTP` | Explicit opt-out required with `HTTP_AUTH=false`; use only on a trusted, isolated network | `false` |
| `ENABLE_RPC2` | Enable the rTorrent XML-RPC endpoint at `/RPC2`; must be exactly `true` or `false` | `false` |

`PORT_RTORRENT`, `MODE_DHT`, `PORT_DHT`, `PEER_EXCHANGE` and `DOWNLOAD_DIRECTORY` seed the image-provided `.rtorrent.rc` when `/config/rtorrent/.rtorrent.rc` is first created. After that, edit the persisted file to change these values. Startup reads its effective `directory.default.set` and aligns ruTorrent with it, warning if `DOWNLOAD_DIRECTORY` differs. The directory must be an absolute literal path; control characters, traversal and unsupported quoting are refused.

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
- `/data/.session/.rutorrent-stop-intents`, `.retrackers-migration`, `.autotools-file-jobs` – private native/plugin journals; the session location follows `session.path.set`

### Persistent configuration and migration

`/rutorrent/conf-dist` is the pristine configuration shipped by the selected fork. Startup restores missing files into `/config/rutorrent/conf` and preserves existing files; removing a file intentionally does not keep it absent. Existing `xmlrpc_proxy.php` is preserved, while new volumes use the image policy until the file is explicitly installed. Compare an operator file against the image copy with:

```sh
docker exec rutorrent diff /rutorrent/conf-dist/plugins.ini /config/rutorrent/conf/plugins.ini
```

The preserved `config.php` is then migrated on every start. The image sets `$topDirectory` from the persisted download directory, uses the internal SCGI socket by default, updates `/RPC` in `$XMLRPCMountPoint`, and fills missing/default paths for PHP, curl, ffmpeg, unrar, sox and mediainfo. Custom executable wrappers and PHP expressions are preserved. The removed `dumptorrent` setting is not added. `RU_SCGI_HOST` and `RU_SCGI_PORT` override only PHP's connection settings; they do not move the daemon, scheduler or healthcheck from `/run/rtorrent/rtorrent.sock`, so use them only with a deliberately coordinated runtime configuration.

Use `/config/rutorrent/conf/config.local.php` for supported operator settings such as a narrower `$topDirectory`. Image policy enforces a single shared profile and CSRF checks after that include: `$forbidUserSettings=true` and `$enableCSRFCheck=true` cannot be disabled there. Keep the PHP file valid; a parse/lint failure aborts startup without modifying its original contents.

`update-config` also migrates old rTorrent command names and removes deprecated no-op settings. Its native and legacy rules differ so that 0.9.8 limits remain valid; native-only image defaults are retained as reversible comments when switching modes. Keep `config.php` and `.rtorrent.rc` as regular readable files. Writable directory mounts allow atomic file replacement with owner and permissions retained. A writable single-file bind mount is updated in place when rename is unavailable; ordinary shutdown signals are deferred until its validated write or rollback is complete. SIGKILL and power loss cannot make that fallback atomic; unchanged canonical files also work in read-only directories. A read-only file that needs a change or a symlink in place of either file is refused with a clear diagnostic. Back up operator configuration before changing image versions.

The image provides `/usr/local/bin/mktorrent`. Updated fork sources select it automatically when it is executable, setting both `$useExternal = 'mktorrent';` and its absolute `$pathToCreatetorrent`; otherwise they use the internal builder. `$useInternalHybrid=true` retains the internal builder for hybrid torrents. Older pinned fork sources still default to the internal builder. To change either choice, copy the plugin's complete `conf.php` from the image and mount the configured copy read-only at `/rutorrent/app/plugins/create/conf.php`. Set `$useExternal = false;` for the internal builder, or configure another supported creator and its full path. Keep the remaining settings and make the file readable by the chosen UID.

### Fork Changes

The Docker build fetches the prepared ruTorrent fork by `RUTORRENT_REF` and checks its resolved commit against `RUTORRENT_COMMIT_SHA`. The default repository and exact source identity are listed in the build-argument table. It no longer copies `overrides/rutorrent` over the downloaded tree and no longer applies `sed` patches to ruTorrent files. Third-party plugins are fetched in independent source stages at exact default commits, then copied into the cleaned runtime tree without VCS metadata.

#### `rutracker_check`

A heavily modified tracker checker with stability and functionality improvements:

- **Smart File Cleanup**: Automatically removes obsolete files (renamed or deleted in the new torrent) after an update.
- **Recursive Folder Cleanup**: Removes empty directories left behind after file cleanup.
- **Improved URL Detection**: Prioritizes comment URLs over announce URLs (fixes RuTracker topic detection).
- **Critical Bug Fixes**: Fixed ratio group visibility (`rat_Array` bug) and label initialization.
- **Anti-Bot Protection**: Uses a modern Chrome User-Agent to reduce 403 errors.
- **NNMClub Auto-Check Restored** (`trackers/nnmclub.php`): Automatic torrent update checking for NNMClub is fully functional again despite Cloudflare Turnstile protection on the website.

#### Build-Time Plugins

- **`geoip2`**: Fetched from `GEOIP2_REPO` at `GEOIP2_REF`. Its bundled 2023 database is replaced with checksum-verified `GeoLite2-Country.mmdb` from the versioned [P3TERX/GeoLite.mmdb](https://github.com/P3TERX/GeoLite.mmdb) release.
- **`ratiocolor`**: Fetched from `RATIOCOLOR_REPO` at `RATIOCOLOR_REF`. The default remains the tested Micdu70 implementation because reviewed active forks are not safe drop-in replacements for the current plugin set.

The GeoLite2 database source and license notice are retained at `/usr/share/licenses/GeoLite2/NOTICE` in the image. Updating `GEOIP2_DB_VERSION` also requires updating `GEOIP2_DB_SHA256`; the build fails instead of accepting an unverified or missing database. The upstream release retains only the two newest database versions, so an old pin can disappear and a clean build can fail with 404. Refresh both pins before an uncached build, or use a controlled `GEOIP2_DB_URL` mirror with the same checksum. Follow the database license requirements when maintaining a mirror.

#### Upstream Compatibility

General rTorrent 0.16.x / tinyxml2 / trusted `httprpc` compatibility is handled by upstream ruTorrent 5.3.x. The Docker image does not maintain a separate compatibility overlay for `xmlrpc.php`, `httprpc`, `getplugins.php`, or generic plugin command aliases.

### Log Rotation

The image includes an automatic log rotation service for nginx logs:

| Variable | Description | Default |
|----------|-------------|---------|
| `MAX_SIZE` | Positive integer size threshold in bytes | `10485760` (10MB) |
| `SLEEP_SECS` | Positive integer interval in seconds | `3600` (1 hour) |

The service rotates the four `/tmp/nginx-*.log` files; their directory is fixed by nginx configuration. Invalid `MAX_SIZE` or `SLEEP_SECS` values produce a warning and use the defaults. PHP, rTorrent and FileBot logs are not covered.

Rotation scheme: `access.log` → `access.log.1` → `access.log.1.gz` → delete. It retains the current log, the previous uncompressed rotation, and the previous compressed rotation (three files total).

### Ports

- `8080/tcp` – ruTorrent UI
- `45000/tcp` (configurable via `PORT_RTORRENT`)
- `6881/udp` – DHT only when enabled; use `45000/udp` instead if DHT is configured on the listen port

## FAQ

- **Can I use one port for everything (peers + DHT)?** Yes. Set `network.listen.port.range.set = 45000-45000`, set `network.listen.port.random.set = no`, and set `dht.override_port.set` to the same number. Expose the listen port as TCP and the enabled DHT port as UDP. For an existing volume, edit `.rtorrent.rc`; environment variables seed only new files.
- **Listen port vs DHT port — what’s the difference?** The listen port (`network.listen.port.range.set`) is where other peers connect to you for torrent data; it uses TCP; this build does not provide uTP. `network.listen.port.random.set` controls whether rTorrent randomly selects a port from that range. The DHT port (`dht.override_port.set`) is UDP-only and used to talk to the distributed hash table for trackerless peer discovery. They can be the same (simpler firewall/NAT rules) or different if you need to split traffic; open the listen port as TCP and the DHT port as UDP when DHT is enabled.

## Usage

### Default launch

```sh
docker run --name rutorrent -d \
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
docker run --name rutorrent -dt \
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

`UID` and `GID` select the numeric identity used by rTorrent. Startup remaps the built-in `torrent` account and group when those IDs are free, or reuses an existing group such as `users:100`. Startup adjusts ownership of ordinary working paths and, by default, `/data`; protected journals, root configuration and witnesses are excluded from recursive ownership changes. Before first use, `/config`, `/config/rutorrent`, `/data` and the session directory must be real directories owned by root or the selected `UID`. Their permissions and private journal entries are checked before ownership changes. `CHECK_PERM_DATA=false` skips the data ownership sweep and does not disable trust checks. See “Upgrading and troubleshooting” for repairs and read-only mounts.

### FileBot launch

Place the license in persistent storage before starting. The example uses `UID:GID=1000:1000`; create trusted parent directories without group/world write permissions (use your own paths and IDs):

```sh
sudo install -d -o 1000 -g 1000 -m 0755 /mnt/docker/rutorrent/config /mnt/docker/rutorrent/data
sudo install -d -o 1000 -g 1000 -m 0700 /mnt/docker/rutorrent/config/filebot
sudo install -o 1000 -g 1000 -m 0600 ./FileBot_License.psm /mnt/docker/rutorrent/config/filebot/FileBot_License.psm
```

```sh
docker run --name rutorrent-filebot -dt \
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

The command writes a password hash into `/config/nginx/passwd/rutorrent_passwd`; reusing `/config` preserves it and nginx reads updates without a restart. HTTP Basic authentication requires TLS when the network is untrusted.

An installation that explicitly sets `HTTP_AUTH=false` must remove that override or set `ALLOW_UNAUTHENTICATED_HTTP=true`. This exposes stored tracker credentials and torrent controls to clients that can reach port 8080. Keep this mode confined to trusted clients. Origin/CSRF checks remain enabled, and the image uses a single shared ruTorrent profile (`$forbidUserSettings=true`) in both modes.

`/php/getplugins.php` is a WebUI AJAX bootstrap endpoint. A direct GET client must send
`X-Requested-With: XMLHttpRequest`; a plain authenticated `curl` request receives 403.
State-changing WebUI requests must carry an `Origin` or `Referer` matching the URL's scheme, host and port. A reverse proxy must preserve the external `Host` (including its port), set `X-Forwarded-Proto` to the external scheme, and overwrite that header rather than accept a client-supplied value. Forwarding headers should come only from the trusted proxy. Test a normal WebUI upload after enabling TLS; an origin mismatch returns 403. For explicitly trusted additional browser origins, updated fork sources accept `RU_TRUSTED_ORIGINS` as a comma-separated list of complete HTTP(S) origins (scheme, host and port), or exact browser-extension origins. Wildcards are refused. This environment value is passed to PHP only when provided; standalone deployments can use the fork's `$trustedOrigins` array in `config.local.php`.
`/healthz-plugins` is denied on the public listener. The container scheduler uses a private Unix socket, which a reverse proxy must not expose.

### rTorrent RPC2 endpoint

`/RPC2` is inaccessible by default. It is the fork's filtering XML-RPC proxy. Enable it only for clients that need it, and protect it with HTTP authentication:

```sh
docker run --name rutorrent-rpc2 -dt \
  -e ENABLE_RPC2=true \
  -e HTTP_AUTH=true \
  -p 8080:8080 \
  -v rutorrent_config:/config \
  -v rutorrent_data:/data \
  ivanshift/rutorrent:latest
```

With the default `sanitize` policy, command execution, method/schedule changes, session/default-directory changes and local-path loads are rejected with HTTP 403 and XML-RPC fault `-501`. The daemon can additionally reject a command with HTTP 200 and fault `-507` (`not allowed for untrusted connections`).

New volumes use the image's proxy policy. An old `/config/rutorrent/conf/xmlrpc_proxy.php` remains active; inspect it before retaining it. To install the image policy explicitly:

```sh
docker exec rutorrent cp /rutorrent/conf-dist/xmlrpc_proxy.php /config/rutorrent/conf/xmlrpc_proxy.php
```

Enabling `ENABLE_RPC2=true` with `HTTP_AUTH=false` still permits torrent controls to reachable clients. Startup warns about this combination; confine it to trusted clients.

### Custom plugins/themes

Place each custom plugin in its own directory under `/config/custom_plugins`, with the plugin's entry files directly inside that directory. Place each theme in its own directory under `/config/custom_themes`; a repository containing several themes must be unpacked into separate theme directories. Bundled plugin/theme names are reserved and custom directories do not replace them. Install only code you trust: server-side plugins run as the service user and can read torrent and tracker settings.

The image includes `geoip2` and `ratiocolor`; cloning them again into the custom directory is unnecessary. See the startup diagnostics when a custom name collides or an entry directory is invalid.

## Upgrading and troubleshooting

Stop every container using the same volumes and back them up before maintenance. Use `docker stop --time 60 rutorrent` (or `--stop-timeout 60` when creating the container) to give rTorrent time to save its session. After an image update, reload the UI without browser cache if it still serves an older JavaScript bundle.

Session and queue storage requires POSIX owners, permissions, regular files and atomic rename semantics. Linux 5.4 or newer is required for the native background-task cancellation helper. Data-directory rename operations require filesystem support for `renameat2(RENAME_NOREPLACE)`. FAT/exFAT, incompatible network mounts and older kernels may not provide these properties; keep the session and protected journals on suitable local storage.

| Exit / symptom | Diagnostic | Action |
|---------------|------------|--------|
| 64 | `HTTP_AUTH`, `ENABLE_RPC2` or `CHECK_PERM_DATA` must be exactly true or false; invalid UID, port, DHT/PEX or directory value | Correct the named environment value. `HTTP_AUTH=false` also requires `ALLOW_UNAUTHENTICATED_HTTP=true`. |
| 78 | `Cannot attest …` / unsafe or foreign-owned directory | Inspect the named path, owner and mode outside the application container; repair only trusted storage as below. |
| 78 / erasedata unavailable | Missing/unsafe `.erasedata-first-use`, missing `erasedata-jobs`, or legacy queue needs operator attestation | Inspect pending jobs while stopped, then use the explicit offline queue command below. |
| Nonzero migration exit | `Unable to update old configurations (exit …)` | Read the preceding `update-config` diagnostic. Repair invalid syntax or provide writable regular files; startup does not launch services after a migration error. |
| 401 / 403 before first user | HTTP authentication has no provisioned password file | Run `docker exec -it rutorrent gen-http-passwd`. |
| 403 during WebUI POST | Origin/Referer mismatch | Correct the trusted reverse proxy's external Host and scheme. |
| 403 / fault -501 at `/RPC2` | Proxy policy refused the command | Check `conf/xmlrpc_proxy.php`; keep executable and filesystem controls restricted. |
| HTTP 200 / fault -507 at `/RPC2` | Native untrusted-connection policy refused the command | Use a supported remote command; this is a daemon policy rejection. |

For trusted host directories using `UID:GID=1000:1000`, inspect the exact paths first with `ls -ldn`. If they contain your own data and should belong to that identity, repair the parent directories with targeted commands:

```sh
sudo chown 1000:1000 /mnt/docker/rutorrent/config /mnt/docker/rutorrent/config/rutorrent /mnt/docker/rutorrent/data
sudo chmod 0755 /mnt/docker/rutorrent/config /mnt/docker/rutorrent/config/rutorrent /mnt/docker/rutorrent/data
sudo chown 1000:1000 /mnt/docker/rutorrent/data/.session
sudo chmod 00700 /mnt/docker/rutorrent/data/.session
```

The explicit `00700` clears session-directory setgid as well as group/world access. For a customized session path, repair that path and inspect all its ancestors. Private journal directories need mode 0700, their regular records 0600, the selected UID or root as owner, and no hard links or symlinks. Do not run a recursive `chown` over `/config`: `.erasedata-first-use` must remain root:root 0400, empty and single-linked. Restore those properties only after verifying that the witness and queue are trusted; otherwise use offline attestation. Read-only mounted working files, including the password file, must already have the selected identity because startup cannot change their owners.

To migrate empty legacy erasedata queues, first inspect `/config/rutorrent/share/settings/erasedata` and each `/config/rutorrent/share/users/*/settings/erasedata`. Pending `.list`, `.pending`, protected jobs or unexpected entries need manual review; the command refuses nonempty queues and never executes their contents. Legacy settings parents must have no group/world write, and queue directories must have no group/world access. For a trusted default-profile host directory, set these modes while stopped (apply the same checks and targeted changes to each existing per-user profile):

```sh
sudo chmod go-w /mnt/docker/rutorrent/config/rutorrent/share /mnt/docker/rutorrent/config/rutorrent/share/settings
sudo chmod 00700 /mnt/docker/rutorrent/config/rutorrent/share/settings/erasedata
```

After that inspection, run a disposable offline container with the same `/config` volume and numeric identity:

```sh
docker stop --time 60 rutorrent
docker run --rm --name rutorrent-queue-maintenance \
  --entrypoint attest-erasedata-queue \
  -e UID=1000 -e GID=1000 \
  -v /mnt/docker/rutorrent/config:/config \
  ivanshift/rutorrent:latest --attest-clean-legacy-queue
docker start rutorrent
```

Use the actual identity, mount and image of your installation. The command removes only empty old queue directories, creates `erasedata-jobs` as UID:GID 0700 and replaces a verified clean witness as root:root 0400. It refuses links, foreign ownership and nonempty state. If it refuses, inspect its named path and preserve pending jobs; restarting or disabling `CHECK_PERM_DATA` does not attest them.

## Image internals

- ruTorrent and third-party plugin sources have dedicated cacheable stages; native tools and MediaInfo also download verified sources in their respective builder stages.
- ruTorrent and third-party plugins are fetched by explicit refs; plugin defaults resolve to exact commits.
- The GeoIP database is selected by release version and guarded by a mandatory SHA256 checksum.
- rTorrent and libtorrent are compiled with optional `-Werror` controls (`STRICT_WERROR` arg).
- Runtime image stays small: only runtime packages and healthcheck dependencies are installed.
- Healthcheck runs every 60 seconds, has a 10-minute initialization period, and verifies the rTorrent process/socket plus nginx and PHP through the loopback `/healthz` endpoint. Each probe has a bounded timeout.

## License

The build scripts in this repository use the [MIT License](LICENSE). Software bundled in the image, including ruTorrent, rTorrent, libtorrent, FFmpeg and UnRAR, keeps its own licenses. Component license texts and source notices are retained in the image; the MIT license does not cover those components.
