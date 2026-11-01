# Patched transmission-daemon (async move backport, branch async-move-4.1.4)
# dropped into the exact linuxserver image the live container runs.
#
# Build context: this directory, after ./prepare-source.sh has written
# transmission-src.tar (HEAD of the branch plus every submodule, recursively).
#
#   ./prepare-source.sh
#   docker build -t local/transmission-asyncmove:4.1.4 .

# lscr.io/linuxserver/transmission:4.1.4-r0-ls367, built 2026-10-30, Alpine edge:
# the linux/arm64 manifest of index sha256:5b3c27a96c8a37c878a525ebf377a32032c3a3acfe845ecaa7c3eb10b09c2caa
# (amd64 manifest: sha256:ad85dbef853a8fb5318898023c9c2be88a982d26c4e4dfca02a18095ac3113c9).
ARG BASE=lscr.io/linuxserver/transmission@sha256:48ef9dd6e3d7e48de55d43cc1e16fd6e9ea269a2f06c0593a3ecbd74755fd387

# ---------------------------------------------------------------------------
# builder: the same image, so musl, libstdc++, curl, libevent, libdeflate,
# libpsl and miniupnpc are the versions the binary will run against.
# ---------------------------------------------------------------------------
FROM ${BASE} AS builder

# makedepends from aports community/transmission APKBUILD (pkgver 4.1.4-r0),
# minus the GTK/Qt toolkits, plus build-base (abuild always installs it; clang
# needs gcc's libgcc_s.so link) and abuild for its default CFLAGS/LDFLAGS.
RUN apk add --no-cache \
      abuild build-base clang cmake curl-dev gettext-dev libdeflate-dev libevent-dev \
      libpsl-dev linux-headers lld llvm miniupnpc-dev openssl-dev samurai

COPY transmission-src.tar /tmp/
RUN mkdir /src && tar -xf /tmp/transmission-src.tar -C /src && rm /tmp/transmission-src.tar
WORKDIR /src

# 4.1.4's CMakeLists.txt takes the revision from $GIT_COMMIT only when
# $JENKINS_URL is also set (there is no .git in this tree, so otherwise it
# would fall back to "0"). It keeps the first 10 characters, which for the
# 4.1.4 tag is a6d907d9ea: the same "(a6d907d9ea)" stock prints.
ENV JENKINS_URL=local \
    GIT_COMMIT=a6d907d9ea2313143af714d7830feb6c1b60b84c \
    CMAKE_POLICY_VERSION_MINIMUM=3.5

# Flags as in the APKBUILD's build(): abuild's default CFLAGS/CXXFLAGS/LDFLAGS
# (/usr/share/abuild/default.conf; /etc/abuild.conf is only comments),
# then clang, -flto -O2 -DNDEBUG and lld. Only the daemon is enabled.
RUN set -e; . /usr/share/abuild/default.conf; \
    export LDFLAGS="$LDFLAGS -fuse-ld=lld"; \
    CC=clang CXX=clang++ \
    CXXFLAGS="$CXXFLAGS -flto -O2 -DNDEBUG" \
    CFLAGS="$CFLAGS -flto -O2 -DNDEBUG" \
    cmake -B build -G Ninja \
      -DCMAKE_INSTALL_PREFIX=/usr \
      -DCMAKE_INSTALL_LIBDIR=lib \
      -DCMAKE_BUILD_TYPE=None \
      -DBUILD_SHARED_LIBS=OFF \
      -DENABLE_DEPRECATED=ON \
      -DENABLE_DAEMON=ON \
      -DENABLE_CLI=OFF \
      -DENABLE_GTK=OFF \
      -DENABLE_QT=OFF \
      -DENABLE_MAC=OFF \
      -DENABLE_UTILS=OFF \
      -DENABLE_UTP=ON \
      -DENABLE_NLS=ON \
      -DENABLE_TESTS=OFF \
      -DINSTALL_LIB=OFF \
      -DRUN_CLANG_TIDY=OFF \
      -DREBUILD_WEB=OFF \
      -DUSE_SYSTEM_DEFLATE=ON \
      -DUSE_SYSTEM_EVENT2=ON \
      -DUSE_SYSTEM_MINIUPNPC=ON \
      -DUSE_SYSTEM_PSL=ON \
      -DWITH_CRYPTO=openssl \
      -DWITH_SYSTEMD=OFF \
    && grep -E 'LONG_VERSION_STRING|PEERID_PREFIX|USERAGENT_PREFIX' build/libtransmission/version.h \
    && cmake --build build --target transmission-daemon \
    && strip build/daemon/transmission-daemon

# ---------------------------------------------------------------------------
# final: the live image byte-for-byte, with only the daemon binary replaced.
# ---------------------------------------------------------------------------
FROM ${BASE}
# re-declared so the label below can name the base it was built on
ARG BASE
COPY --from=builder --chown=root:root \
     /src/build/daemon/transmission-daemon /usr/bin/transmission-daemon
LABEL org.jcarin.asyncmove.source="transmission branch async-move-4.1.4" \
      org.jcarin.asyncmove.base="${BASE}"
