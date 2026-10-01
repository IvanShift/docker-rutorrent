#!/bin/sh
# Keep configure's visibility, threading and pkg-config flags in the Makefiles.
set -eu
[ "$#" -eq 1 ] || exit 64
component=$1
case "${RTORRENT_BRANCH:?}" in
    v0.16.24) cxx_standard=c++20; legacy=false ;;
    v0.9.8) cxx_standard=c++17; legacy=true ;;
    *) echo 'RTORRENT_BRANCH must be v0.16.24 or v0.9.8' >&2; exit 64 ;;
esac
case "${STRICT_WERROR:?}" in true|false) ;; *) echo 'STRICT_WERROR must be true or false' >&2; exit 64 ;; esac
flags="-O3 -flto -std=$cxx_standard"
if [ "$legacy" = true ]; then
    flags="$flags -Wno-deprecated-declarations -Wno-terminate"
fi
if [ "$STRICT_WERROR" = true ]; then
    flags="$flags -Werror=lto-type-mismatch -Werror=strict-aliasing"
    # rTorrent 0.9.8 has a known directory_entry ODR warning under current GCC.
    [ "$legacy" = true ] || flags="$flags -Werror=odr"
fi
case "$component" in
    libtorrent)
        set -- --disable-debug
        [ "$legacy" = false ] || set -- "$@" --enable-aligned
        ;;
    rtorrent)
        backend=--with-xmlrpc-tinyxml2
        [ "$legacy" = false ] || backend=--with-xmlrpc-c
        set -- --disable-debug "$backend" --with-ncurses
        ;;
    *) echo "unknown native component: $component" >&2; exit 64 ;;
esac
autoreconf -vfi
./configure "$@" CXXFLAGS="$flags"
make -j"$(nproc)"
if [ "$component" = libtorrent ]; then
    make install-strip -j"$(nproc)"
fi
make DESTDIR="${DIST_PATH:?}" install-strip -j"$(nproc)"
license_directory="$DIST_PATH/usr/share/licenses/$component"
mkdir -p "$license_directory"
find . -maxdepth 1 -type f \( -iname '*license*' -o -iname 'copying*' -o -iname 'copyright*' -o -iname 'authors*' \) \
    -exec cp -p {} "$license_directory/" \;
