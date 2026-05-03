# IvanShift/rutorrent

Opinionated ruTorrent + rTorrent container image with a focus on controlled source builds, small footprint, and a prepared ruTorrent fork.

## Features

- Multi-arch image (`linux/amd64`, `linux/arm64`) built on Alpine Linux 3.23.4
- PHP 8.5 with the IvanShift/ruTorrent fork from its `master` branch, rTorrent/libtorrent 0.16.11, c-ares 1.34.6, and UnRAR 7.2.6
- rTorrent uses the tinyxml2 XML-RPC backend for faster ruTorrent plugin calls
- Non-root runtime (`UID` / `GID` configurable), healthcheck-ready, and persistent volumes
- Automatic log rotation for nginx access/error logs (prevents disk space exhaustion)
- Optional FileBot integration (portable 5.2.1) with OpenJDK 21 and on-demand multimedia dependencies
- Supply-chain aware build: ruTorrent fork fetched by explicit remote ref, shallow git fetches, and optional SHA256 verification for supported source tarballs
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
| `ALPINE_VERSION` | Alpine base image tag | optional | `3.23.4` |
| `CARES_VERSION` | c-ares release version | optional | `1.34.6` |
| `MKTORRENT_VERSION` | mktorrent release tag | optional | `v1.1` |
| `DUMP_TORRENT_VERSION` | dump-torrent release tag | optional | `v1.7.0` |
| `UNRAR_VERSION` | UnRAR source release version | optional | `7.2.6` |
| `FILEBOT` | Include FileBot + JRE/FFmpeg stack | optional | `false` |
| `FILEBOT_VER` | FileBot portable release tag | optional | `5.2.1` |
| `RUTORRENT_REPO` | ruTorrent fork repository URL | optional | `https://github.com/IvanShift/ruTorrent.git` |
| `RUTORRENT_REF` | ruTorrent fork remote ref, branch, tag, or commit | optional | `refs/heads/master` |
| `LIBTORRENT_BRANCH` | libtorrent release tag used for source checkout | optional | `v0.16.11` |
| `RTORRENT_BRANCH` | rTorrent release tag used for source checkout | optional | `v0.16.11` |
| `STRICT_WERROR` | Treat selected warnings as errors during C++ builds | optional | `true` |
| `CARES_SHA256` | Expected checksum for the c-ares tarball | optional | _(empty)_ |
| `GEOIP2_COMMIT_SHA`, `RATIOCOLOR_COMMIT_SHA` | Pin build-time plugin clones to specific commits | optional | _(empty)_ |

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

### Custom ruTorrent ref build

```sh
docker build --tag ivanshift/rutorrent:ci \
  --build-arg CARES_SHA256="..." \
  --build-arg RUTORRENT_REF="refs/heads/master" \
  https://github.com/IvanShift/docker-rutorrent.git
```

## Runtime configuration

### Environment variables

| Variable | Description | Default |
|----------|-------------|---------|
| `UID` / `GID` | User/group IDs used by rTorrent & services | `991` |
| `PORT_RTORRENT` | TCP listening port for rTorrent | `45000` |
| `MODE_DHT` | DHT mode (`off`, `on`, `disable`) | `off` |
| `PORT_DHT` | UDP DHT port | `6881` |
| `PEER_EXCHANGE` | Enable PEX (`yes` / `no`) | `no` |
| `DOWNLOAD_DIRECTORY` | Main downloads directory | `/data/downloads` |
| `CHECK_PERM_DATA` | Permission check toggle | `true` |
| `HTTP_AUTH` | Enable HTTP auth in nginx/ruTorrent | `false` |

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

The Docker build fetches the prepared ruTorrent fork by `RUTORRENT_REF`; by default it tracks `refs/heads/master` from `IvanShift/ruTorrent`. It no longer copies `overrides/rutorrent` over the downloaded tree and no longer applies `sed` patches to ruTorrent files. The build then clones third-party plugins into the image and removes unused docs, VCS metadata, and unwanted upstream plugins from the runtime image.

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

- **`geoip2`**: Cloned from `Micdu70/geoip2-rutorrent` during Docker build, then `.git` metadata is removed from the runtime image.
- **`ratiocolor`**: Cloned from `Micdu70/rutorrent-ratiocolor` during Docker build, then `.git` metadata is removed from the runtime image.

#### Upstream Compatibility

General rTorrent 0.16.x / tinyxml2 / trusted `httprpc` compatibility is handled by upstream ruTorrent 5.3.1. The Docker image does not maintain a separate compatibility overlay for `xmlrpc.php`, `httprpc`, `getplugins.php`, or generic plugin command aliases.

### Log Rotation

The image includes an automatic log rotation service for nginx logs:

| Variable | Description | Default |
|----------|-------------|---------|
| `LOG_DIR` | Directory containing log files | `/tmp` |
| `MAX_SIZE` | Rotate when file exceeds this size (bytes) | `10485760` (10MB) |
| `SLEEP_SECS` | Interval between rotation checks (seconds) | `3600` (1 hour) |

Rotation scheme: `access.log` → `access.log.1` → `access.log.1.gz` → delete (keeps 2 files total).

### Ports

- `8080/tcp` – ruTorrent UI
- `45000/tcp` (configurable via `PORT_RTORRENT`)
- `45000/udp`, `6881/udp` – DHT/peer ports (expose as needed)

## FAQ

- **Can I use one port for everything (peers + DHT)?** Yes. Set `port_range` to a single value like `45000-45000`, disable random ports, and set the DHT port (`dht.port` / `dht.override_port.set`) to the same number. Expose the listen port as TCP+UDP; if the DHT port is separate, open it as UDP.
- **Listen port vs DHT port — what’s the difference?** The listen port (`network.listen.port` / `port_range`) is where other peers connect to you for torrent data; it uses TCP (classic BitTorrent) and UDP (uTP). The DHT port (`dht.port` / `dht.override_port.set`) is UDP-only and used to talk to the distributed hash table for trackerless peer discovery. They can be the same (simpler firewall/NAT rules) or different if you need to split traffic; always keep the listen port open as TCP+UDP so peers can reach you.

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

- Source assets are fetched in a dedicated stage to maximise cache hits.
- ruTorrent and third-party plugins are fetched by explicit refs or depth-limited clones; native source dependencies use release tarballs or depth-limited clones.
- Supported source tarballs can be guarded with checksum build args such as `CARES_SHA256`.
- rTorrent and libtorrent are compiled with optional `-Werror` controls (`STRICT_WERROR` arg).
- Runtime image stays small: only runtime packages and healthcheck dependencies are installed.
- Healthcheck queries the ruTorrent UI via `curl` every 60 seconds.

## License

Docker image [ivanshift/rutorrent](https://hub.docker.com/r/ivanshift/rutorrent) is released under the [MIT License](LICENSE).
