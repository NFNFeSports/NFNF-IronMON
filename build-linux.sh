#!/bin/sh
# One-command portable Linux build. Requires Docker. Output:
#   dist/NFNF-IronMON-Linux/          (runs on glibc >= 2.36: Debian 12, Ubuntu 23.04+, Fedora 37+ ...)
#   dist/NFNF-IronMON-Linux.tar.gz
set -eu
cd "$(dirname "$0")"
exec docker run --rm -v "$PWD":/src -w /src python:3.12-slim-bookworm sh packaging/build_linux.sh
