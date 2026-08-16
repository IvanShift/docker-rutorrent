# IvanShift/rutorrent

Opinionated ruTorrent + rTorrent container image with a focus on controlled source builds, small footprint, and a prepared ruTorrent fork.

## Features

- Multi-arch image (`linux/amd64`, `linux/arm64`) built on Alpine Linux 3.24.1
- PHP 8.5 with the IvanShift/ruTorrent fork pinned to `b753d01bd619b96064f8bfb3d4c4f7532efd4d94`, rTorrent/libtorrent 0.16.20, Alpine-provided c-ares, and UnRAR 7.23 built from RARLab's `unrarsrc-7.2.7.tar.gz`
- rTorrent uses the tinyxml2 XML-RPC backend for faster ruTorrent plugin calls
- Non-root runtime (`UID` / `GID` configurable), healthcheck-ready, and persistent volumes
- Automatic log rotation for nginx access/error logs (prevents disk space exhaustion)
- Optional FileBot integration (portable 5.2.3) with OpenJDK 21 and chromaprint
- Supply-chain aware build: ruTorrent fetched by an explicit ref, plugins pinned to exact default commits, and a checksum-verified GeoIP database
- Easy plugin/theme overrides through `/config` mounts
- Image additions: custom forked `rutracker_check` behavior for RuTracker/NNMClub plus build-time fetched `geoip2` and `ratiocolor` plugins

## Tags

- `latest` – standard image (default build args)
- `filebot` – same as latest but built with `FILEBOT=true`
- `rtorrent-<major.minor.patch>` / `filebot-rtorrent-<major.minor.patch>` – CI tags aligned with the rTorrent version from `Dockerfile`

## Build

### Build arguments

| Argument | Description | Type | Default |
|----------|-------------|------|---------|
| `ALPINE_VERSION` | Alpine base image tag | optional | `3.24.1` |
| `MKTORRENT_VERSION` | mktorrent release tag | optional | `v1.1` |
| `MKTORRENT_COMMIT` | Expected resolved commit for `MKTORRENT_VERSION` | optional | `b20ef699b4ee5ded2f078ead776c7deac969e19a` |
| `DUMP_TORRENT_VERSION` | dump-torrent release tag | optional | `v1.7.0` |
| `DUMP_TORRENT_COMMIT` | Expected resolved commit for `DUMP_TORRENT_VERSION` | optional | `ddf988d3099637c93dc0247854ca711c0a2a0289` |
| `UNRAR_VERSION` | Suffix used in RARLab's `unrarsrc-${UNRAR_VERSION}.tar.gz` filename; `7.2.7` contains UnRAR 7.23 sources | optional | `7.2.7` |
| `UNRAR_SHA256` | Expected SHA256 of the UnRAR source archive | optional | `01d903a7dcf413cb2925696d7796e48e38d471f79bfe7ef3ad2aebf6c12dbefd` |
| `FILEBOT` | Include FileBot + JRE/FFmpeg stack | optional | `false` |
| `FILEBOT_VER` | FileBot portable release tag | optional | `5.2.3` |
| `FILEBOT_SHA256` | Expected SHA256 of the FileBot portable archive | optional | `0dae8364f9d465707ff30031d055dcc7c6b24907d96823ced3d4e979f1519d0c` |
| `RUTORRENT_REPO` | ruTorrent fork repository URL | optional | `https://github.com/IvanShift/ruTorrent.git` |
| `RUTORRENT_REF` | ruTorrent fork ref fetched and checked out detached | optional | `b753d01bd619b96064f8bfb3d4c4f7532efd4d94` |
| `GEOIP2_REPO` | GeoIP2 plugin repository URL | optional | `https://github.com/Micdu70/geoip2-rutorrent.git` |
| `GEOIP2_REF` | GeoIP2 branch, tag, full ref, or commit | optional | `cad8a11b47f02ff75358b7bd9c4137648f5fedd0` |
| `RATIOCOLOR_REPO` | RatioColor plugin repository URL | optional | `https://github.com/Micdu70/rutorrent-ratiocolor.git` |
| `RATIOCOLOR_REF` | RatioColor branch, tag, full ref, or commit | optional | `4aec1988be1e09b44799b71ed4a25751c695a6f2` |
| `GEOIP2_DB_VERSION` | P3TERX GeoLite2 release containing `GeoLite2-Country.mmdb` | optional | `2026.08.13` |
| `GEOIP2_DB_SHA256` | Expected SHA256 of `GeoLite2-Country.mmdb` | optional | `b6a525d8ffd7628b59a1c7853264937ae3a23632deebed9daae2ebffbf876265` |
| `LIBTORRENT_BRANCH` | libtorrent release tag used for source checkout | optional | `v0.16.20` |
| `LIBTORRENT_VERSION` | Expected resolved libtorrent source commit | optional | `ea68bf287e36a13fa0c13c965dee6c3d40e3d242` |
| `RTORRENT_BRANCH` | rTorrent release tag used for source checkout | optional | `v0.16.20` |
| `RTORRENT_VERSION` | Expected resolved rTorrent source commit | optional | `dd3ddf7c391ada92e4ba86fb6afd8a6cc01446b8` |
| `STRICT_WERROR` | Treat selected warnings as errors during C++ builds | optional | `true` |
| `GEOIP2_COMMIT_SHA`, `RATIOCOLOR_COMMIT_SHA` | Deprecated compatibility overrides for the corresponding `*_REF` | optional | _(empty)_ |

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

The build validates source pairs rather than trusting a name alone: the rTorrent/libtorrent branches and the mktorrent/dump-torrent tags must resolve to their corresponding expected commits. The default ruTorrent and plugin refs are exact commits; the GeoLite2, UnRAR, and optional FileBot downloads must match their configured SHA256 values. Plugin stages also verify their required entry files, and the image verifies a GeoLite2 lookup after installation.

For reproducible source inputs, use exact commits and matching checksums. The legacy `GEOIP2_COMMIT_SHA` and `RATIOCOLOR_COMMIT_SHA` arguments still work and take precedence over `GEOIP2_REF` and `RATIOCOLOR_REF` when non-empty. Docker cache does not detect when a remote branch or tag moves, so rebuild the corresponding `geoip2-source` or `ratiocolor-source` stage without cache when deliberately using a mutable ref. These protections do not guarantee byte-identical images: the Alpine base tag, package repositories (including `apk upgrade`), and builder environment can change over time. Pin or mirror those external inputs too when full image reproducibility is required.

## Runtime configuration

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` / `GID` | User/group IDs used by rTorrent & services | `991` |
| `PORT_RTORRENT` | TCP listening port for rTorrent | `45000` |
| `MODE_DHT` | DHT mode (`off`, `on`, `auto`, `disable`) | `off` |
| `PORT_DHT` | UDP DHT port | `6881` |
| `PEER_EXCHANGE` | Enable PEX (`yes` / `no`) | `no` |
| `DOWNLOAD_DIRECTORY` | Main downloads directory | `/data/downloads` |
| `CHECK_PERM_DATA` | Permission check toggle | `true` |
| `HTTP_AUTH` | Enable HTTP auth in nginx/ruTorrent | `false` |
| `ENABLE_RPC2` | Enable the rTorrent XML-RPC endpoint at `/RPC2`; must be exactly `true` or `false` | `false` |

`PORT_RTORRENT`, `MODE_DHT`, `PORT_DHT`, and `PEER_EXCHANGE` seed the image-provided `.rtorrent.rc` only on the first start that creates `/config/rtorrent/.rtorrent.rc`. Once that file exists, it is treated as user-managed and these environment variables do not overwrite it.

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
- `/config/custom_plugins` / `/config/custom_themes` – custom overrides
- `/config/filebot/*` – FileBot license and scripts

### Fork Changes

The Docker build fetches the prepared ruTorrent fork by `RUTORRENT_REF`; by default it checks out detached commit `b753d01bd619b96064f8bfb3d4c4f7532efd4d94` from `IvanShift/ruTorrent`. It no longer copies `overrides/rutorrent` over the downloaded tree and no longer applies `sed` patches to ruTorrent files. Third-party plugins are fetched in independent source stages at exact default commits, then copied into the cleaned runtime tree without VCS metadata.

#### `rutracker_check`

A heavily modified tracker checker with stability and functionality improvements:

- **Smart File Cleanup**: Automatically removes obsolete files (renamed or deleted in the new torrent) after an update.
- **Recursive Folder Cleanup**: Removes empty directories left behind after file cleanup.
- **Improved URL Detection**: Prioritizes comment URLs over announce URLs (fixes RuTracker topic detection).
- **Critical Bug Fixes**: Fixed ratio group visibility (`rat_Array` bug) and label initialization.
- **PHP 8 Compatibility**: Fixed `TypeError` in `scandir`/`array_diff` and added defensive checks.
- **Anti-Bot Protection**: Uses a modern Chrome User-Agent to reduce 403 errors.
- **Absorption Detection**: Enhanced logic to detect "absorbed" topics by searching for links both before and after keywords.
- **NNMClub Auto-Check Restored** (`trackers/nnmclub.php`): Automatic torrent update checking for NNMClub is fully functional again despite Cloudflare Turnstile protection on the website.

#### Build-Time Plugins

- **`geoip2`**: Fetched from `GEOIP2_REPO` at `GEOIP2_REF`. Its bundled 2023 database is replaced with checksum-verified `GeoLite2-Country.mmdb` from the versioned [P3TERX/GeoLite.mmdb](https://github.com/P3TERX/GeoLite.mmdb) release.
- **`ratiocolor`**: Fetched from `RATIOCOLOR_REPO` at `RATIOCOLOR_REF`. The default remains the tested Micdu70 implementation because reviewed active forks are not safe drop-in replacements for the current plugin set.

The GeoLite2 database source and license notice are retained at `/usr/share/licenses/GeoLite2/NOTICE` in the image. Updating `GEOIP2_DB_VERSION` also requires updating `GEOIP2_DB_SHA256`; the build fails instead of accepting an unverified or missing database.

#### Upstream Compatibility

General rTorrent 0.16.x / tinyxml2 / trusted `httprpc` compatibility is handled by upstream ruTorrent 5.3.x. The Docker image does not maintain a separate compatibility overlay for `xmlrpc.php`, `httprpc`, `getplugins.php`, or generic plugin command aliases.

### Log Rotation

The image includes an automatic log rotation service for nginx logs:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_DIR` | Directory containing log files | `/tmp` |
| `MAX_SIZE` | Rotate when file exceeds this size (bytes) | `10485760` (10MB) |
| `SLEEP_SECS` | Interval between rotation checks (seconds) | `3600` (1 hour) |

Rotation scheme: `access.log` → `access.log.1` → `access.log.1.gz` → delete. It retains the current log, the previous uncompressed rotation, and the previous compressed rotation (three files total).

### Ports

- `8080/tcp` – ruTorrent UI
- `45000/tcp` (configurable via `PORT_RTORRENT`)
- `45000/udp`, `6881/udp` – DHT/peer ports (expose as needed)

## FAQ

- **Can I use one port for everything (peers + DHT)?** Yes. Set `network.listen.port.range.set = 45000-45000`, set `network.listen.port.random.set = no`, and set `dht.override_port.set` to the same number. Expose the listen port as TCP+UDP; if the DHT port is separate, open it as UDP.
- **Listen port vs DHT port — what’s the difference?** The listen port (`network.listen.port.range.set`) is where other peers connect to you for torrent data; it uses TCP (classic BitTorrent) and UDP (uTP). `network.listen.port.random.set` controls whether rTorrent randomly selects a port from that range. The DHT port (`dht.override_port.set`) is UDP-only and used to talk to the distributed hash table for trackerless peer discovery. They can be the same (simpler firewall/NAT rules) or different if you need to split traffic; always keep the listen port open as TCP+UDP so peers can reach you.

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
```

UI: <http://localhost:8080>

The explicit DNS servers keep tracker plugin lookups independent from the Docker host resolver. This is required for `rutracker_check` when the default container DNS does not resolve `rutracker.org`.

### FileBot launch

```sh
docker run --name rutorrent-filebot -dt \
  -e UID=1000 \
  -e GID=1000 \
  -e FILEBOT_LICENSE=/config/filebot/FileBot_License.psm \
  -p 9080:8080 \
  -p 6881:6881 \
  -p 6881:6881/udp \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/data:/data \
  ivanshift/rutorrent:filebot
```

### HTTP authentication

```sh
docker run --name rutorrent-auth -dt \
  -e HTTP_AUTH=true \
  -p 8080:8080 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/data:/data \
  ivanshift/rutorrent:latest

docker exec -it rutorrent-auth gen-http-passwd
```
Generate your password:

```sh
docker exec -it rutorrent gen-http-passwd
Username: torrent
Password:
Verifying - Password:
Password was generated for the http user: torrent
```

URL: http://xx.xx.xx.xx:8080

### rTorrent RPC2 endpoint

`/RPC2` is inaccessible by default. Enable it only for clients that need direct rTorrent XML-RPC access, and protect it with HTTP authentication:

```sh
docker run --name rutorrent-rpc2 -dt \
  -e ENABLE_RPC2=true \
  -e HTTP_AUTH=true \
  -p 8080:8080 \
  -v rutorrent_config:/config \
  -v rutorrent_data:/data \
  ivanshift/rutorrent:latest
```

Enabling `ENABLE_RPC2=true` without `HTTP_AUTH=true` exposes a trusted, unauthenticated control endpoint. The container logs a warning for that configuration; use it only on a suitably isolated network.

### Custom plugins/themes

```sh
mkdir -p /mnt/docker/rutorrent/config/custom_plugins
# ratiocolor is already installed by the image build; custom plugins can still be mounted here.
# git clone https://example.com/some-rutorrent-plugin.git \
#   /mnt/docker/rutorrent/config/custom_plugins/some-plugin

mkdir -p /mnt/docker/rutorrent/config/custom_themes
git clone https://github.com/artyuum/3rd-party-ruTorrent-Themes.git \
  /mnt/docker/rutorrent/config/custom_themes/themes-pack
```

## Image internals

- Source assets are fetched in dedicated stages to maximise cache reuse.
- ruTorrent and third-party plugins are fetched by explicit refs; plugin defaults resolve to exact commits.
- The GeoIP database is selected by release version and guarded by a mandatory SHA256 checksum.
- rTorrent and libtorrent are compiled with optional `-Werror` controls (`STRICT_WERROR` arg).
- Runtime image stays small: only runtime packages and healthcheck dependencies are installed.
- Healthcheck runs every 60 seconds and requires both the rTorrent Unix socket and the loopback-only `/healthz` HTTP endpoint.

## License

Docker image [ivanshift/rutorrent](https://hub.docker.com/r/ivanshift/rutorrent) is released under the [MIT License](LICENSE).
