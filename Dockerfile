# syntax=docker/dockerfile:1.26
#
# ------------------------------ Global Build Arguments ------------------------------
# Centralize all version pins and feature toggles here.

# --- Platform ARGs (for multi-arch builds) ---
ARG BUILDPLATFORM
ARG TARGETPLATFORM

# --- Alpine base ---
ARG ALPINE_VERSION=3.24.1

# --- Component versions ---
ARG MKTORRENT_VERSION=v1.1
ARG MKTORRENT_COMMIT=b20ef699b4ee5ded2f078ead776c7deac969e19a
ARG DUMP_TORRENT_VERSION=v1.7.0
ARG DUMP_TORRENT_COMMIT=ddf988d3099637c93dc0247854ca711c0a2a0289
# RARLab's archive suffix 7.2.7 contains sources that identify as UnRAR 7.23.
ARG UNRAR_VERSION=7.2.7
ARG UNRAR_SHA256=01d903a7dcf413cb2925696d7796e48e38d471f79bfe7ef3ad2aebf6c12dbefd

# libtorrent v0.16.20
ARG LIBTORRENT_BRANCH=v0.16.20
ARG LIBTORRENT_VERSION=ea68bf287e36a13fa0c13c965dee6c3d40e3d242

# rtorrent v0.16.20
ARG RTORRENT_BRANCH=v0.16.20
ARG RTORRENT_VERSION=dd3ddf7c391ada92e4ba86fb6afd8a6cc01446b8

# --- Final image options ---
ARG FILEBOT=false
ARG FILEBOT_VER=5.2.3
ARG FILEBOT_SHA256=0dae8364f9d465707ff30031d055dcc7c6b24907d96823ced3d4e979f1519d0c
ARG RUTORRENT_REPO=https://github.com/IvanShift/ruTorrent.git
ARG RUTORRENT_REF=e051f6f5d36caaee619248d456ac5d247fc4ddd6

# --- Build-time ruTorrent plugins ---
ARG GEOIP2_REPO=https://github.com/Micdu70/geoip2-rutorrent.git
ARG GEOIP2_REF=cad8a11b47f02ff75358b7bd9c4137648f5fedd0
ARG RATIOCOLOR_REPO=https://github.com/Micdu70/rutorrent-ratiocolor.git
ARG RATIOCOLOR_REF=4aec1988be1e09b44799b71ed4a25751c695a6f2

# --- GeoIP2 country database ---
ARG GEOIP2_DB_VERSION=2026.08.13
ARG GEOIP2_DB_SHA256=b6a525d8ffd7628b59a1c7853264937ae3a23632deebed9daae2ebffbf876265

# --- Build options ---
ARG STRICT_WERROR=true

# --- Build metadata (pass via --build-arg) ---
ARG BUILD_DATE
ARG VCS_REF

# Optional commit pins for ruTorrent plugins
ARG GEOIP2_COMMIT_SHA=
ARG RATIOCOLOR_COMMIT_SHA=


# ============================== Stage 1: Sources fetcher ==============================
# Download all sources once to maximize cache hits across CI runs.
FROM alpine:${ALPINE_VERSION} AS src

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare needed args
ARG LIBTORRENT_BRANCH
ARG LIBTORRENT_VERSION
ARG RTORRENT_BRANCH
ARG RTORRENT_VERSION
ARG MKTORRENT_VERSION
ARG DUMP_TORRENT_VERSION

# Install fetch tools. BusyBox provides the sed used below.
RUN apk add --no-cache ca-certificates git

WORKDIR /src

# ---- libtorrent sources (pinned by branch and commit) ----
RUN git clone --depth 1 --no-tags --single-branch -b "${LIBTORRENT_BRANCH}" "https://github.com/rakshasa/libtorrent.git" libtorrent \
   && cd libtorrent \
   && resolved="$(git rev-parse --verify 'HEAD^{commit}')" \
   && printf 'libtorrent %s resolved commit: %s\n' "${LIBTORRENT_BRANCH}" "${resolved}" \
   && test "${resolved}" = "${LIBTORRENT_VERSION}" \
   && rm -rf .git

# ---- rTorrent sources (pinned by branch and commit) ----
RUN git clone --depth 1 --no-tags --single-branch -b "${RTORRENT_BRANCH}" "https://github.com/rakshasa/rtorrent.git" rtorrent \
   && cd rtorrent \
   && resolved="$(git rev-parse --verify 'HEAD^{commit}')" \
   && printf 'rtorrent %s resolved commit: %s\n' "${RTORRENT_BRANCH}" "${resolved}" \
   && test "${resolved}" = "${RTORRENT_VERSION}" \
   && rm -rf .git

# ---- mktorrent sources (tag) ----
ARG MKTORRENT_COMMIT
RUN git clone --depth 1 --no-tags --branch "${MKTORRENT_VERSION}" "https://github.com/pobrn/mktorrent.git" mktorrent \
   && resolved="$(git -C mktorrent rev-parse --verify 'HEAD^{commit}')" \
   && printf 'mktorrent resolved commit: %s\n' "${resolved}" \
   && test "${resolved}" = "${MKTORRENT_COMMIT}" \
   && rm -rf mktorrent/.git

# ---- dumptorrent sources (tag) ----
ARG DUMP_TORRENT_COMMIT
RUN git clone --depth 1 --no-tags --branch "${DUMP_TORRENT_VERSION}" "https://github.com/tomcdj71/dumptorrent.git" dump-torrent \
   && resolved="$(git -C dump-torrent rev-parse --verify 'HEAD^{commit}')" \
   && printf 'dump-torrent resolved commit: %s\n' "${resolved}" \
   && test "${resolved}" = "${DUMP_TORRENT_COMMIT}" \
   && sed -i '1i #include <sys/time.h>' ./dump-torrent/src/scrapec.c \
   && rm -rf dump-torrent/.git*


# =========================== Build-time plugin sources ==============================
# Fetch architecture-independent plugins and data on the build platform. Exact default
# refs make rebuilds deterministic while still allowing repo/ref overrides.
FROM --platform=${BUILDPLATFORM} alpine:${ALPINE_VERSION} AS plugin-source-base

SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

RUN apk add --no-cache ca-certificates curl git

FROM plugin-source-base AS geoip2-source

ARG GEOIP2_REPO
ARG GEOIP2_REF
ARG GEOIP2_COMMIT_SHA

RUN set -eu; \
   ref="${GEOIP2_COMMIT_SHA:-${GEOIP2_REF}}"; \
   test -n "${GEOIP2_REPO}"; \
   test -n "${ref}"; \
   git init -q /plugin; \
   git -C /plugin remote add origin "${GEOIP2_REPO}"; \
   git -C /plugin fetch -q --depth 1 --no-tags origin "${ref}"; \
   git -C /plugin checkout -q --detach FETCH_HEAD; \
   resolved="$(git -C /plugin rev-parse --verify 'HEAD^{commit}')"; \
   printf 'geoip2 %s@%s (requested %s)\n' "${GEOIP2_REPO}" "${resolved}" "${ref}"; \
   test -r /plugin/plugin.info; \
   test -r /plugin/init.js; \
   test -r /plugin/init.php; \
   test -r /plugin/geoip2.phar; \
   rm -rf /plugin/.git /plugin/.github; \
   rm -f /plugin/README /plugin/README.md; \
   rm -f /plugin/database/GeoLite2-Country.mmdb

FROM plugin-source-base AS ratiocolor-source

ARG RATIOCOLOR_REPO
ARG RATIOCOLOR_REF
ARG RATIOCOLOR_COMMIT_SHA

RUN set -eu; \
   ref="${RATIOCOLOR_COMMIT_SHA:-${RATIOCOLOR_REF}}"; \
   test -n "${RATIOCOLOR_REPO}"; \
   test -n "${ref}"; \
   git init -q /plugin; \
   git -C /plugin remote add origin "${RATIOCOLOR_REPO}"; \
   git -C /plugin fetch -q --depth 1 --no-tags origin "${ref}"; \
   git -C /plugin checkout -q --detach FETCH_HEAD; \
   resolved="$(git -C /plugin rev-parse --verify 'HEAD^{commit}')"; \
   printf 'ratiocolor %s@%s (requested %s)\n' "${RATIOCOLOR_REPO}" "${resolved}" "${ref}"; \
   test -r /plugin/plugin.info; \
   test -r /plugin/init.js; \
   rm -rf /plugin/.git /plugin/.github; \
   rm -f /plugin/README /plugin/README.md

FROM plugin-source-base AS geoip2-db-source

ARG GEOIP2_DB_VERSION
ARG GEOIP2_DB_SHA256

RUN set -eu; \
   test -n "${GEOIP2_DB_VERSION}"; \
   test -n "${GEOIP2_DB_SHA256}"; \
   url="https://github.com/P3TERX/GeoLite.mmdb/releases/download/${GEOIP2_DB_VERSION}/GeoLite2-Country.mmdb"; \
   mkdir -p /database; \
   curl -fsSL -o /database/GeoLite2-Country.mmdb "${url}"; \
   printf '%s  %s\n' "${GEOIP2_DB_SHA256}" /database/GeoLite2-Country.mmdb | sha256sum -c -; \
   printf '%s\n' \
      "GeoLite2-Country.mmdb source: ${url}" \
      "Release: ${GEOIP2_DB_VERSION}" \
      'Database and Contents Copyright (c) MaxMind, Inc.' \
      'GeoLite2 EULA: https://www.maxmind.com/en/geolite2/eula' \
      'GeoNames data: https://creativecommons.org/licenses/by/4.0/' \
      > /database/NOTICE


# =============================== Stage 2: Builder ====================================
# Compile everything from source and stage into /dist for a clean final image.
FROM alpine:${ALPINE_VERSION} AS builder

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in this stage
ARG STRICT_WERROR
ARG UNRAR_VERSION

ENV DIST_PATH="/dist"
ENV CC=gcc
ENV CXX=g++

# Build toolchain and dev libs
RUN apk add --no-cache \
   autoconf automake build-base ca-certificates cmake curl curl-dev \
   libtool linux-headers ncurses-dev openssl-dev pkgconf zlib-dev

# ---------- Build libtorrent (autotools) ----------
WORKDIR /usr/local/src/libtorrent
COPY --from=src /src/libtorrent .
RUN \
   # Set WERROR flags if enabled
   if [ "${STRICT_WERROR}" = "true" ]; then \
   WERROR_FLAGS="-Werror=odr -Werror=lto-type-mismatch -Werror=strict-aliasing"; \
   fi \
   # Use minimal flags for configure C++20 check
   && CONFIGURE_CXXFLAGS="-std=c++20" \
   # Use full optimization flags for make
   && MAKE_CXXFLAGS="-w -O3 -flto -std=c++20 ${WERROR_FLAGS}" \
   && autoreconf -vfi \
   # Pass minimal flags to configure
   && ./configure --enable-aligned CXXFLAGS="${CONFIGURE_CXXFLAGS}" \
   # Pass full flags to make
   && make -j"$(nproc)" CXXFLAGS="${MAKE_CXXFLAGS}" \
   && make install-strip -j"$(nproc)" \
   && make DESTDIR="${DIST_PATH}" install-strip -j"$(nproc)"

# ---------- Build rTorrent (autotools) ----------
WORKDIR /usr/local/src/rtorrent
COPY --from=src /src/rtorrent .
RUN \
   # Set WERROR flags if enabled
   if [ "${STRICT_WERROR}" = "true" ]; then \
   WERROR_FLAGS="-Werror=odr -Werror=lto-type-mismatch -Werror=strict-aliasing"; \
   fi \
   # Use minimal flags for configure C++20 check
   && CONFIGURE_CXXFLAGS="-std=c++20" \
   # Use full optimization flags for make
   && MAKE_CXXFLAGS="-w -O3 -flto -std=c++20 ${WERROR_FLAGS}" \
   && autoreconf -vfi \
   # Pass minimal flags to configure
   && ./configure --with-xmlrpc-tinyxml2 --with-ncurses CXXFLAGS="${CONFIGURE_CXXFLAGS}" \
   # Pass full flags to make
   && make -j"$(nproc)" CXXFLAGS="${MAKE_CXXFLAGS}" \
   && make DESTDIR="${DIST_PATH}" install-strip -j"$(nproc)"

# ---------- Build mktorrent (Makefile) ----------
WORKDIR /usr/local/src/mktorrent
COPY --from=src /src/mktorrent .
RUN \
   printf 'CFLAGS = -w -flto -O3\nUSE_PTHREADS = 1\nUSE_OPENSSL = 1\n' >> Makefile \
   && make -j"$(nproc)" CC="${CC}" \
   && make DESTDIR="${DIST_PATH}" install -j"$(nproc)"

# ---------- Build dumptorrent (CMake) ----------
WORKDIR /usr/local/src/dump-torrent
COPY --from=src /src/dump-torrent .
RUN \
   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
   && cmake --build build --parallel "$(nproc)" \
   && if command -v strip >/dev/null 2>&1; then strip --strip-unneeded build/dumptorrent build/scrapec || true; fi \
   && install -Dm755 build/dumptorrent "${DIST_PATH}/usr/local/bin/dumptorrent" \
   && install -Dm755 build/scrapec "${DIST_PATH}/usr/local/bin/scrapec"

# ---------- Build unrar (Makefile) ----------
WORKDIR /usr/local/src/unrar
ARG UNRAR_SHA256
RUN \
   curl -fsSL -o /tmp/unrarsrc.tar.gz "https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz" \
   && printf '%s  %s\n' "${UNRAR_SHA256}" /tmp/unrarsrc.tar.gz | sha256sum -c - \
   && tar -xzf /tmp/unrarsrc.tar.gz --strip-components=1 \
   && rm -f /tmp/unrarsrc.tar.gz \
   && make -f makefile \
   && install -m 755 unrar "${DIST_PATH}/usr/local/bin/unrar"

# ============================== Stage 3: Final runtime ===============================
FROM alpine:${ALPINE_VERSION}

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in runtime stage
ARG FILEBOT
ARG FILEBOT_VER
ARG RUTORRENT_REPO
ARG RUTORRENT_REF
ARG GEOIP2_DB_SHA256

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
   HTTP_AUTH=false \
   ENABLE_RPC2=false



# Bring compiled artifacts from builder
COPY --from=builder /dist /

# ----------------------------- Base runtime packages --------------------------------
# Keep the runtime minimal; add curl explicitly for healthcheck.
RUN apk upgrade --no-cache \
   && apk add --no-cache \
   bash \
   ca-certificates \
   curl \
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
   php85-pecl-apcu \
   php85-phar \
   php85-session \
   php85-simplexml \
   php85-sockets \
   php85-xml \
   php85-zip \
   # --- End PHP modules ---
   su-exec \
   s6 \
   unzip \
   ffmpeg \
   mediainfo \
   sox \
   zlib \
   # Create user/group and config dir
   && addgroup -S -g ${GID} torrent \
   && adduser -S -D -h /home/torrent -s /bin/sh -G torrent -u ${UID} torrent \
   && mkdir -p /home/torrent /config

# ------------------------------- ruTorrent install ----------------------------------
# Fetch the prepared ruTorrent fork by the configured remote ref. Runtime cleanup below only
# removes unnecessary image contents; it does not patch ruTorrent behavior.
RUN apk add --no-cache --virtual .rutorrent-build git \
   && mkdir -p /rutorrent/app \
   && git init /rutorrent/app \
   && cd /rutorrent/app \
   && git remote add origin "${RUTORRENT_REPO}" \
   && git fetch --depth 1 origin "${RUTORRENT_REF}" \
   && git checkout -q --detach FETCH_HEAD \
   && echo "ruTorrent ${RUTORRENT_REPO}@$(git rev-parse HEAD)" \
   # Cleanup unnecessary image contents
   && rm -rf /rutorrent/app/plugins/geoip \
   && rm -rf /rutorrent/app/plugins/_cloudflare \
   && rm -rf /rutorrent/app/.git \
   && find /rutorrent/app -type d -name ".github" -prune -exec rm -rf {} + \
   && find /rutorrent/app -type f \( -name "*.md" -o -name "LICENSE*" -o -name "README*" \) -delete \
   # Sockets and runtime dirs
   && mkdir -p /run/rtorrent /run/nginx /run/php \
   # Remove build-time deps
   && apk del --no-cache .rutorrent-build

# Install deterministic third-party plugins after cleaning the ruTorrent checkout so
# their required files and license notices are not removed by the generic cleanup above.
COPY --from=geoip2-source /plugin/ /rutorrent/app/plugins/geoip2/
COPY --from=ratiocolor-source /plugin/ /rutorrent/app/plugins/ratiocolor/
COPY --from=geoip2-db-source /database/GeoLite2-Country.mmdb /rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb
COPY --from=geoip2-db-source /database/NOTICE /usr/share/licenses/GeoLite2/NOTICE

RUN printf '%s  %s\n' "${GEOIP2_DB_SHA256}" /rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb | sha256sum -c - \
   && php85 -l /rutorrent/app/plugins/geoip2/init.php \
   && php85 -l /rutorrent/app/plugins/geoip2/lookup.php \
   && php85 -r 'require "/rutorrent/app/plugins/geoip2/geoip2.phar"; $reader = new GeoIp2\Database\Reader("/rutorrent/app/plugins/geoip2/database/GeoLite2-Country.mmdb"); $country = $reader->country("8.8.8.8")->country->isoCode; if ($country !== "US") { fwrite(STDERR, "Unexpected GeoIP country: ".$country."\n"); exit(1); } echo "GeoIP2 lookup OK\n";'

# ------------------------------- FileBot (optional) ---------------------------------
# Install FileBot-only runtime dependencies when requested.
RUN if [ "${FILEBOT}" = true ]; then \
   apk add --no-cache \
   chromaprint \
   findutils \
   openjdk21-jre-headless ; \
   fi

ARG FILEBOT_SHA256
RUN if [ "${FILEBOT}" = true ]; then \
   mkdir /filebot \
   && cd /filebot \
   && curl -fsSL -o /filebot/filebot.tar.xz "https://get.filebot.net/filebot/FileBot_${FILEBOT_VER}/FileBot_${FILEBOT_VER}-portable.tar.xz" \
   && printf '%s  %s\n' "${FILEBOT_SHA256}" /filebot/filebot.tar.xz | sha256sum -c - \
   && tar -xJf /filebot/filebot.tar.xz \
   && rm -f /filebot/filebot.tar.xz \
   && sed -i 's/-Dapplication.deployment=tar/-Dapplication.deployment=docker/g' /filebot/filebot.sh \
   && find /filebot/lib -type f -not -name 'libjnidispatch.so' -delete ; \
   fi

# ----------------------------- Local configs & scripts -------------------------------
COPY rootfs /

# --- OCI Labels ---
# Keep volatile metadata after expensive installation layers to preserve their caches.
ARG BUILD_DATE
ARG VCS_REF
LABEL org.opencontainers.image.title="ruTorrent on Alpine" \
   org.opencontainers.image.version="${RUTORRENT_REF}" \
   org.opencontainers.image.revision="${VCS_REF}" \
   org.opencontainers.image.created="${BUILD_DATE}" \
   org.opencontainers.image.source="${RUTORRENT_REPO}" \
   org.opencontainers.image.description="rTorrent + ruTorrent built from source on Alpine" \
   maintainer="IvanShift"

# ------------------------------- Volumes & Ports ------------------------------------
VOLUME /data /config

# Expose web UI and typical rTorrent ports (tcp/udp)
EXPOSE 8080 45000/tcp 45000/udp 6881/udp

# ------------------------------ Healthcheck & Entrypoint -----------------------------
# Require both rTorrent's Unix socket and the auth-independent local HTTP endpoint.
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
   CMD test -S /run/rtorrent/rtorrent.sock \
      && curl -fsS http://127.0.0.1:8080/healthz >/dev/null \
      || exit 1

# s6-svscan is provided by the Alpine s6 package under /usr/bin
ENTRYPOINT ["/usr/local/bin/startup"]
CMD ["/usr/bin/s6-svscan", "/etc/s6.d"]
