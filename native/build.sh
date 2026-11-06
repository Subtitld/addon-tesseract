#!/usr/bin/env bash
# Build libtesseract with Leptonica linked in, as one shared library.
#
# Copyright 2026 Subtitld contributors
# SPDX-License-Identifier: Apache-2.0
#
#     native/build.sh linux|windows|macos OUT_DIR
#
# The add-on hands Tesseract raw grayscale pixels (TessBaseAPISetImage), so
# Leptonica is built without any image codec: no zlib, libpng, libjpeg,
# libtiff, libwebp, openjpeg or giflib, and so none of their licences to ship.
# Tesseract is built without libarchive, libcurl, the legacy engine (the
# tessdata_fast models are LSTM-only), OpenMP and the training tools.
#
# The C++ runtime is linked statically, so the library depends on nothing but
# the system's C library:
#   linux   - run inside AlmaLinux 8 (glibc 2.28) by native/docker-build.sh;
#             gcc-toolset's libstdc++ and libgcc are linked in.
#   windows - cross-compiled with MinGW-w64 by native/docker-build.sh;
#             libstdc++, libgcc and winpthreads are linked in.
#   macos   - on a Mac (CI), against libc++ from the system.
#
# Output: OUT_DIR/libtesseract.{so,dll,dylib} and OUT_DIR/BUILD-INFO.txt.

set -euo pipefail

TARGET=${1:?usage: build.sh linux|windows|macos OUT_DIR}
OUT=$(mkdir -p "${2:?usage: build.sh linux|windows|macos OUT_DIR}" && cd "$2" && pwd)
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck source=versions.env
source "$HERE/versions.env"

WORK=${WORK:-$(mktemp -d)}
JOBS=${JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || sysctl -n hw.ncpu)}
PREFIX="$WORK/prefix"
mkdir -p "$WORK/src" "$PREFIX"

fetch() {  # url file sha256
    local url=$1 file="$WORK/src/$2" sum=$3
    [ -f "$file" ] || curl -fsSL --retry 3 -o "$file" "$url"
    if command -v sha256sum >/dev/null; then
        echo "$sum  $file" | sha256sum -c -
    else
        echo "$sum  $file" | shasum -a 256 -c -
    fi
    tar -xzf "$file" -C "$WORK/src"
}

fetch "https://github.com/DanBloomberg/leptonica/releases/download/${LEPTONICA_VERSION}/leptonica-${LEPTONICA_VERSION}.tar.gz" \
      "leptonica-${LEPTONICA_VERSION}.tar.gz" "$LEPTONICA_SHA256"
fetch "https://github.com/tesseract-ocr/tesseract/archive/refs/tags/${TESSERACT_VERSION}.tar.gz" \
      "tesseract-${TESSERACT_VERSION}.tar.gz" "$TESSERACT_SHA256"

COMMON=(-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$PREFIX" -DCMAKE_PREFIX_PATH="$PREFIX"
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON)
SHARED_LDFLAGS=''
case "$TARGET" in
    linux)
        SHARED_LDFLAGS='-static-libstdc++ -static-libgcc -Wl,--exclude-libs,ALL'
        LIBRARY=libtesseract.so
        ;;
    windows)
        COMMON+=(-DCMAKE_SYSTEM_NAME=Windows
                 -DCMAKE_C_COMPILER=x86_64-w64-mingw32-gcc-posix
                 -DCMAKE_CXX_COMPILER=x86_64-w64-mingw32-g++-posix
                 -DCMAKE_RC_COMPILER=x86_64-w64-mingw32-windres
                 -DCMAKE_FIND_ROOT_PATH="$PREFIX"
                 -DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER
                 -DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY
                 -DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY
                 -DCMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY
                 -DLEPT_TIFF_RESULT=1)  # Tesseract's TIFF probe cannot run when cross-compiling
        # Tesseract asks for Ws2_32, MinGW ships libws2_32.a: on a case-sensitive
        # file system the name has to be offered as written.
        mkdir -p "$WORK/winlibs"
        ln -sf "$(x86_64-w64-mingw32-gcc-posix -print-file-name=libws2_32.a)" "$WORK/winlibs/libWs2_32.a"
        SHARED_LDFLAGS="-static -static-libstdc++ -static-libgcc -L$WORK/winlibs"
        LIBRARY=libtesseract.dll
        ;;
    macos)
        export MACOSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-11.0}
        COMMON+=(-DCMAKE_OSX_ARCHITECTURES="${MACOS_ARCH:-arm64}" -DCMAKE_OSX_DEPLOYMENT_TARGET="$MACOSX_DEPLOYMENT_TARGET")
        LIBRARY=libtesseract.dylib
        ;;
    *)
        echo "unknown target $TARGET" >&2
        exit 2
        ;;
esac

# Leptonica: static, no codecs, folded into libtesseract below. NO_CONSOLE_IO
# silences its messages: every TessBaseAPI makes a debug font from TIFF data,
# which a codec-less Leptonica cannot decode, and says so on stderr.
cmake -S "$WORK/src/leptonica-${LEPTONICA_VERSION}" -B "$WORK/build-leptonica" "${COMMON[@]}" \
      -DCMAKE_C_FLAGS="-DNO_CONSOLE_IO" \
      -DBUILD_SHARED_LIBS=OFF -DBUILD_PROG=OFF -DSW_BUILD=OFF \
      -DENABLE_ZLIB=OFF -DENABLE_PNG=OFF -DENABLE_GIF=OFF -DENABLE_JPEG=OFF \
      -DENABLE_TIFF=OFF -DENABLE_WEBP=OFF -DENABLE_OPENJPEG=OFF
cmake --build "$WORK/build-leptonica" --parallel "$JOBS"
cmake --install "$WORK/build-leptonica"

# Tesseract: shared, with its SIMD kernels (picked at run time by CPU).
cmake -S "$WORK/src/tesseract-${TESSERACT_VERSION}" -B "$WORK/build-tesseract" "${COMMON[@]}" \
      -DBUILD_SHARED_LIBS=ON -DBUILD_TRAINING_TOOLS=OFF -DBUILD_TESTS=OFF \
      -DDISABLED_LEGACY_ENGINE=ON -DGRAPHICS_DISABLED=ON -DOPENMP_BUILD=OFF \
      -DDISABLE_TIFF=ON -DDISABLE_ARCHIVE=ON -DDISABLE_CURL=ON \
      -DENABLE_NATIVE=OFF -DENABLE_LTO=OFF -DENABLE_CCACHE=OFF -DINSTALL_CONFIGS=OFF \
      -DCMAKE_SHARED_LINKER_FLAGS="$SHARED_LDFLAGS" \
      -DLeptonica_DIR="$PREFIX/lib/cmake/leptonica"
cmake --build "$WORK/build-tesseract" --parallel "$JOBS" --target libtesseract

built=$(find "$WORK/build-tesseract" -maxdepth 2 \( -name 'libtesseract*.so*' -o -name 'libtesseract*.dll' \
        -o -name 'libtesseract*.dylib' \) -type f | head -n 1)
[ -n "$built" ] || { echo "no library built" >&2; exit 1; }
cp "$built" "$OUT/$LIBRARY"
case "$TARGET" in
    linux)   strip --strip-unneeded "$OUT/$LIBRARY" ;;
    windows) x86_64-w64-mingw32-strip --strip-unneeded "$OUT/$LIBRARY" ;;
    macos)   install_name_tool -id "@rpath/$LIBRARY" "$OUT/$LIBRARY"
             strip -x "$OUT/$LIBRARY" ;;
esac

{
    echo "libtesseract ${TESSERACT_VERSION} with Leptonica ${LEPTONICA_VERSION} linked in"
    echo "target: $TARGET"
    echo "built: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    case "$TARGET" in
        linux)   echo "compiler: $(g++ --version | head -n 1)"; echo "needs:"; objdump -p "$OUT/$LIBRARY" | grep NEEDED ;;
        windows) echo "compiler: $(x86_64-w64-mingw32-g++-posix --version | head -n 1)"; echo "needs:"
                 x86_64-w64-mingw32-objdump -p "$OUT/$LIBRARY" | grep 'DLL Name' ;;
        macos)   echo "compiler: $(c++ --version | head -n 1)"; echo "needs:"; otool -L "$OUT/$LIBRARY" ;;
    esac
} > "$OUT/BUILD-INFO.txt"
cat "$OUT/BUILD-INFO.txt"
