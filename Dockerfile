# syntax=docker/dockerfile:1.27@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e
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
ARG MEDIAINFO_VERSION=26.05
ARG MEDIAINFO_SHA512=219ca43d78dc84c4f66f9a5140705470a2555d879de26857e96080e47120b156ca56f1b22de6459fe0ea74d4a8b2ae75c9ffc8e55501f8982e7d5b41de0ee75c
ARG LIBMEDIAINFO_SHA512=8eef2b1214d2b512c8d4483f4a8a967cd05f065cb7dd0ec9ac09b2989a2bce0f60a897a7b154e90f2d6f9e3b53ac8bebe7e9c577dfe2afdb37686c027b3a97a7

# libtorrent v0.16.25
ARG LIBTORRENT_BRANCH=v0.16.25
ARG LIBTORRENT_VERSION=6ad3fecf3e7e7aa86855445587b532a91256f344

# The default source is the IvanShift rTorrent fork: upstream 0.16.25 (e652a3cb)
# plus the native stop-claim and retracker migration commands that ruTorrent's
# retrackers, datadir and autotools Move require. v0.16.25 is the
# compatibility mode and CI label; it does not select the source.
ARG RTORRENT_BRANCH=v0.16.25
ARG RTORRENT_REPO=https://github.com/IvanShift/rtorrent.git
ARG RTORRENT_REF=
ARG RTORRENT_VERSION=d883db88bee69b5d9558ed57e337ef05e2e9f3a8

# --- Final image options ---
ARG FILEBOT=false
ARG FILEBOT_VER=5.3.0
ARG FILEBOT_SHA256=633d798a917607051b4f61be37b5ad923457050708c7e7eea99c09957583b575
ARG RUTORRENT_REPO=https://github.com/IvanShift/ruTorrent.git
ARG RUTORRENT_REF=8d587100f9b4ac282d5f86aadeec3e3d08b1359b
# Mutable remote refs require an independent expected commit.
ARG RUTORRENT_COMMIT_SHA=${RUTORRENT_REF}

# --- Build-time ruTorrent plugins ---
ARG GEOIP2_REPO=https://github.com/Micdu70/geoip2-rutorrent.git
ARG GEOIP2_REF=cad8a11b47f02ff75358b7bd9c4137648f5fedd0
ARG RATIOCOLOR_REPO=https://github.com/Micdu70/rutorrent-ratiocolor.git
ARG RATIOCOLOR_REF=4aec1988be1e09b44799b71ed4a25751c695a6f2

# --- GeoIP2 country database ---
ARG GEOIP2_DB_VERSION=2026.10.04
ARG GEOIP2_DB_SHA256=025e035d484210f8af1b0f0c578d05f389ad104e79805d474bd7eeeba6b8f0c8
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
# Fetch architecture-independent native sources once on the build platform.
FROM --platform=${BUILDPLATFORM} alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS src

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare needed args
ARG LIBTORRENT_BRANCH
ARG LIBTORRENT_VERSION

# Install fetch tools. BusyBox provides the sed used below.
RUN apk add --no-cache ca-certificates git

WORKDIR /src

# All git fetches share the same exact identity check (also tested directly in CI).
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/rakshasa/libtorrent.git "${LIBTORRENT_BRANCH}" "${LIBTORRENT_VERSION}" /src/libtorrent

ARG RTORRENT_REPO
ARG RTORRENT_REF
ARG RTORRENT_VERSION
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source "${RTORRENT_REPO}" "${RTORRENT_REF}" "${RTORRENT_VERSION}" /src/rtorrent

ARG MKTORRENT_VERSION
ARG MKTORRENT_COMMIT
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/pobrn/mktorrent.git "${MKTORRENT_VERSION}" "${MKTORRENT_COMMIT}" /src/mktorrent

ARG DUMP_TORRENT_VERSION
ARG DUMP_TORRENT_COMMIT
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source https://github.com/tomcdj71/dumptorrent.git "${DUMP_TORRENT_VERSION}" "${DUMP_TORRENT_COMMIT}" /src/dump-torrent \
   && sed -i '1i #include <sys/time.h>' /src/dump-torrent/src/scrapec.c


# =========================== Build-time plugin sources ==============================
# Fetch architecture-independent plugins and data on the build platform. Exact default
# refs check source identity; GeoLite upstream assets can expire and need refresh.
FROM --platform=${BUILDPLATFORM} alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS plugin-source-base

SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

RUN apk add --no-cache ca-certificates curl git

FROM plugin-source-base AS geoip2-source

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

FROM plugin-source-base AS ratiocolor-source

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

FROM plugin-source-base AS geoip2-db-source

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


# ============================ Native target builder =================================
# Compile target binaries; the fetch stages stay on BUILDPLATFORM.
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS builder
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
ARG STRICT_WERROR
ARG RTORRENT_BRANCH
ENV DIST_PATH=/dist CC=gcc CXX=g++

RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native validate \
   && apk add --no-cache \
      autoconf automake build-base ca-certificates cmake curl curl-dev \
      libtool linux-headers ncurses-dev openssl-dev pkgconf zlib-dev \
   && if [ "${RTORRENT_BRANCH}" = v0.9.8 ]; then apk add --no-cache xmlrpc-c-dev; fi

WORKDIR /usr/local/src/libtorrent
COPY --from=src /src/libtorrent .
RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native libtorrent

WORKDIR /usr/local/src/rtorrent
COPY --from=src /src/rtorrent .
RUN --mount=type=bind,source=scripts/build-native-components.sh,target=/build-native,readonly \
   sh /build-native rtorrent

WORKDIR /usr/local/src/mktorrent
COPY --from=src /src/mktorrent .
RUN --mount=type=bind,source=patches/mktorrent-cooperative-stop.patch,target=/mktorrent.patch,readonly \
   patch -p1 < /mktorrent.patch \
   && printf 'CFLAGS = -flto -O3\nUSE_PTHREADS = 1\nUSE_OPENSSL = 1\n' >> Makefile \
   && make -j"$(nproc)" CC="${CC}" \
   && make DESTDIR="${DIST_PATH}" install -j"$(nproc)" \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/mktorrent" \
   && cp COPYING "${DIST_PATH}/usr/share/licenses/mktorrent/"

WORKDIR /usr/local/src/dump-torrent
COPY --from=src /src/dump-torrent .
RUN cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
   && cmake --build build --target dumptorrent --parallel "$(nproc)" \
   && strip --strip-unneeded build/dumptorrent \
   && install -Dm755 build/dumptorrent "${DIST_PATH}/usr/local/bin/dumptorrent" \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/dumptorrent" \
   && find . -maxdepth 1 -type f \( -iname '*license*' -o -iname 'copying*' \) -exec cp {} "${DIST_PATH}/usr/share/licenses/dumptorrent/" \;

WORKDIR /usr/local/src/unrar
ARG UNRAR_VERSION
ARG UNRAR_SHA256
RUN curl -fsSL --retry 3 --connect-timeout 15 --max-time 180 -o /tmp/unrarsrc.tar.gz "https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz" \
   && printf '%s  %s\n' "${UNRAR_SHA256}" /tmp/unrarsrc.tar.gz | sha256sum -c - \
   && tar -xzf /tmp/unrarsrc.tar.gz --strip-components=1 \
   && rm -f /tmp/unrarsrc.tar.gz \
   && make -f makefile \
   && install -m 755 unrar "${DIST_PATH}/usr/local/bin/unrar" \
   && mkdir -p "${DIST_PATH}/usr/share/licenses/unrar" \
   && cp license.txt "${DIST_PATH}/usr/share/licenses/unrar/" \
   && rm -rf "${DIST_PATH}/usr/local/include" "${DIST_PATH}/usr/local/lib/pkgconfig" \
   && find "${DIST_PATH}" -name '*.la' -delete

# Checkout ruTorrent once, independent of target architecture; compile its helpers
# in a lightweight target stage instead of installing compilers in runtime.
FROM plugin-source-base AS rutorrent-source
ARG RUTORRENT_REPO
ARG RUTORRENT_REF
ARG RUTORRENT_COMMIT_SHA
RUN --mount=type=bind,source=scripts/fetch-build-source.sh,target=/fetch-source,readonly \
   sh /fetch-source "${RUTORRENT_REPO}" "${RUTORRENT_REF}" "${RUTORRENT_COMMIT_SHA}" /rutorrent-source

FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS rutorrent-helpers
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
RUN apk add --no-cache build-base linux-headers
COPY --from=rutorrent-source /rutorrent-source /source
RUN --mount=type=bind,source=scripts/build-rutorrent-helpers.sh,target=/build-helpers,readonly \
   sh /build-helpers /source /helpers

# Docker-owned filesystem/supervisor helpers are independent from the app ABI.
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS docker-helpers
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
RUN apk add --no-cache build-base linux-headers
RUN --mount=type=bind,source=rootfs/usr/local/libexec,target=/sources,readonly \
   --mount=type=bind,source=scripts/build-docker-helpers.sh,target=/build-docker-helpers,readonly \
   sh /build-docker-helpers /sources /helpers

# ========================= MediaInfo stable-Alpine APKs ==============================
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS mediainfo-builder

SHELL ["/bin/sh", "-eo", "pipefail", "-c"]
ARG MEDIAINFO_VERSION
ARG MEDIAINFO_SHA512
ARG LIBMEDIAINFO_SHA512
ENV REPODEST=/packages

RUN apk add --no-cache abuild build-base \
   && abuild-keygen -a -n \
   && cp /root/.abuild/*.pub /etc/apk/keys/

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
   && mkdir -p /out/keys \
   && cp /packages/src/*/libmediainfo-${MEDIAINFO_VERSION}-r*.apk /out/ \
   && cp /packages/src/*/mediainfo-${MEDIAINFO_VERSION}-r*.apk /out/ \
   && cp /etc/apk/keys/*.pub /out/keys/

# ============================== Final runtime =======================================
FROM alpine:${ALPINE_VERSION}@${ALPINE_DIGEST} AS runtime

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in runtime stage
ARG RTORRENT_BRANCH
ARG MEDIAINFO_VERSION

# -------------------------- Runtime environment variables ---------------------------
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
   HTTP_AUTH=true \
   RU_PUBLIC_URL="" \
   ENABLE_RPC2=false



# Bring compiled artifacts from builder
COPY --from=builder /dist /

# ----------------------------- Base runtime packages --------------------------------
# Keep the runtime minimal; add curl explicitly for healthcheck.
# Trust the build-local APK key only for this installation. Its private key and
# the build toolchain stay in mediainfo-builder; no edge repository is enabled.
RUN --mount=type=bind,from=mediainfo-builder,source=/out,target=/mediainfo-apks \
   apk upgrade --no-cache \
   && apk add --no-cache --keys-dir /mediainfo-apks/keys \
   bash \
   ca-certificates \
   curl \
   libcrypto3 \
   libcurl \
   libgcc \
   libncursesw \
   libstdc++ \
   nginx \
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
   ffmpeg \
   /mediainfo-apks/libmediainfo-${MEDIAINFO_VERSION}-r*.apk \
   /mediainfo-apks/mediainfo-${MEDIAINFO_VERSION}-r*.apk \
   sox \
   zlib \
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

# ------------------------------- ruTorrent install ----------------------------------
COPY --from=rutorrent-source /rutorrent-source /rutorrent/app
COPY --from=rutorrent-helpers /helpers/ /
COPY --from=docker-helpers /helpers/ /
RUN mkdir -p /usr/share/build-info /usr/share/licenses/rutorrent \
   && cp /rutorrent/app/.source-revision /usr/share/build-info/rutorrent-revision \
   && cp /rutorrent/app/LICENSE.md /usr/share/licenses/rutorrent/ \
   && rm -rf /rutorrent/app/plugins/geoip /rutorrent/app/plugins/_cloudflare \
   && find /rutorrent/app -type d -name .github -prune -exec rm -rf {} + \
   && find /rutorrent/app -type f ! -name '*LICENSE*' \( -name '*.md' -o -name 'README*' \) -delete \
   && cp -a /rutorrent/app/conf /rutorrent/conf-dist \
   && mkdir -p /run/rtorrent /run/nginx /run/php

# Install deterministic third-party plugins after cleaning the ruTorrent checkout so
# their required files and license notices are not removed by the generic cleanup above.
COPY --from=geoip2-source /plugin/ /rutorrent/app/plugins/geoip2/
COPY --from=ratiocolor-source /plugin/ /rutorrent/app/plugins/ratiocolor/
COPY --from=geoip2-db-source /database/GeoLite2-Country.mmdb /rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb
COPY --from=geoip2-db-source /database/NOTICE /usr/share/licenses/GeoLite2/NOTICE

ARG GEOIP2_DB_SHA256
RUN --mount=type=bind,source=scripts/normalize-immutable-tree.sh,target=/normalize-immutable-tree,readonly \
   sh /normalize-immutable-tree /rutorrent/app /rutorrent/conf-dist \
   && /usr/local/libexec/native-actor-paths normalize \
   && printf '%s  %s\n' "${GEOIP2_DB_SHA256}" /rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb | sha256sum -c - \
   && (find /rutorrent/app -type f -name '*.php' -print0 | xargs -0 -n1 php85 -l >/dev/null) \
   && php85 -r 'require "/rutorrent/app/plugins/geoip2/geoip2.phar"; $reader = new GeoIp2\Database\Reader("/rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb"); $country = $reader->country("8.8.8.8")->country->isoCode; if ($country !== "US") { fwrite(STDERR, "Unexpected GeoIP country: ".$country."\n"); exit(1); } echo "GeoIP2 lookup OK\n";' \
   && if [ -f /rutorrent/app/tools/update-asset-version.php ]; then php85 -n /rutorrent/app/tools/update-asset-version.php; fi

# ------------------------------- FileBot (optional) ---------------------------------
# Install FileBot-only runtime dependencies when requested.
ARG FILEBOT
ARG FILEBOT_VER
RUN case "${FILEBOT}" in true|false) ;; *) echo 'FILEBOT must be true or false' >&2; exit 64 ;; esac \
   && if [ "${FILEBOT}" = true ]; then \
   apk add --no-cache \
   chromaprint \
   findutils \
   openjdk21-jre-headless ; \
   fi

ARG FILEBOT_SHA256
RUN if [ "${FILEBOT}" = true ]; then \
   mkdir /filebot \
   && cd /filebot \
   && curl -fsSL --retry 3 --retry-max-time 600 --connect-timeout 15 --max-time 180 -o /filebot/filebot.tar.xz "https://get.filebot.net/filebot/FileBot_${FILEBOT_VER}/FileBot_${FILEBOT_VER}-portable.tar.xz" \
   && printf '%s  %s\n' "${FILEBOT_SHA256}" /filebot/filebot.tar.xz | sha256sum -c - \
   && tar -xJf /filebot/filebot.tar.xz \
   && rm -f /filebot/filebot.tar.xz \
   && sed -i 's/-Dapplication.deployment=tar/-Dapplication.deployment=docker/g' /filebot/filebot.sh \
   && find /filebot/lib -type f -not -name 'libjnidispatch.so' -delete ; \
   fi

# ----------------------------- Local configs & scripts -------------------------------
COPY rootfs /
RUN --mount=type=bind,source=rootfs,target=/image-rootfs,readonly \
   --mount=type=bind,source=scripts/normalize-rootfs-permissions.sh,target=/normalize-rootfs,readonly \
   sh /normalize-rootfs /image-rootfs \
   && /usr/local/libexec/native-actor-paths normalize \
   && php85 /etc/nginx/generate-cli-only.php /rutorrent/app /etc/nginx/cli-only.conf \
   && rm /etc/nginx/generate-cli-only.php \
   && mkdir -p /usr/local/share/rutorrent-defaults \
   && cp -p /home/torrent/.rtorrent.rc /usr/local/share/rutorrent-defaults/rtorrent.rc \
   && if [ -f /filebot/args_amc.txt ]; then cp -p /filebot/args_amc.txt /usr/local/share/rutorrent-defaults/args_amc.txt; fi \
   && chmod -R go-w /usr/local/share/rutorrent-defaults

# --- OCI Labels ---
# Keep volatile metadata after expensive installation layers to preserve their caches.
ARG BUILD_DATE
ARG VCS_REF
ARG RTORRENT_VERSION
ARG RUTORRENT_REF
ARG RUTORRENT_COMMIT_SHA
ARG LIBTORRENT_BRANCH
ARG GEOIP2_DB_VERSION
ARG GEOIP2_DB_SHA256
LABEL org.opencontainers.image.title="ruTorrent on Alpine" \
   org.opencontainers.image.version="${RUTORRENT_COMMIT_SHA}" \
   org.opencontainers.image.revision="${VCS_REF}" \
   org.opencontainers.image.created="${BUILD_DATE}" \
   org.opencontainers.image.source="https://github.com/IvanShift/docker-rutorrent" \
   io.github.ivanshift.rtorrent.revision="${RTORRENT_VERSION}" \
   io.github.ivanshift.rtorrent.version="${RTORRENT_BRANCH}" \
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
