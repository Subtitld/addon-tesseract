#!/usr/bin/env bash
# Run native/build.sh for Linux or Windows in a container, as CI does.
#
# Copyright 2026 Subtitld contributors
# SPDX-License-Identifier: Apache-2.0
#
#     native/docker-build.sh linux|windows [OUT_DIR]
#
# linux:   AlmaLinux 8 (glibc 2.28, the oldest the other add-ons support) with
#          gcc-toolset-13, so the library runs on any distribution since 2018.
# windows: Debian with MinGW-w64 (posix threads, for std::thread).
# OUT_DIR defaults to build/native/<target>.

set -euo pipefail

TARGET=${1:?usage: docker-build.sh linux|windows [OUT_DIR]}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=${2:-$ROOT/build/native/$TARGET}
mkdir -p "$OUT"
ENGINE=${CONTAINER_ENGINE:-docker}

case "$TARGET" in
    linux)
        IMAGE=almalinux:8
        SETUP='dnf -y -q install gcc-toolset-13-gcc-c++ cmake make binutils curl tar gzip >/dev/null
               source /opt/rh/gcc-toolset-13/enable'
        ;;
    windows)
        IMAGE=debian:trixie
        SETUP='apt-get update -qq && apt-get install -y -qq g++-mingw-w64-x86-64-posix cmake make curl ca-certificates >/dev/null'
        ;;
    *)
        echo "unknown target $TARGET" >&2
        exit 2
        ;;
esac

"$ENGINE" run --rm -v "$ROOT/native:/native:ro" -v "$OUT:/out" "$IMAGE" bash -euo pipefail -c "
    $SETUP
    WORK=/tmp/work bash /native/build.sh $TARGET /out
    chown -R $(id -u):$(id -g) /out
"
