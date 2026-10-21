# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
#
# ------------------------------ Global Build Arguments ------------------------------
# Centralize all version pins and feature toggles here.

# --- Alpine base ---
ARG ALPINE_VERSION=3.24.2
ARG ALPINE_DIGEST=sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6

# --- Component versions ---
ARG MKTORRENT_VERSION=v1.1
ARG MKTORRENT_COMMIT=b20ef699b4ee5ded2f078ead776c7deac969e19a
ARG DUMP_TORRENT_VERSION=v1.7.0
ARG DUMP_TORRENT_COMMIT=ddf988d3099637c93dc0247854ca711c0a2a0289
# RARLab's archive suffix 7.2.7 contains sources that identify as UnRAR 7.23.
ARG UNRAR_VERSION=7.2.7
ARG UNRAR_SHA256=01d903a7dcf413cb2925696d7796e48e38d471f79bfe7ef3ad2aebf6c12dbefd

# MediaInfo is backported as local APKs against the same stable Alpine base.
ARG MEDIAINFO_VERSION=26.10
ARG MEDIAINFO_SHA512=6b0f7b2362ff15167139f4f678a14e0d1686c4bf60f86e0d8e5023c78866a909f3800f313e01cff0ec765fbf9276d78e388ed711cc5e1ac50b484a13da19f97b
ARG LIBMEDIAINFO_SHA512=5b155195d92c098c05ea7e9bd5af884350186367297535a3d533a6657bb7a1f0a307d585a10e92caaca0a0d16815c90525b9bb9de9bfc373d44f89cad7ee8c6a

# curl/libcurl local APK backport: Alpine v3.24 still ships 8.22.0. Remove the backport
# (packaging/curl, curl-builder, these ARGs) once v3.24 provides >= CURL_VERSION.
ARG CURL_VERSION=8.23.0
ARG CURL_SHA512=69cf0feafdc79fbad007cf2fc977b777ce6ccb05d01afd17b5a42f8bedbfcc342fc3a48193faa7f3eafe6800cd7d07948916e8733db1b9de60287ed06219b840

# nginx stable local core-only APK backport: Alpine v3.24 still ships 1.30.4. Take only
# stable (even minor) releases; remove the backport (packaging/nginx, nginx-builder,
# these ARGs) once v3.24 provides >= NGINX_VERSION.
ARG NGINX_VERSION=1.30.6
ARG NGINX_SHA512=1b04fcb5a7ee3df573b1a0d792cf1f1cfc02d30d03592f2f04b2b2842d9f42364957f4420e53af380b9fffd5eb4a3a5c11f6d71643168db25baeea748cf94fa2

# FFmpeg 8.1 point-release backport: Alpine v3.24 still ships 8.1.2-r0. Remove the
# backport (packaging/ffmpeg, ffmpeg-builder, these ARGs) once v3.24 provides
# >= FFMPEG_VERSION. Stay on the 8.1 branch: 9.x changes library majors and features.
ARG FFMPEG_VERSION=8.1.3
ARG FFMPEG_SHA512=acc7dbacde58c91bbc4a86fa589d317515d40bc9bc1ca5f9c020034bb54bf3c8cd61ed70ce437b962bd50332ea8378b3ad52da1942cfdb29e380c33a90d2f0c1

# libtorrent master a755a7a (2026-10-11) still identifies as 0.16.25. It has the
# non-cascading log groups ("errors", "events", "debug_trace") and the log API
# without a subsystem argument that rTorrent upstream master 3c7bb87f requires.
ARG LIBTORRENT_BRANCH=v0.16.25
ARG LIBTORRENT_REF=
ARG LIBTORRENT_VERSION=a755a7ae5fc3b25860198c5ca620f8455e114376

# The default source is the IvanShift rTorrent fork: upstream master (3c7bb87f)
# plus stop claims, shared native queues and the erasedata re-add fence used by
# ruTorrent. v0.16.25 is the compatibility mode and CI label; it does not select
# the source.
ARG RTORRENT_BRANCH=v0.16.25
ARG RTORRENT_REPO=https://github.com/IvanShift/rtorrent.git
ARG RTORRENT_REF=
ARG RTORRENT_VERSION=56194a38ab5aa27dddc1d9f31106bce9bdd7ac8a

# --- Final image options ---
ARG FILEBOT=false
ARG FILEBOT_VER=5.3.0
ARG FILEBOT_SHA256=633d798a917607051b4f61be37b5ad923457050708c7e7eea99c09957583b575
ARG RUTORRENT_REPO=https://github.com/IvanShift/ruTorrent.git
ARG RUTORRENT_REF=5c587ef2fea8bfb5113b174e57c85506309ff6df
# Mutable remote refs require an independent expected commit.
ARG RUTORRENT_COMMIT_SHA=${RUTORRENT_REF}

# --- Build-time ruTorrent plugins ---
ARG GEOIP2_REPO=https://github.com/Micdu70/geoip2-rutorrent.git
ARG GEOIP2_REF=cad8a11b47f02ff75358b7bd9c4137648f5fedd0
ARG RATIOCOLOR_REPO=https://github.com/Micdu70/rutorrent-ratiocolor.git
ARG RATIOCOLOR_REF=4aec1988be1e09b44799b71ed4a25751c695a6f2

# --- GeoIP2 country database ---
ARG GEOIP2_DB_VERSION=2026.10.19
ARG GEOIP2_DB_SHA256=55fb913b4a5ab69fb0bbe11f86242b8a3f64061205227f4c1efceef7e14e5dbc
# Optional controlled mirror; the expected SHA256 remains mandatory.
ARG GEOIP2_DB_URL=

# --- Build options ---
ARG STRICT_WERROR=true

# --- Build metadata (pass via --build-arg) ---
ARG BUILD_DATE
ARG VCS_REF

# Optional commit pins for ruTorrent plugins
ARG GEOIP2_COMMIT_SHA=
ARG RATIOCOLOR_COMMIT_SHA=


# =========================== Native source fetcher ==================================
# Fetch architecture-independent native sources once on the build platform. Only
# libtorrent and rTorrent share this stage: rTorrent is built against libtorrent.
FROM --platform=${BUILDPLATFORM} alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS src

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare needed args
ARG LIBTORRENT_REF
ARG LIBTORRENT_VERSION

# Install fetch tools.
RUN apk add --no-cache ca-certificates git

WORKDIR /src

# All git fetches share the same exact identity check (also tested directly in CI).
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/rakshasa/libtorrent.git "${LIBTORRENT_REF}" "${LIBTORRENT_VERSION}" /src/libtorrent

ARG RTORRENT_REPO
ARG RTORRENT_REF
ARG RTORRENT_VERSION
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source "${RTORRENT_REPO}" "${RTORRENT_REF}" "${RTORRENT_VERSION}" /src/rtorrent


# ============================ Build-time source fetchers ============================
# Fetch architecture-independent tools, plugins, data and FileBot on the build
# platform, each in its own stage so a changed pin refetches only its own input.
# Exact default refs check source identity; GeoLite upstream assets can expire and
# need refresh.
FROM --platform=${BUILDPLATFORM} alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS fetch-base

SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

RUN apk add --no-cache ca-certificates curl git

FROM fetch-base AS mktorrent-source

ARG MKTORRENT_VERSION
ARG MKTORRENT_COMMIT

RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/pobrn/mktorrent.git "${MKTORRENT_VERSION}" "${MKTORRENT_COMMIT}" /src/mktorrent

FROM fetch-base AS dumptorrent-source

ARG DUMP_TORRENT_VERSION
ARG DUMP_TORRENT_COMMIT

RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/tomcdj71/dumptorrent.git "${DUMP_TORRENT_VERSION}" "${DUMP_TORRENT_COMMIT}" /src/dump-torrent \
   && sed -i '1i #include <sys/time.h>' /src/dump-torrent/src/scrapec.c

FROM fetch-base AS unrar-source

ARG UNRAR_VERSION
ARG UNRAR_SHA256

RUN curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 -o /tmp/unrarsrc.tar.gz "https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz" \
   && printf '%s  %s\n' "${UNRAR_SHA256}" /tmp/unrarsrc.tar.gz | sha256sum -c - \
   && mkdir -p /src/unrar \
   && tar -xzf /tmp/unrarsrc.tar.gz -C /src/unrar --strip-components=1 \
   && rm -f /tmp/unrarsrc.tar.gz

FROM fetch-base AS geoip2-source

ARG GEOIP2_REPO
ARG GEOIP2_REF
ARG GEOIP2_COMMIT_SHA

RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   expected="${GEOIP2_COMMIT_SHA:-${GEOIP2_REF}}" \
   && sh /fetch-source "${GEOIP2_REPO}" "${GEOIP2_REF}" "$expected" /plugin \
   && test -r /plugin/plugin.info \
   && test -r /plugin/init.js \
   && test -r /plugin/init.php \
   && test -r /plugin/geoip2.phar \
   && rm -rf /plugin/.github \
   && rm -f /plugin/README /plugin/README.md /plugin/database/GeoLite2-Country.mmdb

FROM fetch-base AS ratiocolor-source

ARG RATIOCOLOR_REPO
ARG RATIOCOLOR_REF
ARG RATIOCOLOR_COMMIT_SHA

RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   expected="${RATIOCOLOR_COMMIT_SHA:-${RATIOCOLOR_REF}}" \
   && sh /fetch-source "${RATIOCOLOR_REPO}" "${RATIOCOLOR_REF}" "$expected" /plugin \
   && test -r /plugin/plugin.info \
   && test -r /plugin/init.js \
   && rm -rf /plugin/.github \
   && rm -f /plugin/README /plugin/README.md

FROM fetch-base AS geoip2-db-source

ARG GEOIP2_DB_VERSION
ARG GEOIP2_DB_SHA256
ARG GEOIP2_DB_URL

RUN set -eu; \
   test -n "${GEOIP2_DB_VERSION}"; \
   test -n "${GEOIP2_DB_SHA256}"; \
   url="${GEOIP2_DB_URL:-https://github.com/P3TERX/GeoLite.mmdb/releases/download/${GEOIP2_DB_VERSION}/GeoLite2-Country.mmdb}"; \
   mkdir -p /database; \
   curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 -o /database/GeoLite2-Country.mmdb "${url}"; \
   printf '%s  %s\n' "${GEOIP2_DB_SHA256}" /database/GeoLite2-Country.mmdb | sha256sum -c -; \
   printf '%s\n' \
      'GeoLite2-Country.mmdb upstream: https://github.com/P3TERX/GeoLite.mmdb' \
      "Release: ${GEOIP2_DB_VERSION}" \
      'Database and Contents Copyright (c) MaxMind, Inc.' \
      'GeoLite2 EULA: https://www.maxmind.com/en/geolite2/eula' \
      'GeoNames data: https://creativecommons.org/licenses/by/4.0/' \
      > /database/NOTICE

# The architecture-independent FileBot payload is fetched, checked, unpacked and
# pruned here so the runtime stage, rebuilt by CI on every run, only copies it.
# FILEBOT=false leaves /payload empty: nothing is downloaded and no /filebot exists.
FROM fetch-base AS filebot-payload

ARG FILEBOT
ARG FILEBOT_VER
ARG FILEBOT_SHA256

RUN mkdir /payload \
   && if [ "${FILEBOT}" = true ]; then \
   mkdir /filebot \
   && cd /filebot \
   && curl -fsSL --retry 3 --retry-max-time 600 --connect-timeout 15 --max-time 180 -o /filebot/filebot.tar.xz "https://get.filebot.net/filebot/FileBot_${FILEBOT_VER}/FileBot_${FILEBOT_VER}-portable.tar.xz" \
   && printf '%s  %s\n' "${FILEBOT_SHA256}" /filebot/filebot.tar.xz | sha256sum -c - \
   && tar -xJf /filebot/filebot.tar.xz \
   && rm -f /filebot/filebot.tar.xz \
   && sed -i 's/-Dapplication.deployment=tar/-Dapplication.deployment=docker/g' /filebot/filebot.sh \
   && find /filebot/lib -type f -not -name 'libjnidispatch.so' -delete \
   && mv /filebot /payload/filebot ; \
   fi


# ============================ Native target builders ================================
# Compile target binaries; the fetch stages stay on BUILDPLATFORM. mktorrent,
# dumptorrent and UnRAR build in their own stages, so an rTorrent change does not
# rebuild them. BuildKit runs these stages concurrently with the libtorrent ->
# rTorrent chain and MediaInfo, which keep every CPU; each small tool is therefore
# limited to half the CPUs and at most four jobs.
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS tools-base
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
ENV DIST_PATH=/dist CC=gcc CXX=g++
RUN apk add --no-cache build-base cmake linux-headers openssl-dev

FROM tools-base AS mktorrent-build
WORKDIR /usr/local/src/mktorrent
COPY --from=mktorrent-source /src/mktorrent .
RUN --mount=type=bind,source=patches/mktorrent-cooperative-stop.patch,target=/mktorrent.patch,readonly \
   jobs=$(( $(nproc) / 2 )) \
   && jobs=$(( jobs < 1 ? 1 : jobs > 4 ? 4 : jobs )) \
   && patch -p1 < /mktorrent.patch \
   && printf 'CFLAGS = -flto -O3\nUSE_PTHREADS = 1\nUSE_OPENSSL = 1\n' >> Makefile \
   && make -j"$jobs" CC="${CC}" \
   && make DESTDIR="${DIST_PATH}" install \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/mktorrent" \
   && cp COPYING "${DIST_PATH}/usr/share/licenses/mktorrent/"

FROM tools-base AS dumptorrent-build
WORKDIR /usr/local/src/dump-torrent
COPY --from=dumptorrent-source /src/dump-torrent .
RUN jobs=$(( $(nproc) / 2 )) \
   && jobs=$(( jobs < 1 ? 1 : jobs > 4 ? 4 : jobs )) \
   && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
   && cmake --build build --target dumptorrent --parallel "$jobs" \
   && strip --strip-unneeded build/dumptorrent \
   && install -Dm755 build/dumptorrent "${DIST_PATH}/usr/local/bin/dumptorrent" \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/dumptorrent" \
   && find . -maxdepth 1 -type f \( -iname '*license*' -o -iname 'copying*' \) -exec cp {} "${DIST_PATH}/usr/share/licenses/dumptorrent/" \;

FROM tools-base AS unrar-build
WORKDIR /usr/local/src/unrar
COPY --from=unrar-source /src/unrar .
# The upstream makefile supports parallel builds.
RUN jobs=$(( $(nproc) / 2 )) \
   && jobs=$(( jobs < 1 ? 1 : jobs > 4 ? 4 : jobs )) \
   && make -f makefile -j"$jobs" \
   && install -Dm755 unrar "${DIST_PATH}/usr/local/bin/unrar" \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/unrar" \
   && cp license.txt "${DIST_PATH}/usr/share/licenses/unrar/"

FROM tools-base AS builder
ARG STRICT_WERROR
ARG RTORRENT_BRANCH

RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native validate \
   && apk add --no-cache \
      autoconf automake ca-certificates curl curl-dev \
      libtool ncurses-dev pkgconf zlib-dev \
   && if [ "${RTORRENT_BRANCH}" = v0.9.8 ]; then apk add --no-cache xmlrpc-c-dev; fi

WORKDIR /usr/local/src/libtorrent
COPY --from=src /src/libtorrent .
RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native libtorrent

WORKDIR /usr/local/src/rtorrent
COPY --from=src /src/rtorrent .
# libtorrent's development files are only needed to build rTorrent.
RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native rtorrent \
   && rm -rf "${DIST_PATH}/usr/local/include" "${DIST_PATH}/usr/local/lib/pkgconfig" \
   && find "${DIST_PATH}" -name '*.la' -delete

# One runtime layer for all compiled native programs and their license notices.
FROM scratch AS native-dist
COPY --from=builder /dist /
COPY --from=mktorrent-build /dist /
COPY --from=dumptorrent-build /dist /
COPY --from=unrar-build /dist /

# Checkout ruTorrent once, independent of target architecture; compile its helpers
# in a lightweight target stage instead of installing compilers in runtime.
FROM fetch-base AS rutorrent-source
ARG RUTORRENT_REPO
ARG RUTORRENT_REF
ARG RUTORRENT_COMMIT_SHA
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source "${RUTORRENT_REPO}" "${RUTORRENT_REF}" "${RUTORRENT_COMMIT_SHA}" /rutorrent-source

# Assemble the runtime application tree before it reaches the runtime stage, so
# removed files and mode fixes add no image bytes. Of the fork's tests only the files
# that image smoke runs in the shipped image are kept. Its PHP operator tools ship;
# the host-side development scripts tools/matrix.sh and tools/rt-lab.sh, which need
# a Git checkout, Docker, Python and the whole test tree, do not. The helper build
# below keeps reading the unmodified checkout.
FROM fetch-base AS rutorrent-app
COPY --from=rutorrent-source /rutorrent-source /export/rutorrent/app
RUN --mount=type=bind,source=scripts/prune-rutorrent-tests.sh,target=/prune-rutorrent-tests,readonly \
   --mount=type=bind,source=scripts/rutorrent-image-tests.list,target=/rutorrent-image-tests.list,readonly \
   mkdir -p /export/usr/share/build-info /export/usr/share/licenses/rutorrent \
   && cp /export/rutorrent/app/.source-revision /export/usr/share/build-info/rutorrent-revision \
   && cp /export/rutorrent/app/LICENSE.md /export/usr/share/licenses/rutorrent/ \
   && rm -rf /export/rutorrent/app/plugins/geoip /export/rutorrent/app/plugins/_cloudflare \
   && find /export/rutorrent/app -type d -name .github -prune -exec rm -rf {} + \
   && find /export/rutorrent/app -type f ! -name '*LICENSE*' \( -name '*.md' -o -name 'README*' \) -delete \
   && rm -f /export/rutorrent/app/tools/matrix.sh /export/rutorrent/app/tools/rt-lab.sh \
   && sh /prune-rutorrent-tests /export/rutorrent/app /rutorrent-image-tests.list \
   && cp -a /export/rutorrent/app/conf /export/rutorrent/conf-dist

# Install deterministic third-party plugins after cleaning the ruTorrent checkout so
# their required files and license notices are not removed by the generic cleanup above.
COPY --from=geoip2-source /plugin/ /export/rutorrent/app/plugins/geoip2/
COPY --from=ratiocolor-source /plugin/ /export/rutorrent/app/plugins/ratiocolor/
COPY --from=geoip2-db-source /database/GeoLite2-Country.mmdb /export/rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb
COPY --from=geoip2-db-source /database/NOTICE /export/usr/share/licenses/GeoLite2/NOTICE
RUN --mount=type=bind,source=scripts/normalize-immutable-tree.sh,target=/normalize-immutable-tree,readonly \
   sh /normalize-immutable-tree /export/rutorrent/app /export/rutorrent/conf-dist

FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS rutorrent-helpers
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
RUN apk add --no-cache build-base linux-headers
COPY --from=rutorrent-source /rutorrent-source /source
RUN --mount=type=bind,source=scripts/build-rutorrent-helpers.sh,target=/build-helpers,readonly \
   sh /build-helpers /source /helpers

# Docker-owned filesystem/supervisor helpers are independent from the app ABI. The
# compile sees only the libexec C sources, so a change to the PHP and shell workers
# beside them keeps its cache; build-docker-helpers.sh names the sources it compiles.
FROM scratch AS docker-helper-sources
COPY rootfs/usr/local/libexec/*.c /

FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS docker-helpers
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
RUN apk add --no-cache build-base linux-headers
RUN --mount=type=bind,from=docker-helper-sources,target=/sources,readonly \
   --mount=type=bind,source=scripts/build-docker-helpers.sh,target=/build-docker-helpers,readonly \
   sh /build-docker-helpers /sources /helpers

# ===================== Local stable-Alpine APK backports =============================
# One toolchain and one temporary signing key for every local APK: the builders below
# share the key, so the runtime needs a single --keys-dir. The private key stays in
# the build stages; only public keys reach the runtime. A further backport is one
# more builder stage FROM apk-builder plus one COPY into local-apks.
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS apk-builder

SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
ENV REPODEST=/packages

RUN apk add --no-cache abuild build-base \
   && abuild-keygen -a -n \
   && cp /root/.abuild/*.pub /etc/apk/keys/ \
   && mkdir -p /out/keys \
   && cp /etc/apk/keys/*.pub /out/keys/

# ========================= MediaInfo stable-Alpine APKs ==============================
FROM apk-builder AS mediainfo-builder

ARG MEDIAINFO_VERSION
ARG MEDIAINFO_SHA512
ARG LIBMEDIAINFO_SHA512

COPY packaging/libmediainfo/APKBUILD /src/libmediainfo/APKBUILD
WORKDIR /src/libmediainfo
RUN apk update \
   && abuild -F -r \
   && apk add --no-cache \
      /packages/src/*/libmediainfo-${MEDIAINFO_VERSION}-r*.apk \
      /packages/src/*/libmediainfo-dev-${MEDIAINFO_VERSION}-r*.apk

COPY packaging/mediainfo/APKBUILD /src/mediainfo/APKBUILD
WORKDIR /src/mediainfo
RUN abuild -F -r \
   && cp /packages/src/*/libmediainfo-${MEDIAINFO_VERSION}-r*.apk /out/ \
   && cp /packages/src/*/mediainfo-${MEDIAINFO_VERSION}-r*.apk /out/

# ============================ curl stable-Alpine APKs ================================
# Alpine v3.24 ships curl 8.22.0; 8.23.0 fixes the issues curl lists for 8.22.0. The
# recipe keeps the v3.24 features (OpenSSL, nghttp2, c-ares, Unix sockets) and runs
# upstream's test suite in check(). Only the runtime packages leave this stage; the
# native builder keeps Alpine's ABI-identical curl-dev.
FROM apk-builder AS curl-builder

ARG CURL_VERSION
ARG CURL_SHA512

COPY packaging/curl/ /src/curl/
WORKDIR /src/curl
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/curl-${CURL_VERSION}-r*.apk /out/ \
   && cp /packages/src/*/libcurl-${CURL_VERSION}-r*.apk /out/

# ============================ nginx stable-Alpine APK =================================
# Alpine v3.24 ships nginx 1.30.4, which lacks the security fixes of stable 1.30.5 and
# 1.30.6. The recipe builds only the core package with Alpine's static modules, install
# scripts and file layout; the image does not use the nginx-mod-* dynamic modules.
FROM apk-builder AS nginx-builder

ARG NGINX_VERSION
ARG NGINX_SHA512

COPY packaging/nginx/ /src/nginx/
WORKDIR /src/nginx
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/nginx-${NGINX_VERSION}-r*.apk /out/

# =========================== FFmpeg stable-Alpine APKs ===============================
# Alpine v3.24 ships FFmpeg 8.1.2; 8.1.3 adds the 8.1-branch bounds and overflow fixes
# for the demuxers and decoders that the screenshots plugin runs on downloaded files.
# The recipe keeps Alpine's configure flags, makedepends, patches and split packages.
# The CLI leaves with all seven split libraries it links: given only ffmpeg.apk, apk
# would take so:libav* from the repository's 8.1.2 packages.
FROM apk-builder AS ffmpeg-builder

ARG FFMPEG_VERSION
ARG FFMPEG_SHA512

COPY packaging/ffmpeg/ /src/ffmpeg/
WORKDIR /src/ffmpeg
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/ffmpeg-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libavcodec-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libavdevice-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libavfilter-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libavformat-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libavutil-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libswresample-${FFMPEG_VERSION}-r*.apk \
      /packages/src/*/ffmpeg-libswscale-${FFMPEG_VERSION}-r*.apk \
      /out/

# All local runtime APKs and their public key, mounted once by the runtime install.
FROM scratch AS local-apks
COPY --from=mediainfo-builder /out/ /
COPY --from=curl-builder /out/ /
COPY --from=nginx-builder /out/ /
COPY --from=ffmpeg-builder /out/ /

# ============================== Final runtime =======================================
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS runtime

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in runtime stage
ARG RTORRENT_BRANCH
ARG MEDIAINFO_VERSION
ARG CURL_VERSION
ARG NGINX_VERSION
ARG FFMPEG_VERSION

# -------------------------- Runtime environment variables ---------------------------
# nginx, PHP-FPM, rTorrent and plugin workers all run as UID, so ruTorrent creates
# its profile, temporary and log entries (tracker credentials included) without
# group or other access. Startup refuses a mask that drops owner rwx.
ENV UID=991 \
   GID=991 \
   PORT_RTORRENT=45000 \
   MODE_DHT=off \
   PORT_DHT=6881 \
   PEER_EXCHANGE=no \
   DOWNLOAD_DIRECTORY=/data/downloads \
   CHECK_PERM_DATA=true \
   FILEBOT_RENAME_METHOD=symlink \
   FILEBOT_LANG=en \
   FILEBOT_CONFLICT=skip \
   RU_PUBLIC_URL="" \
   RU_PROFILE_MASK=0700 \
   ENABLE_RPC2=false

# ----------------------------- Base runtime packages --------------------------------
# Keep the runtime minimal; add curl explicitly for healthcheck.
# Trust the build-local APK key only for this installation. Its private key and
# the build toolchain stay in the APK builder stages; no edge repository is enabled.
# Packages come before the build outputs, so a cached local build that changes only
# native or application sources reuses these layers.
RUN --mount=type=bind,from=local-apks,source=/,target=/local-apks \
   apk upgrade --no-cache \
   && apk add --no-cache --keys-dir /local-apks/keys \
   bash \
   ca-certificates \
   /local-apks/curl-${CURL_VERSION}-r*.apk \
   libcrypto3 \
   /local-apks/libcurl-${CURL_VERSION}-r*.apk \
   libgcc \
   libncursesw \
   libstdc++ \
   /local-apks/nginx-${NGINX_VERSION}-r*.apk \
   openssl \
   # --- Full PHP modules for ruTorrent & plugins ---
   php85 \
   php85-bcmath \
   php85-ctype \
   php85-curl \
   php85-dom \
   php85-fpm \
   php85-fileinfo \
   php85-mbstring \
   php85-openssl \
   php85-pcntl \
   php85-phar \
   php85-posix \
   php85-session \
   php85-simplexml \
   php85-tokenizer \
   php85-xml \
   php85-zip \
   # --- End PHP modules ---
   shadow \
   su-exec \
   s6 \
   unzip \
   # FFmpeg: the CLI and every split library it links, all from local-apks.
   /local-apks/ffmpeg-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libavcodec-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libavdevice-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libavfilter-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libavformat-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libavutil-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libswresample-${FFMPEG_VERSION}-r*.apk \
   /local-apks/ffmpeg-libswscale-${FFMPEG_VERSION}-r*.apk \
   /local-apks/libmediainfo-${MEDIAINFO_VERSION}-r*.apk \
   /local-apks/mediainfo-${MEDIAINFO_VERSION}-r*.apk \
   sox \
   zlib \
   # A mixed FFmpeg install (repository 8.1.2 libraries) stops the build.
   && if apk info -v 2>/dev/null | grep '^ffmpeg' | grep -v -- "-${FFMPEG_VERSION}-r"; then \
        echo "FFmpeg packages other than ${FFMPEG_VERSION} are installed" >&2; exit 1; \
      fi \
   # Create user/group and config dir
   && addgroup -S -g ${GID} torrent \
   && adduser -S -D -h /home/torrent -s /bin/sh -G torrent -u ${UID} torrent \
   && mkdir -p /config \
   && if [ "${RTORRENT_BRANCH}" = "v0.9.8" ]; then \
        apk add --no-cache xmlrpc-c; \
        touch /etc/rtorrent-legacy; \
      fi \
   && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +

# Complete optional YE chains using a cross-certificate verified against the
# already trusted ISRG Root X2. No specific host dependency was recorded when
# this compatibility file was added. Remove this
# optional compatibility certificate when Alpine trusts Root YE; skip it after
# 2032-09-02 rather than installing expired material or stopping unrelated builds.
# Verified official source: https://letsencrypt.org/certs/gen-y/root-ye-by-x2.pem
RUN --mount=type=bind,source=certs/isrg-root-ye-by-x2.crt,target=/root-ye.crt,readonly \
   --mount=type=bind,source=scripts/install-cross-certificate.sh,target=/install-cross-certificate,readonly \
   sh /install-cross-certificate /root-ye.crt /usr/local/share/ca-certificates/isrg-root-ye-by-x2.crt

# ------------------------------- FileBot (optional) ---------------------------------
# Install FileBot-only runtime dependencies when requested; filebot-payload below
# supplies the checked application.
ARG FILEBOT
RUN case "${FILEBOT}" in true|false) ;; *) echo 'FILEBOT must be true or false' >&2; exit 64 ;; esac \
   && if [ "${FILEBOT}" = true ]; then \
   apk add --no-cache \
   chromaprint \
   findutils \
   openjdk21-jre-headless \
   && if apk info -v 2>/dev/null | grep '^ffmpeg' | grep -v -- "-${FFMPEG_VERSION}-r"; then \
        echo "FFmpeg packages other than ${FFMPEG_VERSION} are installed" >&2; exit 1; \
      fi ; \
   fi

# Bring compiled artifacts from the native builders
COPY --from=native-dist / /

# ------------------------------- ruTorrent install ----------------------------------
COPY --from=rutorrent-helpers /helpers/ /
COPY --from=docker-helpers /helpers/ /
COPY --from=rutorrent-app /export/ /

# Check the assembled tree with the target PHP: database checksum, syntax in bounded
# lint batches (one interpreter per file dominated this step) and a GeoLite2 lookup.
ARG GEOIP2_DB_SHA256
RUN printf '%s  %s\n' "${GEOIP2_DB_SHA256}" /rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb | sha256sum -c - \
   && (find /rutorrent/app -type f -name '*.php' -print0 | xargs -0 -n100 php85 -l >/dev/null) \
   && php85 -r 'require "/rutorrent/app/plugins/geoip2/geoip2.phar"; $reader = new GeoIp2\Database\Reader("/rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb"); $country = $reader->country("8.8.8.8")->country->isoCode; if ($country !== "US") { fwrite(STDERR, "Unexpected GeoIP country: ".$country."\n"); exit(1); } echo "GeoIP2 lookup OK\n";' \
   && if [ -f /rutorrent/app/tools/update-asset-version.php ]; then php85 -n /rutorrent/app/tools/update-asset-version.php; fi \
   && mkdir -p /run/rtorrent /run/nginx /run/php

COPY --from=filebot-payload /payload/ /

# ----------------------------- Local configs & scripts -------------------------------
# docker-helpers compiles the libexec C sources from the build context; the runtime
# keeps only the binaries. Without FileBot the image keeps neither /filebot nor the
# filebot-queue service.
# Copied build outputs get the same setuid/setgid removal as the packages.
COPY rootfs /
RUN --mount=type=bind,source=rootfs,target=/image-rootfs,readonly \
   --mount=type=bind,source=scripts/normalize-rootfs-permissions.sh,target=/normalize-rootfs,readonly \
   sh /normalize-rootfs /image-rootfs \
   && rm /usr/local/libexec/*.c \
   && /usr/local/libexec/native-actor-paths normalize \
   && php85 /etc/nginx/generate-cli-only.php /rutorrent/app /etc/nginx/cli-only.conf \
   && rm /etc/nginx/generate-cli-only.php \
   && mkdir -p /usr/local/share/rutorrent-defaults \
   && cp -p /home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc \
   && if [ -f /filebot/args_amc.txt ]; then cp -p /filebot/args_amc.txt /usr/local/share/rutorrent-defaults/args_amc.txt; fi \
   && if [ "${FILEBOT}" != true ]; then rm -r /filebot /etc/s6.d/filebot-queue; fi \
   && chmod -R go-w /usr/local/share/rutorrent-defaults \
   && find / -xdev -type f \( -perm -4000 -o -perm -2000 \) -exec chmod a-s {} +

# --- OCI Labels ---
# Keep volatile metadata after expensive installation layers to preserve their caches.
ARG BUILD_DATE
ARG VCS_REF
ARG RTORRENT_VERSION
ARG RUTORRENT_REF
ARG RUTORRENT_COMMIT_SHA
ARG LIBTORRENT_BRANCH
ARG LIBTORRENT_VERSION
ARG GEOIP2_DB_VERSION
ARG GEOIP2_DB_SHA256
# As in earlier published images, licenses names this repository's packaging
# license. Components built from source here keep their notices under
# /usr/share/licenses; Alpine packages carry their license in the apk database.
LABEL org.opencontainers.image.title="ruTorrent on Alpine" \
   org.opencontainers.image.version="${RUTORRENT_COMMIT_SHA}" \
   org.opencontainers.image.revision="${VCS_REF}" \
   org.opencontainers.image.created="${BUILD_DATE}" \
   org.opencontainers.image.source="https://github.com/IvanShift/docker-rutorrent" \
   org.opencontainers.image.url="https://github.com/IvanShift/docker-rutorrent" \
   org.opencontainers.image.licenses="MIT" \
   io.github.ivanshift.rtorrent.revision="${RTORRENT_VERSION}" \
   io.github.ivanshift.rtorrent.version="${RTORRENT_BRANCH}" \
   io.github.ivanshift.libtorrent.revision="${LIBTORRENT_VERSION}" \
   io.github.ivanshift.libtorrent.version="${LIBTORRENT_BRANCH}" \
   io.github.ivanshift.geolite.version="${GEOIP2_DB_VERSION}" \
   io.github.ivanshift.geolite.sha256="${GEOIP2_DB_SHA256}" \
   io.github.ivanshift.rutorrent.revision="${RUTORRENT_COMMIT_SHA}" \
   org.opencontainers.image.description="rTorrent + ruTorrent built from source on Alpine" \
   maintainer="IvanShift"

# ------------------------------- Volumes & Ports ------------------------------------
VOLUME /data /config

# Expose HTTP, TCP peer connections and optional UDP DHT; uTP is unavailable.
EXPOSE 8080 45000/tcp 6881/udp

# ------------------------------ Healthcheck & Entrypoint -----------------------------
# Bound nginx/PHP/config and an actual rTorrent SCGI reply; allow large sessions
# ten minutes to load before repeated failures affect Docker's health state.
HEALTHCHECK --interval=60s --timeout=5s --start-period=10m --retries=3 \
   CMD /usr/local/bin/rutorrent-healthcheck

# The wrapper isolates the supervisor session from terminal group signals.
ENTRYPOINT ["/usr/local/bin/startup"]
CMD ["/usr/local/libexec/service-runtime", "supervise", "/etc/s6.d"]
