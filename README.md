# IvanShift/rutorrent

[![](https://github.com/ivanshift/docker-rutorrent/workflows/build/badge.svg)](https://github.com/ivanshift/docker-rutorrent/actions)
[![](https://img.shields.io/docker/pulls/ivanshift/rutorrent)](https://hub.docker.com/r/ivanshift/rutorrent)
[![](https://img.shields.io/docker/stars/ivanshift/rutorrent)](https://hub.docker.com/r/ivanshift/rutorrent)

## Recommended Tags

## Features
 - RuTorrent 4.0
 - Multi-platform image: `linux/amd64`, `linux/arm64` (only 64-bit architecture)
 - Based on Alpine Linux 3.16
 - php 8.0
 - Provides by default a solid configuration
 - No root process
 - Persitance custom configuration for rutorrent and rtorrent
 - Add your own rutorrent plugins and themes
 - Filebot is included, and creates symlinks in `/downloads/media` (choose filebot tag)

## Tag available

 - latest [(Dockerfile)](https://github.com/ivanshift/docker-rutorrent/blob/master/Dockerfile)
 - filebot [(Dockerfile)](https://github.com/ivanshift/docker-rutorrent/blob/master/Dockerfile)

## Build image

### Build arguments

| Argument | Description | Type | Default value |
| -------- | ----------- | ---- | ------------- |
| **FILEBOT** | Build with filebot | *optional* | false
| **FILEBOT_VER** | Filebot version | *optional* | 4.9.4

### build

```sh
docker build --tag ivanshift/rutorrent:latest https://github.com/ivanshift/docker-rutorrent.git
```

### Build with arguments

```sh
docker build --tag ivanshift/rutorrent:filebot --build-arg FILEBOT=true https://github.com/ivanshift/docker-rutorrent.git
```

## Configuration

### Environment variables

| Variable | Description | Type | Default value |
| -------- | ----------- | ---- | ------------- |
| **UID** | Choose uid for launch rtorrent | *optional* | 991
| **GID** | Choose gid for launch rtorrent | *optional* | 991
| **PORT_RTORRENT** | Port of rtorrent | *optional* | 45000
| **DHT_RTORRENT** | DHT option in rtorrent.rc file | *optional* | off
| **CHECK_PERM_DATA** | Check permission data in downloads directory | *optional* | true
| **HTTP_AUTH** | Enable HTTP authentication | *optional* | false

### Environment variables with filebot

| Variable | Description | Type | Default value |
| -------- | ----------- | ---- | ------------- |
| **FILEBOT_LICENSE** | License file path | **required** | none
| **FILEBOT_RENAME_METHOD** | Method for rename media | *optional* | symlink
| **FILEBOT_LANG** | Set your language | *optional* | fr
| **FILEBOT_CONFLICT** | Conflict management | *optional* | skip

### Volumes

 - `/downloads` : folder for download torrents
 - `/config` : folder for rtorrent and rutorrent configuration

#### Data folder tree

 - `/downloads/.watch` : rtorrent watch directory
 - `/downloads/` : rtorrent download torrent here
 - `/downloads/media` : organize your media and create a symlink with filebot
 - `/config/.session` : rtorrent save statement here
 - `/config/rtorrent` : path of .rtorrent.rc
 - `/config/rutorrent/conf` : global configuration of rutorrent
 - `/config/rutorrent/share` : rutorrent user configuration and cache
 - `/config/custom_plugins` : add your own plugins
 - `/config/custom_themes` : add your own themes
 - `/config/filebot` : add your License file in this folder
 - `/config/filebot/args_amc.txt` : configuration of fn:amc script of filebot
 - `/config/filebot/postdl` : modify postdl script, example [here](https://github.com/ivanshift/docker-rutorrent/blob/master/rootfs/usr/local/bin/postdl)

### Ports

 - 8080
 - PORT_RTORRENT (default: 45000)

## Usage

### Simple launch

```sh
docker run --name rutorrent -dt \
  -e UID=1000 \
  -e GID=1000 \
  -p 8080:8080 \
  -p 45000:45000 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/downloads:/downloads \
  ivanshift/rutorrent:latest
```

URL: http://xx.xx.xx.xx:8080

### Advanced launch

Add custom plugin :

```sh
mkdir -p /mnt/docker/rutorrent/config/custom_plugins
git clone https://github.com/Gyran/rutorrent-ratiocolor.git /mnt/docker/rutorrent/config/custom_plugins/ratiocolor
```

Add custom theme :

Donwload a theme for example in this repository https://github.com/artyuum/3rd-party-ruTorrent-Themes.git  
And copy the folder in `/mnt/docker/rutorrent/config/custom_themes`

Run container :

```sh
docker run --name rutorrent -dt \
  -e UID=1000 \
  -e GID=1000 \
  -e DHT_RTORRENT=on \
  -e PORT_RTORRENT=6881 \
  -e FILEBOT_LICENSE=/config/filebot/FileBot_License_XXXXXXXXX.psm \
  -e FILEBOT_RENAME_METHOD=move \
  -p 9080:8080 \
  -p 6881:6881 \
  -p 6881:6881/udp \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/downloads:/downloads \
  ivanshift/rutorrent:filebot
```

URL: http://xx.xx.xx.xx:9080

### Add HTTP authentication

```sh
docker run --name rutorrent -dt \
  -e UID=1000 \
  -e GID=1000 \
  -e PORT_RTORRENT=46000 \
  -e HTTP_AUTH=true \
  -p 8080:8080 \
  -p 46000:46000 \
  -v /mnt/docker/rutorrent/config:/config \
  -v /mnt/docker/rutorrent/downloads:/downloads \
  ivanshift/rutorrent:latest
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

## License

Docker image [ivanshift/rutorrent](https://hub.docker.com/r/ivanshift/rutorrent) is released under [MIT License](https://github.com/ivanshiftr/docker-rutorrent/blob/master/LICENSE).
