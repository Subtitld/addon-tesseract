#!/usr/bin/env python3
"""Put the native library and the bundled model into build/assets/ for PyInstaller.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

    python tools/stage_assets.py [--native build/native/linux]

The library comes from native/build.sh's output folder (by default
build/native/<this platform>). The bundled models (English) come from the
pinned tessdata_fast commit and are checked against their sha256.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tesseract_addon import tessdata  # noqa: E402
from tesseract_addon.__main__ import library_name  # noqa: E402

ASSETS = REPO_ROOT / 'build' / 'assets'


def default_native() -> Path:
    target = 'macos' if sys.platform == 'darwin' else 'windows' if sys.platform == 'win32' else 'linux'
    return REPO_ROOT / 'build' / 'native' / target


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--native', type=Path, default=default_native())
    args = parser.parse_args(argv)

    library = args.native / library_name()
    if not library.is_file():
        raise SystemExit(f'FATAL: {library} is missing. Build it with native/build.sh first.')
    (ASSETS / 'tessdata').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(library, ASSETS / library.name)
    shutil.copyfile(args.native / 'BUILD-INFO.txt', ASSETS / 'BUILD-INFO.txt')
    print(f'{ASSETS / library.name}: {library.stat().st_size:,} bytes')

    for model in tessdata.BUNDLED:
        path = tessdata.local_path(model, ASSETS / 'tessdata') or tessdata.download(model)
        target = ASSETS / 'tessdata' / f'{model}.traineddata'
        if path != target:
            shutil.copyfile(path, target)
        if tessdata.local_path(model, ASSETS / 'tessdata') != target:
            raise SystemExit(f'FATAL: {target} does not match its pin')
        print(f'{target}: {target.stat().st_size:,} bytes, sha256 verified')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
