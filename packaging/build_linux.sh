#!/bin/sh
# Builds NFNF-IronMON-Linux inside an old-glibc container so the result runs on
# most current distributions (glibc >= the build image's, here Debian 12 = 2.36).
#
#   docker run --rm -v "$PWD":/src -w /src python:3.12-slim-bookworm sh packaging/build_linux.sh
#
# Output: dist/NFNF-IronMON-Linux/   (components must already be fetched)
set -eu
apt-get update -qq >/dev/null
apt-get install -y -qq --no-install-recommends binutils tk8.6 >/dev/null
pip install --quiet --root-user-action=ignore "pyinstaller==6.*"
rm -rf build/pyi-work build/pyi-dist
pyinstaller --noconfirm --clean --onedir --name nfnf-ironmon \
    --paths "$PWD" --collect-submodules nfnf_ironmon \
    --distpath build/pyi-dist --workpath build/pyi-work --specpath build \
    packaging/entry.py
python packaging/assemble.py build/pyi-dist/nfnf-ironmon dist/NFNF-IronMON-Linux --platform linux-x64
chown -R "$(stat -c %u:%g /src)" build dist
