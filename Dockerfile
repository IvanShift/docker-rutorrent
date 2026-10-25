# syntax=docker/dockerfile:1.28@sha256:bb22d9815c728170f72750f4e5b0d672e06176142e1d602c7e66c050100b7e5b
#
# ------------------------------ Global Build Arguments ------------------------------
# Centralize all version pins and feature toggles here.

# --- Alpine base ---
ARG ALPINE_VERSION=3.24.3
ARG ALPINE_DIGEST=sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a

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

# Alpine now provides this curl/libcurl release with c-ares and HTTP/2.
# Keep a minimum version so a stale mirror cannot restore the older release.
ARG CURL_VERSION=8.23.0

# Alpine provides FFmpeg 8.1.3, built against libvpx.so.11. Keep this build
# against local libvpx.so.12 until Alpine also provides matching libvpx.
# Stay on the 8.1 branch: 9.x changes library majors and features.
ARG FFMPEG_VERSION=8.1.3
ARG FFMPEG_SHA512=8c667090bcdc70796caaa8cdc2cf82e57d7c7f6feedcb0ec7251cefd6a0c63b7cad203a86c800d3e2f69ca91255167aa4e631701060b96d576d1a9666ca7aafe

# Library backports retain Alpine package ownership. libvpx changes its SONAME,
# so FFmpeg is compiled against the matching local development package below.
ARG LIBVPX_VERSION=1.17.0
ARG LIBVPX_SHA512=be98d1b913575e538fefefe657c9944df85118fe2d0558361495a72c75ac6a2251048cb645fca49e60d6941c823a923f31d4ebc4de9b778008c267c42805b339
ARG LIBXML2_VERSION=2.13.9
ARG LIBXML2_SHA512=62d4813860124c969f204aaf33b497105dbc32a6c5655f5a86168743660e10987d687d7e5e7ee49fdfdeb8f6ad9fa4503f81fcce2e4d459094895f02436d1b13
ARG OPENJDK21_VERSION=21.0.13.0.2_p1
ARG OPENJDK21_SHA512=3ab22e3429fa375c72b08346c32b950110de14e8959ef7f83c497e3678a73693aa01bff8aa43b98f3f684918d6c702df3669c2d6ce63f341b0b9f75a0d5d49fa

# libtorrent master at 05f20adc, still version 0.16.26. Keep paired with
# rTorrent 439c6e84: its SCGI code requires the newer Event descriptor API.
ARG LIBTORRENT_BRANCH=v0.16.26
ARG LIBTORRENT_REF=
ARG LIBTORRENT_VERSION=05f20adc2b47aab7d6fd3e30fef1a8f11a26e97a

# The default source is the IvanShift rTorrent fork: upstream master (550b5c88)
# plus stop claims, shared native queues and the erasedata re-add fence used by
# ruTorrent. v0.16.26 is the compatibility mode and CI label; it does not select
# the source.
ARG RTORRENT_BRANCH=v0.16.26
ARG RTORRENT_REPO=https://github.com/IvanShift/rtorrent.git
ARG RTORRENT_REF=
ARG RTORRENT_VERSION=439c6e8472d3a0fc9e34c7085e87c443ffd743d6

# --- Final image options ---
ARG FILEBOT=false
ARG FILEBOT_VER=5.3.0
ARG FILEBOT_SHA256=633d798a917607051b4f61be37b5ad923457050708c7e7eea99c09957583b575
ARG RUTORRENT_REPO=https://github.com/IvanShift/ruTorrent.git
ARG RUTORRENT_REF=999613f90f0f39acf03621d5aacdb8c8da6c8340
# Mutable remote refs require an independent expected commit.
ARG RUTORRENT_COMMIT_SHA=${RUTORRENT_REF}

# --- Build-time ruTorrent plugins ---
ARG GEOIP2_REPO=https://github.com/Micdu70/geoip2-rutorrent.git
ARG GEOIP2_REF=cad8a11b47f02ff75358b7bd9c4137648f5fedd0
ARG RATIOCOLOR_REPO=https://github.com/Micdu70/rutorrent-ratiocolor.git
ARG RATIOCOLOR_REF=4aec1988be1e09b44799b71ed4a25751c695a6f2

# --- GeoIP2 country database ---
ARG GEOIP2_DB_VERSION=2026.10.25
ARG GEOIP2_DB_SHA256=4d3587f2f39e8d5dced147bfcaa2a13b1655938930ed020e25929e91e1303fc8
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

# ============================ UnZip security backport ================================
# Stable Alpine still lacks Debian's three 2026 UnZip fixes. Keep Alpine's
# existing patches and ABI, adding only those reviewed fixes to the 6.0 package.
FROM apk-builder AS unzip-builder

COPY packaging/unzip/ /src/unzip/
WORKDIR /src/unzip
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/unzip-6.0-r17.apk /out/

# ============================ Library security backports =============================
FROM apk-builder AS libxml2-builder
ARG LIBXML2_VERSION
ARG LIBXML2_SHA512
COPY packaging/libxml2/ /src/libxml2/
WORKDIR /src/libxml2
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/libxml2-${LIBXML2_VERSION}-r3.apk /out/

FROM apk-builder AS libvpx-builder
ARG LIBVPX_VERSION
ARG LIBVPX_SHA512
COPY packaging/libvpx/ /src/libvpx/
WORKDIR /src/libvpx
RUN apk update \
   && abuild -F -r \
   && cp /packages/src/*/libvpx-${LIBVPX_VERSION}-r0.apk /out/ \
   && mkdir /build-deps \
   && cp /packages/src/*/libvpx-dev-${LIBVPX_VERSION}-r0.apk /build-deps/

# Java belongs only to FileBot. Build the stable-ABI security update locally
# while Alpine has not published it, using the same APK owner and signing key.
FROM apk-builder AS openjdk21-builder
ARG FILEBOT
ARG OPENJDK21_VERSION
ARG OPENJDK21_SHA512
COPY packaging/openjdk21/ /src/openjdk21/
WORKDIR /src/openjdk21
RUN mkdir -p /out \
   && if [ "${FILEBOT}" = true ]; then \
        apk update \
        && JOBS=8 abuild -F -r \
        && cp /packages/src/*/openjdk21-jre-headless-${OPENJDK21_VERSION}-r1.apk /out/ \
        && rm -rf /src/openjdk21/src /src/openjdk21/pkg; \
      fi

# =========================== FFmpeg stable-Alpine APKs ===============================
# Alpine v3.24 ships FFmpeg 8.1.3 against libvpx.so.11; rebuild it for our .so.12.
# The recipe keeps Alpine's configure flags, makedepends, patches and split packages.
# Install all seven split libraries with the CLI so apk cannot select repository
# libraries built against the older libvpx ABI.
FROM apk-builder AS ffmpeg-builder

ARG FFMPEG_VERSION
ARG FFMPEG_SHA512
ARG LIBVPX_VERSION

COPY --from=libvpx-builder /out/ /libvpx-apks/
COPY --from=libvpx-builder /build-deps/ /libvpx-apks/
COPY packaging/ffmpeg/ /src/ffmpeg/
WORKDIR /src/ffmpeg
RUN apk update \
   && apk add --no-cache /libvpx-apks/libvpx-${LIBVPX_VERSION}-r0.apk /libvpx-apks/libvpx-dev-${LIBVPX_VERSION}-r0.apk \
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
COPY --from=unzip-builder /out/ /
COPY --from=libxml2-builder /out/ /
COPY --from=libvpx-builder /out/ /
COPY --from=openjdk21-builder /out/ /
COPY --from=ffmpeg-builder /out/ /

# ============================== Final runtime =======================================
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS runtime

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in runtime stage
ARG RTORRENT_BRANCH
ARG MEDIAINFO_VERSION
ARG CURL_VERSION
ARG FFMPEG_VERSION
ARG LIBXML2_VERSION
ARG LIBVPX_VERSION
ARG OPENJDK21_VERSION

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
   "curl>=${CURL_VERSION}-r0" \
   libcrypto3 \
   "libcurl>=${CURL_VERSION}-r0" \
   /local-apks/libxml2-${LIBXML2_VERSION}-r3.apk \
   /local-apks/libvpx-${LIBVPX_VERSION}-r0.apk \
   libgcc \
   libncursesw \
   libstdc++ \
   # Alpine now ships the fixed stable nginx; refuse older mirrors.
   'nginx>=1.30.6-r0' \
   openssl \
   # --- Full PHP modules for ruTorrent & plugins ---
   # 8.5.12 fixes CVE-2026-109618 and CVE-2026-109619 in CLI and FPM.
   'php85>=8.5.12-r0' \
   'php85-bcmath>=8.5.12-r0' \
   'php85-ctype>=8.5.12-r0' \
   'php85-curl>=8.5.12-r0' \
   'php85-dom>=8.5.12-r0' \
   'php85-fpm>=8.5.12-r0' \
   'php85-fileinfo>=8.5.12-r0' \
   'php85-mbstring>=8.5.12-r0' \
   'php85-openssl>=8.5.12-r0' \
   'php85-pcntl>=8.5.12-r0' \
   'php85-phar>=8.5.12-r0' \
   'php85-posix>=8.5.12-r0' \
   'php85-session>=8.5.12-r0' \
   'php85-simplexml>=8.5.12-r0' \
   'php85-tokenizer>=8.5.12-r0' \
   'php85-xml>=8.5.12-r0' \
   'php85-zip>=8.5.12-r0' \
   # --- End PHP modules ---
   shadow \
   su-exec \
   s6 \
   /local-apks/unzip-6.0-r17.apk \
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
   # Refuse a mixed FFmpeg release; local packages also share the libvpx ABI.
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
RUN --mount=type=bind,from=local-apks,source=/,target=/local-apks \
   case "${FILEBOT}" in true|false) ;; *) echo 'FILEBOT must be true or false' >&2; exit 64 ;; esac \
   && if [ "${FILEBOT}" = true ]; then \
   apk add --no-cache --keys-dir /local-apks/keys \
   chromaprint \
   findutils \
   /local-apks/openjdk21-jre-headless-${OPENJDK21_VERSION}-r1.apk \
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
