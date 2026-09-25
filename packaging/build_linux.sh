#!/bin/sh
# Runs INSIDE python:3.12-slim-bookworm (glibc 2.36 baseline). Use ./build-linux.sh from the repo root.
set -eu
PLAT=linux-x64
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends binutils tk8.6 >/dev/null
pip install --quiet --root-user-action=ignore "pyinstaller==6.16.0"
# 1. third-party components: pinned URLs, sha256-verified (components.json)
python -m nfnf_ironmon components fetch --platform "$PLAT" upr-zx firered-ironmon-rnqs sdl2 jdk-build mgba-source
# 1b. emulator core compiled from the pinned mGBA source (reproducible; MPL-2.0 source available)
sh packaging/build_core.sh "$PLAT"
# 2. reduced Java runtime: only the modules UPR ZX needs (jdeps: java.base,java.desktop,java.logging)
rm -rf build/jre-$PLAT
build/jdk/$PLAT/bin/jlink --module-path build/jdk/$PLAT/jmods --add-modules java.base,java.desktop,java.logging \
    --strip-debug --no-header-files --no-man-pages --compress=zip-9 --output build/jre-$PLAT
# 3. freeze the application (no Python needed by users)
rm -rf build/pyi-work build/pyi-dist
pyinstaller --noconfirm --clean --onedir --name nfnf-ironmon \
    --paths "$PWD" \
    --collect-submodules nfnf_ironmon \
    --add-data "$PWD/nfnf_ironmon/tracker/data:nfnf_ironmon/tracker/data" \
    --collect-data nfnf_ironmon \
    --distpath build/pyi-dist --workpath build/pyi-work --specpath build \
    packaging/entry.py >/dev/null 2>&1
# 4. portable folder + archive
python packaging/assemble.py build/pyi-dist/nfnf-ironmon dist/NFNF-IronMON-Linux --platform "$PLAT" --java build/jre-$PLAT
tar -C dist -czf dist/NFNF-IronMON-Linux.tar.gz NFNF-IronMON-Linux
chown -R "$(stat -c %u:%g /src)" build dist runtime emulator randomizer randomizer-profiles components.json 2>/dev/null || true
du -sh dist/NFNF-IronMON-Linux dist/NFNF-IronMON-Linux.tar.gz
