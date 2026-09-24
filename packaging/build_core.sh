#!/bin/sh
# Builds the mGBA libretro core from the pinned source tarball (components.json: mgba-source).
# Runs INSIDE debian:bookworm-slim.  usage: sh packaging/build_core.sh linux-x64|windows-x64
set -eu
PLAT="$1"
SRC=$(ls build/mgba-src-*.tar.gz | head -1)
apt-get update -qq >/dev/null
PKGS="cmake make pkg-config ca-certificates"
[ "$PLAT" = linux-x64 ] && PKGS="$PKGS gcc g++"
[ "$PLAT" = windows-x64 ] && PKGS="$PKGS mingw-w64"
apt-get install -y -qq --no-install-recommends $PKGS >/dev/null
rm -rf build/mgba-src build/mgba-build-$PLAT && mkdir -p build/mgba-src
tar -xzf "$SRC" -C build/mgba-src --strip-components=1
OPTS="-DBUILD_LIBRETRO=ON -DBUILD_QT=OFF -DBUILD_SDL=OFF -DBUILD_SHARED=OFF -DBUILD_STATIC=OFF -DUSE_FFMPEG=OFF
 -DUSE_LIBZIP=OFF -DUSE_MINIZIP=OFF -DUSE_EDITLINE=OFF -DUSE_ELF=OFF -DUSE_SQLITE3=OFF -DUSE_LUA=OFF -DUSE_EPOXY=OFF
 -DUSE_DISCORD_RPC=OFF -DUSE_PNG=OFF -DUSE_ZLIB=OFF -DUSE_FREETYPE=OFF -DUSE_JSON_C=OFF -DBUILD_GL=OFF -DBUILD_GLES2=OFF
 -DBUILD_GLES3=OFF -DSKIP_LIBRARY=ON -DCMAKE_BUILD_TYPE=Release"
if [ "$PLAT" = windows-x64 ]; then
  OPTS="$OPTS -DCMAKE_SYSTEM_NAME=Windows -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc-posix
   -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++-posix -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres
   -DCMAKE_SHARED_LINKER_FLAGS=-static"
  OUT=mgba_libretro.dll
else
  OUT=mgba_libretro.so
fi
cmake -S build/mgba-src -B build/mgba-build-$PLAT $OPTS >/dev/null
cmake --build build/mgba-build-$PLAT -j"$(nproc)" --target mgba_libretro >/dev/null
mkdir -p emulator/cores/$PLAT
cp build/mgba-build-$PLAT/$OUT emulator/cores/$PLAT/$OUT
echo "built emulator/cores/$PLAT/$OUT"
