# syntax=docker/dockerfile:1.7
#
# ------------------------------ Global Build Arguments ------------------------------
# Centralize all version pins and feature toggles here.

# --- Platform ARGs (for multi-arch builds) ---
ARG BUILDPLATFORM
ARG TARGETPLATFORM

# --- Alpine base ---
ARG ALPINE_VERSION=3.22

# --- Component versions ---
ARG CARES_VERSION=1.34.5
ARG CURL_VERSION=8.17.0
ARG XMLRPC_VERSION=1.60.05
ARG MKTORRENT_VERSION=v1.1
ARG DUMP_TORRENT_VERSION=v1.7.0
ARG UNRAR_VERSION=7.2.1

# libtorrent v0.16.2
ARG LIBTORRENT_BRANCH=v0.16.2
ARG LIBTORRENT_VERSION=bd9c66338d9d33b92db9939abb3e0c5d0dace511

# rtorrent v0.16.2
ARG RTORRENT_BRANCH=v0.16.2
ARG RTORRENT_VERSION=8550facf43fd1e36b416bcdb3b025e7e09578364

# --- Final image options ---
ARG FILEBOT=false
ARG FILEBOT_VER=5.2.0
ARG RUTORRENT_VER=5.2.10

# --- Build options ---
ARG STRICT_WERROR=true

# --- Build metadata (pass via --build-arg) ---
ARG BUILD_DATE
ARG VCS_REF

# Optional checksums (recommended to provide in CI for supply-chain hardening)
ARG CARES_SHA256=
ARG CURL_SHA256=
ARG XMLRPC_SHA256=
ARG RUTORRENT_SHA256=

# Optional commit pins for ruTorrent plugins
ARG GEOIP2_COMMIT_SHA=
ARG RATIOCOLOR_COMMIT_SHA=


# ============================== Stage 1: Sources fetcher ==============================
# Download all sources once to maximize cache hits across CI runs.
FROM alpine:${ALPINE_VERSION} AS src

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare needed args
ARG CARES_VERSION
ARG CURL_VERSION
ARG XMLRPC_VERSION
ARG LIBTORRENT_BRANCH
ARG LIBTORRENT_VERSION
ARG RTORRENT_BRANCH
ARG RTORRENT_VERSION
ARG MKTORRENT_VERSION
ARG DUMP_TORRENT_VERSION

ARG CARES_SHA256
ARG CURL_SHA256
ARG XMLRPC_SHA256

# Install fetch tools (with BuildKit cache for apk)
RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache ca-certificates curl git tar sed xz

WORKDIR /src

# ---- c-ares sources (with optional checksum verification) ----
RUN mkdir cares \
 && curl -fsSL -o /tmp/cares.tgz "https://github.com/c-ares/c-ares/releases/download/v${CARES_VERSION}/c-ares-${CARES_VERSION}.tar.gz" \
 && if [ -n "${CARES_SHA256}" ]; then echo "${CARES_SHA256} /tmp/cares.tgz" | sha256sum -c -; fi \
 && tar xzf /tmp/cares.tgz --strip 1 -C cares \
 && rm -f /tmp/cares.tgz

# ---- curl sources (with optional checksum verification) ----
RUN mkdir curl \
 && curl -fsSL -o /tmp/curl.tgz "https://curl.se/download/curl-${CURL_VERSION}.tar.gz" \
 && if [ -n "${CURL_SHA256}" ]; then echo "${CURL_SHA256} /tmp/curl.tgz" | sha256sum -c -; fi \
 && tar xzf /tmp/curl.tgz --strip 1 -C curl \
 && rm -f /tmp/curl.tgz

# ---- libtorrent sources (pinned by branch and commit) ----
RUN git clone --depth 1 --no-tags --single-branch -b "${LIBTORRENT_BRANCH}" "https://github.com/rakshasa/libtorrent.git" libtorrent \
 && cd libtorrent \
 && git fetch --depth 1 origin "${LIBTORRENT_VERSION}" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git

# ---- rTorrent sources (pinned by branch and commit) ----
RUN git clone --depth 1 --no-tags --single-branch -b "${RTORRENT_BRANCH}" "https://github.com/rakshasa/rtorrent.git" rtorrent \
 && cd rtorrent \
 && git fetch --depth 1 origin "${RTORRENT_VERSION}" \
 && git checkout -q FETCH_HEAD \
 && rm -rf .git

# ---- xmlrpc-c sources (super-stable release) ----
RUN mkdir xmlrpc-c \
 && curl -fsSL -o /tmp/xmlrpc-c.tgz "https://downloads.sourceforge.net/project/xmlrpc-c/Xmlrpc-c%20Super%20Stable/${XMLRPC_VERSION}/xmlrpc-c-${XMLRPC_VERSION}.tgz" \
 && if [ -n "${XMLRPC_SHA256}" ]; then echo "${XMLRPC_SHA256} /tmp/xmlrpc-c.tgz" | sha256sum -c -; fi \
 && tar xzf /tmp/xmlrpc-c.tgz --strip 1 -C xmlrpc-c \
 && rm -f /tmp/xmlrpc-c.tgz

# ---- mktorrent sources (tag) ----
RUN git clone --depth 1 --no-tags --branch "${MKTORRENT_VERSION}" "https://github.com/pobrn/mktorrent.git" mktorrent \
 && rm -rf mktorrent/.git

# ---- dumptorrent sources (tag) ----
RUN git clone --depth 1 --no-tags --branch "${DUMP_TORRENT_VERSION}" "https://github.com/tomcdj71/dumptorrent.git" dump-torrent \
 && sed -i '1i #include <sys/time.h>' ./dump-torrent/src/scrapec.c \
 && rm -rf dump-torrent/.git*


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

# Build toolchain and dev libs (use BuildKit apk cache)
RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache \
      autoconf automake binutils brotli-dev build-base ca-certificates \
      cmake cppunit-dev curl-dev expat-dev libtool linux-headers ncurses-dev \
      pkgconf \
      openssl-dev zlib-dev zstd-dev

# ---------- Build c-ares ----------
WORKDIR /usr/local/src/cares
COPY --from=src /src/cares .
# Use a single RUN for better layer locality; enable build cache for compilers.
RUN \
    cmake . -DCARES_SHARED=ON -DCMAKE_BUILD_TYPE=Release -DCMAKE_C_FLAGS_RELEASE="-O3" \
 && cmake --build . --parallel "$(nproc)" --clean-first \
 && cmake --install . --prefix /usr/local --strip \
 && DESTDIR="${DIST_PATH}" cmake --install . --strip

# ---------- Build curl ----------
WORKDIR /usr/local/src/curl
COPY --from=src /src/curl .
RUN \
    cmake . \
      -DENABLE_ARES=ON \
      -DCURL_USE_OPENSSL=ON \
      -DCURL_BROTLI=ON \
      -DCURL_ZSTD=ON \
      -DBUILD_SHARED_LIBS=ON \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_C_FLAGS_RELEASE="-O3" \
 && cmake --build . --parallel "$(nproc)" --clean-first \
 && cmake --install . --prefix /usr/local --strip \
 && DESTDIR="${DIST_PATH}" cmake --install . --strip

# ---------- Build xmlrpc-c ----------
WORKDIR /usr/local/src/xmlrpc-c
COPY --from=src /src/xmlrpc-c .
RUN \
    ./configure \
      --prefix=/usr/local \
      --disable-abyss-server \
      --disable-cgi-server \
      --disable-libwww-client \
      --disable-cplusplus \
 && make -j"$(nproc)" \
 && make install \
 && make DESTDIR="${DIST_PATH}" install \
 && find /usr/local/lib -type f -name "libxmlrpc*.so*" -exec strip --strip-unneeded {} + || true \
 && find "${DIST_PATH}/usr/local/lib" -type f -name "libxmlrpc*.so*" -exec strip --strip-unneeded {} + || true

# ---------- Build libtorrent (autotools) ----------
WORKDIR /usr/local/src/libtorrent
COPY --from=src /src/libtorrent .
RUN \
    # Set WERROR flags if enabled
    if [ "${STRICT_WERROR}" = "true" ]; then \
      WERROR_FLAGS="-Werror=odr -Werror=lto-type-mismatch -Werror=strict-aliasing"; \
    fi \
    # Use minimal flags for configure C++17 check
 && CONFIGURE_CXXFLAGS="-std=c++17" \
 && CONFIGURE_CPPFLAGS="-DXMLRPC_HAVE_INT64" \
    # Use full optimization flags for make
 && MAKE_CXXFLAGS="-w -O3 -flto -std=c++17 ${WERROR_FLAGS}" \
 && autoreconf -vfi \
    # Pass minimal flags to configure
 && ./configure --enable-aligned CXXFLAGS="${CONFIGURE_CXXFLAGS}" CPPFLAGS="${CONFIGURE_CPPFLAGS}" \
    # Pass full flags to make
 && make -j"$(nproc)" CXXFLAGS="${MAKE_CXXFLAGS}" CPPFLAGS="${CONFIGURE_CPPFLAGS}" \
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
    # Use minimal flags for configure C++17 check
 && CONFIGURE_CXXFLAGS="-std=c++17" \
 && CONFIGURE_CPPFLAGS="-DXMLRPC_HAVE_INT64" \
    # Use full optimization flags for make
 && MAKE_CXXFLAGS="-w -O3 -flto -std=c++17 ${WERROR_FLAGS}" \
 && autoreconf -vfi \
    # Pass minimal flags to configure
 && ./configure --with-xmlrpc-c --with-ncurses CXXFLAGS="${CONFIGURE_CXXFLAGS}" CPPFLAGS="${CONFIGURE_CPPFLAGS}" \
    # Pass full flags to make
 && make -j"$(nproc)" CXXFLAGS="${MAKE_CXXFLAGS}" CPPFLAGS="${CONFIGURE_CPPFLAGS}" \
 && make install-strip -j"$(nproc)" \
 && make DESTDIR="${DIST_PATH}" install-strip -j"$(nproc)"

# ---------- Build mktorrent (Makefile) ----------
WORKDIR /usr/local/src/mktorrent
COPY --from=src /src/mktorrent .
RUN \
    printf 'CFLAGS = -w -flto -O3\nUSE_PTHREADS = 1\nUSE_OPENSSL = 1\n' >> Makefile \
 && make -j"$(nproc)" CC="${CC}" \
 && make install -j"$(nproc)" \
 && make DESTDIR="${DIST_PATH}" install -j"$(nproc)"

# ---------- Build dumptorrent (CMake) ----------
WORKDIR /usr/local/src/dump-torrent
COPY --from=src /src/dump-torrent .
RUN \
    cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
 && cmake --build build --parallel "$(nproc)" \
 && if command -v strip >/dev/null 2>&1; then strip --strip-unneeded build/dumptorrent build/scrapec || true; fi \
 && install -Dm755 build/dumptorrent /usr/local/bin/dumptorrent \
 && install -Dm755 build/scrapec /usr/local/bin/scrapec \
 && install -Dm755 build/dumptorrent "${DIST_PATH}/usr/local/bin/dumptorrent" \
 && install -Dm755 build/scrapec "${DIST_PATH}/usr/local/bin/scrapec"

# ---------- Build unrar (Makefile) ----------
WORKDIR /usr/local/src/unrar
RUN \
    curl -fsSL "https://www.rarlab.com/rar/unrarsrc-${UNRAR_VERSION}.tar.gz" | tar xz --strip 1 \
 && make -f makefile \
 && install -m 755 unrar /usr/local/bin/unrar \
 && install -m 755 unrar "${DIST_PATH}/usr/local/bin/unrar"

# ============================== Stage 3: Final runtime ===============================
FROM alpine:${ALPINE_VERSION}

# Use a strict shell that fails on errors and pipe failures
SHELL ["/bin/sh", "-eo", "pipefail", "-c"]

# Re-declare args needed in runtime stage
ARG FILEBOT
ARG FILEBOT_VER
ARG RUTORRENT_VER
ARG RUTORRENT_SHA256
ARG GEOIP2_COMMIT_SHA
ARG RATIOCOLOR_COMMIT_SHA
ARG BUILD_DATE
ARG VCS_REF

# --- OCI Labels ---
LABEL org.opencontainers.image.title="ruTorrent on Alpine" \
      org.opencontainers.image.version="${RUTORRENT_VER}" \
      org.opencontainers.image.revision="${VCS_REF}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="https://github.com/Novik/ruTorrent" \
      org.opencontainers.image.description="rTorrent + ruTorrent built from source on Alpine" \
      maintainer="IvanShift"

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
    FILEBOT_LANG=fr \
    FILEBOT_CONFLICT=skip \
    HTTP_AUTH=false

# Create user/group and config dir
RUN addgroup -S -g ${GID} torrent \
 && adduser -S -D -h /home/torrent -s /bin/sh -G torrent -u ${UID} torrent \
 && mkdir -p /home/torrent /config

# Bring compiled artifacts from builder
COPY --from=builder /dist /

# ----------------------------- Base runtime packages --------------------------------
# Keep the runtime minimal; add curl explicitly for healthcheck.
RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache \
      7zip \
      bash \
      ca-certificates \
      findutils \
      nginx \
      openssl \
      # --- curl runtime libs ---
      brotli-libs \
      zstd-libs \
      # --- Full PHP modules for ruTorrent & plugins ---
      php84 \
      php84-bcmath \
      php84-ctype \
      php84-curl \
      php84-dom \
      php84-fpm \
      php84-mbstring \
      php84-opcache \
      php84-openssl \
      php84-pecl-apcu \
      php84-phar \
      php84-session \
      php84-sockets \
      php84-xml \
      php84-zip \
      # --- End PHP modules ---
      ncurses \
      expat \
      su-exec \
      s6 \
      unzip \
      ffmpeg \
      libmediainfo \
      mediainfo \
      libzen \
      sox

# ------------------------------- ruTorrent install ----------------------------------
# Prefer release tarball for determinism; plugins via git and then drop git.
RUN --mount=type=cache,target=/var/cache/apk \
    apk add --no-cache --virtual .rutorrent-build git \
 && mkdir -p /rutorrent/app \
 # Download ruTorrent release
 && curl -fsSL -o /tmp/rutorrent.tgz "https://github.com/Novik/ruTorrent/archive/refs/tags/v${RUTORRENT_VER}.tar.gz" \
 && if [ -n "${RUTORRENT_SHA256}" ]; then echo "${RUTORRENT_SHA256} /tmp/rutorrent.tgz" | sha256sum -c -; fi \
 && tar xzf /tmp/rutorrent.tgz --strip 1 -C /rutorrent/app \
 # Plugins (pinned if COMMIT_SHA is provided)
 && git clone --depth 1 --no-tags https://github.com/Micdu70/geoip2-rutorrent.git /rutorrent/app/plugins/geoip2 \
 && (cd /rutorrent/app/plugins/geoip2 && if [ -n "${GEOIP2_COMMIT_SHA}" ]; then git fetch --depth 1 origin "${GEOIP2_COMMIT_SHA}" && git checkout -q FETCH_HEAD; fi) \
 && git clone --depth 1 --no-tags https://github.com/Micdu70/rutorrent-ratiocolor.git /rutorrent/app/plugins/ratiocolor \
 && (cd /rutorrent/app/plugins/ratiocolor && if [ -n "${RATIOCOLOR_COMMIT_SHA}" ]; then git fetch --depth 1 origin "${RATIOCOLOR_COMMIT_SHA}" && git checkout -q FETCH_HEAD; fi) \
 # Cleanup unnecessary stuff
 && rm -rf /rutorrent/app/plugins/geoip \
 && rm -rf /rutorrent/app/plugins/_cloudflare \
 && rm -rf /rutorrent/app/plugins/geoip2/.git \
 && rm -rf /rutorrent/app/plugins/ratiocolor/.git \
 && rm -rf /rutorrent/app/.git \
 && find /rutorrent/app -type d -name ".github" -prune -exec rm -rf {} + \
 && find /rutorrent/app -type f \( -name "*.md" -o -name "LICENSE*" -o -name "README*" \) -delete \
 # Ensure ruTorrent's XML-RPC probe sends an empty target parameter before the i8 value
 && sed -i 's/new rXMLRPCCommand("to_kb", floatval(1024))/new rXMLRPCCommand("to_kb", array("", floatval(1024)))/' /rutorrent/app/php/settings.php \
 # Sockets and runtime dirs
 && mkdir -p /run/rtorrent /run/nginx /run/php \
 # Remove build-time deps
 && apk del .rutorrent-build \
 && rm -f /tmp/rutorrent.tgz

# ------------------------------- FileBot (optional) ---------------------------------
# Install multimedia/JRE only if FILEBOT=true to keep the default image slim.
RUN if [ "${FILEBOT}" = true ]; then \
      apk add --no-cache \
        chromaprint \
        openjdk17-jre-headless \
        ffmpeg \
        libmediainfo \
        libzen \
        mediainfo \
        sox ; \
    fi

RUN if [ "${FILEBOT}" = true ]; then \
      mkdir /filebot \
      && cd /filebot \
      && curl -fsSL -o /filebot/filebot.tar.xz "https://get.filebot.net/filebot/FileBot_${FILEBOT_VER}/FileBot_${FILEBOT_VER}-portable.tar.xz" \
      && tar -xJf /filebot/filebot.tar.xz \
      && rm -f /filebot/filebot.tar.xz \
      && sed -i 's/-Dapplication.deployment=tar/-Dapplication.deployment=docker/g' /filebot/filebot.sh \
      && find /filebot/lib -type f -not -name 'libjnidispatch.so' -delete ; \
    fi

# ----------------------------- Local configs & scripts -------------------------------
COPY rootfs /
RUN chmod 775 /usr/local/bin/*

# ------------------------------- Volumes & Ports ------------------------------------
VOLUME /data /config

# Expose web UI and typical rTorrent ports (tcp/udp)
EXPOSE 8080 45000/tcp 45000/udp 6881/udp

# ------------------------------ Healthcheck & Entrypoint -----------------------------
# Use curl for a deterministic healthcheck (busybox wget may vary).
HEALTHCHECK --interval=60s --timeout=5s --start-period=30s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/ >/dev/null || exit 1

# s6-svscan is provided by the Alpine s6 package under /usr/bin
ENTRYPOINT ["/usr/local/bin/startup"]
CMD ["/usr/bin/s6-svscan", "/etc/s6.d"]
