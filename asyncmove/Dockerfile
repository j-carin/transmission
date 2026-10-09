# Patched transmission-daemon (async move backport, branch async-move-4.1.3)
# dropped into the exact linuxserver image the live container runs.
#
# Build context: this directory, after ./prepare-source.sh has written
# transmission-src.tar (HEAD of the branch plus every submodule, recursively).
#
#   ./prepare-source.sh
#   docker build -t local/transmission-asyncmove:4.1.3 .

# The live container's image: lscr.io/linuxserver/transmission:latest,
# label 4.1.3-r0-ls363, built 2026-09-29, Alpine edge.
ARG BASE=lscr.io/linuxserver/transmission@sha256:4813745e2c721997d8849f5718786fd4406b3c656fbef479f87a161e51383cbd

# ---------------------------------------------------------------------------
# builder: the same image, so musl, libstdc++, curl, libevent, libdeflate,
# libpsl and miniupnpc are the versions the binary will run against.
# ---------------------------------------------------------------------------
FROM ${BASE} AS builder

# makedepends from aports community/transmission APKBUILD (pkgver 4.1.3-r0),
# minus the GTK/Qt toolkits, plus build-base (abuild always installs it; clang
# needs gcc's libgcc_s.so link) and abuild for its default CFLAGS/LDFLAGS.
RUN apk add --no-cache \
      abuild build-base clang cmake curl-dev gettext-dev libdeflate-dev libevent-dev \
      libpsl-dev linux-headers lld llvm miniupnpc-dev openssl-dev samurai

COPY transmission-src.tar /tmp/
RUN mkdir /src && tar -xf /tmp/transmission-src.tar -C /src && rm /tmp/transmission-src.tar
WORKDIR /src

# 4.1.3's CMakeLists.txt takes the revision from $GIT_COMMIT only when
# $JENKINS_URL is also set (there is no .git in this tree, so otherwise it
# would fall back to "0"). It keeps the first 10 characters, which for the
# 4.1.3 tag is 838877323f: the same "(838877323f)" stock prints.
ENV JENKINS_URL=local \
    GIT_COMMIT=838877323facc4cc2b677fe817e203779c437bb1 \
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
COPY --from=builder --chown=root:root \
     /src/build/daemon/transmission-daemon /usr/bin/transmission-daemon
LABEL org.jcarin.asyncmove.source="transmission branch async-move-4.1.3" \
      org.jcarin.asyncmove.base="lscr.io/linuxserver/transmission@sha256:4813745e2c721997d8849f5718786fd4406b3c656fbef479f87a161e51383cbd"
