# IvanShift/rutorrent

Opinionated ruTorrent + rTorrent container image with a focus on deterministic builds, small footprint, and simple overrides.

## Features

- Multi-arch image (`linux/amd64`, `linux/arm64`) built on Alpine Linux 3.23
- PHP 8.5 with ruTorrent 5.2.10, rTorrent/libtorrent 0.16.5, c-ares 1.34.6
- rTorrent uses the tinyxml2 XML-RPC backend for faster ruTorrent plugin calls
- Non-root runtime (`UID` / `GID` configurable), healthcheck-ready, and persistent volumes
- Optional FileBot integration (portable 5.2.0) with on-demand multimedia dependencies
- Supply-chain aware build: shallow git clones, optional SHA256 verification, ruTorrent release tarballs
- Easy plugin/theme overrides through `/config` mounts
- Bundled ruTorrent overrides: safe XML-RPC target handling, RSS UI guardrails, version-aware plugin calls (multicall/view.* fallbacks), and upgraded plugin manifests (5.1.x→5.1.2) for easier troubleshooting

## Tags

- `latest` – standard image (default build args)
- `filebot` – same as latest but built with `FILEBOT=true`

## Build

### Build arguments

| Argument | Description | Type | Default |
|----------|-------------|------|---------|
| `FILEBOT` | Include FileBot + JRE/FFmpeg stack | optional | `false` |
| `FILEBOT_VER` | FileBot portable release tag | optional | `5.2.0` |
| `RUTORRENT_VER` | ruTorrent release tag | optional | `5.2.10` |
| `STRICT_WERROR` | Treat selected warnings as errors during C++ builds | optional | `true` |
| `CARES_SHA256` | Expected checksum for the c-ares tarball | optional | _(empty)_ |
| `RUTORRENT_SHA256` | Expected checksum for ruTorrent release archive | optional | _(empty)_ |
| `GEOIP2_COMMIT_SHA`, `RATIOCOLOR_COMMIT_SHA` | Pin plugin repos to specific commits | optional | _(empty)_ |

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

### Hardened build (checksums & pinned plugins)

```sh
docker build --tag ivanshift/rutorrent:ci \
  --build-arg RUTORRENT_SHA256="sha256:..." \
  --build-arg CARES_SHA256="..." \
  --build-arg GEOIP2_COMMIT_SHA="abcdef123..." \
  --build-arg RATIOCOLOR_COMMIT_SHA="123abc456..." \
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
| `FILEBOT_LANG` | Language preference | `fr` |
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

### Overrides & Improvements

This image includes patched versions of core files and plugins to ensure stability, compatibility with modern rTorrent/PHP versions, and to fix long-standing bugs.

#### Core Fixes

- **`php/xmlrpc.php`**: Prepends an empty target for `d.*`/`t.*`/`f.*`/`ratio.*`/`to_*` calls when missing, preventing XML-RPC errors.
- **`php/getplugins.php`**: Keeps `plugin.version` values as strings (prevents truncation of `5.10.1` to `5.1`).
- **`js/common.js`**: Guards directory lookups, trackers/chunks parsing, and forces string type for plugin versions.
- **`css/statusbar.css`**: Enables horizontal scrolling of the status bar without visible scrollbars.

#### Plugin Improvements

##### `rutracker_check` (v5.1.2)
A heavily modified version with significant stability and functionality improvements:
- **Smart File Cleanup**: Automatically removes obsolete files (renamed or deleted in the new torrent) after an update.
- **Recursive Folder Cleanup**: Removes empty directories left behind after file cleanup.
- **Improved URL Detection**: Prioritizes comment URLs over announce URLs (fixes RuTracker topic detection).
- **Critical Bug Fixes**: Fixed ratio group visibility (`rat_Array` bug) and label initialization.
- **PHP 8 Compatibility**: Fixed `TypeError` in `scandir`/`array_diff` and added defensive checks.
- **Anti-Bot Protection**: Uses a modern Chrome User-Agent to reduce 403 errors.
- **Absorption Detection**: Enhanced logic to detect "absorbed" topics by searching for links both before and after keywords.

##### `httprpc` (v5.1.2)
- **Settings Persistence**: Restored the `setsettings` handler to ensure ruTorrent settings changes are correctly applied to rTorrent (fixes issues on rTorrent 0.9.x).
- **Modern rTorrent Compatibility**: Skips unsupported `set_hash_*` calls on newer rTorrent versions to prevent XML-RPC faults (-506).
- **Chunks Tab**: Restored `getchunks` handler to fix the "Chunks" tab functionality.
- **DHT Port Setter**: Uses `dht.override_port.set` on rTorrent 0.16.x so DHT port changes from the UI are applied correctly.

##### `ratio` (v5.1.2)
- **Compatibility**: Fixed multicall targets and added `view.add`/`view_list` fallbacks for broader rTorrent version support.

##### `rss`
- **Stability**: Patched `init.js` to safely handle missing RSS payloads, preventing UI freezes.

##### Other Plugins
- **Version-Awareness**: `_getdir`, `datadir`, `autotools`, and `extratio` have been updated to v5.1.2 with version-aware XML-RPC calls (`getCmd`), ensuring compatibility across different rTorrent versions.

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
  -p 8080:8080 \
  -p 45000:45000 \
  -v rutorrent_config:/config \
  -v rutorrent_data:/data \
  ivanshift/rutorrent:latest
```

### Simple launch

```sh
docker run --name rutorrent -dt \
  -e UID=1000 \
  -e GID=1000 \
  -p 8080:8080 \
  -p 45000:45000 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/data:/data \
  ivanshift/rutorrent:latest
```

UI: <http://localhost:8080>

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
git clone https://github.com/Gyran/rutorrent-ratiocolor.git \
  /mnt/docker/rutorrent/config/custom_plugins/ratiocolor

mkdir -p /mnt/docker/rutorrent/config/custom_themes
git clone https://github.com/artyuum/3rd-party-ruTorrent-Themes.git \
  /mnt/docker/rutorrent/config/custom_themes/themes-pack
```

## Image internals

- Source assets are fetched in a dedicated stage to maximise cache hits.
- All builds use release tarballs or depth-limited clones; you can supply checksums to fail fast.
- rTorrent and libtorrent are compiled with optional `-Werror` controls (`STRICT_WERROR` arg).
- Runtime image stays small: only runtime packages are installed; the `curl` binary for the healthcheck comes from the build stage.
- Healthcheck queries the ruTorrent UI via `curl` every 60 seconds.

## License

Docker image [ivanshift/rutorrent](https://hub.docker.com/r/ivanshift/rutorrent) is released under the [MIT License](LICENSE).
