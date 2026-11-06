#!/usr/bin/env python3
"""Assemble THIRD_PARTY_LICENSES/ for a release archive, and refuse native code without a notice.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Besides the add-on's own code, the frozen bundle carries libtesseract
(Tesseract, Apache-2.0, with Leptonica and, on Linux and Windows, the GCC and
MinGW-w64 runtimes linked in), the English tessdata_fast model (Apache-2.0),
certifi's CA bundle (MPL-2.0), CPython with the native libraries its standard
modules link against (OpenSSL among them), the PyInstaller bootloader
(GPL-2.0-or-later with the bootloader exception) and, on Windows, the C
runtime Python ships with.

The notices committed in THIRD_PARTY_LICENSES/ are copied as they are. Two
texts depend on the build machine and are harvested from it: CPython's licence,
from the interpreter running this script (the one that froze the bundle), and
PyInstaller's, from its installed distribution.

Then every native file in the bundle (ELF, Mach-O or PE, whatever its name)
must match a rule below, and the notice that rule names must be in the output.
Anything else fails the build: a library new to the bundle has to come with its
licence before it ships. The result is indexed in THIRD_PARTY_LICENSES/README.md.

    python tools/assemble_licences.py --bundle dist/tesseract-addon --out release-staging/THIRD_PARTY_LICENSES
"""

from __future__ import annotations

import argparse
import platform
import re
import shutil
import sys
import sysconfig
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# What libtesseract links in, by platform (native/build.sh).
_TESSERACT = {
    'linux': 'tesseract/LICENSE leptonica/LICENSE gcc-runtime/COPYING.RUNTIME gcc-runtime/COPYING3',
    'win32': 'tesseract/LICENSE leptonica/LICENSE gcc-runtime/COPYING.RUNTIME gcc-runtime/COPYING3 '
             'mingw-w64/COPYING.MinGW-w64-runtime.txt mingw-w64/winpthreads-COPYING mingw-w64/COPYING',
    'darwin': 'tesseract/LICENSE leptonica/LICENSE',
}['win32' if sys.platform == 'win32' else 'darwin' if sys.platform == 'darwin' else 'linux']

# (pattern on the lower-cased file name, notices inside THIRD_PARTY_LICENSES/
# separated by spaces, component)
RULES = [
    (r'^tesseract-addon(\.exe)?$', 'pyinstaller/COPYING.txt', 'PyInstaller bootloader'),
    (r'^libtesseract\.(so|dylib|dll)$', _TESSERACT, 'Tesseract with Leptonica'),
    (r'^(libpython3[\d.]*\.(so|dylib)[\d.]*|python3\d*\.dll|python)$', 'python/LICENSE.txt', 'CPython'),
    (r'(\.cpython-[^/]+\.(so|dylib)|\.pyd)$', 'python/LICENSE.txt', 'CPython extension module'),
    (r'^libffi[-.\d]*\.(so|dylib|dll)', 'python-runtime-libs/libffi-LICENSE', 'libffi'),
    (r'^libexpat[-.\d]*\.(so|dylib|dll)', 'python-runtime-libs/expat-COPYING', 'Expat'),
    (r'^(libz\.(so|dylib)|zlib\d*\.dll)', 'python-runtime-libs/zlib-LICENSE', 'zlib'),
    (r'^lib(ssl|crypto)[-.\d]', 'python-runtime-libs/openssl-LICENSE.txt', 'OpenSSL'),
    # Excluded from the bundle by pyinstaller.spec. Listed so that, should one
    # come back, the build names the notice to commit instead of a bare file.
    (r'^libbz2', 'python-runtime-libs/bzip2-LICENSE', 'bzip2'),
    (r'^liblzma', 'python-runtime-libs/xz-COPYING', 'XZ Utils'),
    (r'^libzstd', 'python-runtime-libs/zstd-LICENSE', 'zstd'),
    (r'^libmpdec', 'python-runtime-libs/mpdecimal-LICENSE', 'mpdecimal'),
    (r'^(vcruntime140(_1)?|msvcp140\w*)\.dll$', 'msvc-runtime/NOTICE.txt', 'Microsoft C runtime'),
    (r'^(ucrtbase|api-ms-win-[\w-]+)\.dll$', 'msvc-runtime/NOTICE.txt', 'Universal C runtime'),
]
_COMPILED = [(re.compile(pattern), notice, component) for pattern, notice, component in RULES]

_MACHO_MAGIC = {b'\xfe\xed\xfa\xce', b'\xfe\xed\xfa\xcf', b'\xce\xfa\xed\xfe',
                b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def is_native(path: Path) -> bool:
    with path.open('rb') as handle:
        head = handle.read(4)
    return head == b'\x7fELF' or head[:2] == b'MZ' or head in _MACHO_MAGIC


def rule_for(name: str) -> tuple[str, str] | None:
    lowered = name.lower()
    for pattern, notice, component in _COMPILED:
        if pattern.search(lowered):
            return notice, component
    return None


def cpython_licence() -> Path:
    stdlib = Path(sysconfig.get_paths()['stdlib'])
    for candidate in (Path(sys.base_prefix) / 'LICENSE.txt', stdlib / 'LICENSE.txt',
                      Path(sys.base_prefix) / 'LICENSE'):
        if candidate.is_file():
            return candidate
    raise SystemExit(f'FATAL: no CPython licence next to {sys.base_prefix} or in {stdlib}')


def pyinstaller_licence() -> tuple[Path, str]:
    try:
        dist = distribution('pyinstaller')
    except PackageNotFoundError:
        raise SystemExit('FATAL: PyInstaller is not installed in this interpreter') from None
    for entry in dist.files or []:
        if Path(str(entry)).name == 'COPYING.txt':
            path = Path(dist.locate_file(entry))
            if path.is_file():
                return path, dist.version
    raise SystemExit('FATAL: PyInstaller is installed but ships no COPYING.txt')


def assemble(bundle: Path, out: Path) -> list[tuple[str, str, str]]:
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(REPO_ROOT / 'THIRD_PARTY_LICENSES', out)

    python_licence = cpython_licence()
    (out / 'python').mkdir()
    shutil.copyfile(python_licence, out / 'python' / 'LICENSE.txt')
    (out / 'python' / 'SOURCE.txt').write_text(
        f'CPython {platform.python_version()}, the interpreter that froze this add-on.\n'
        f'Licence copied from {python_licence}.\n', encoding='utf-8')
    licence, version = pyinstaller_licence()
    (out / 'pyinstaller').mkdir()
    shutil.copyfile(licence, out / 'pyinstaller' / 'COPYING.txt')
    (out / 'pyinstaller' / 'SOURCE.txt').write_text(
        f'PyInstaller {version}. Its bootloader is the add-on executable; the GPL-2.0\n'
        f'bootloader exception in COPYING.txt allows distributing it with this program.\n',
        encoding='utf-8')

    rows: list[tuple[str, str, str]] = []
    problems: list[str] = []
    for path in sorted(p for p in bundle.rglob('*') if p.is_file() and not p.is_symlink()):
        relative = 'bin/' + path.relative_to(bundle).as_posix()
        if path.suffix == '.traineddata':
            rows.append((relative, 'tessdata_fast model', 'tessdata_fast/LICENSE'))
            continue
        if path.name == 'cacert.pem':
            rows.append((relative, 'certifi CA bundle', 'certifi/LICENSE'))
            continue
        if not is_native(path):
            continue
        match = rule_for(path.name)
        if match is None:
            problems.append(f'{relative}: native file with no licence rule')
            continue
        notices, component = match
        missing = [n for n in notices.split() if not (out / n).is_file()]
        if missing:
            problems.append(f'{relative}: {component} needs THIRD_PARTY_LICENSES/{", ".join(missing)}, '
                            f'which is not committed')
            continue
        rows.append((relative, component, notices))
    if not any(row[1] == 'tessdata_fast model' for row in rows):
        problems.append('the bundle has no tessdata_fast model')
    if not any(row[1].startswith('Tesseract') for row in rows):
        problems.append('the bundle has no libtesseract')
    if problems:
        raise SystemExit('FATAL: licences are incomplete:\n  ' + '\n  '.join(problems))

    lines = ['# Third-party licences', '',
             'Everything frozen into this add-on, and the notice in this folder that covers it.', '',
             '| File | Component | Notice |', '|------|-----------|--------|']
    lines += [f'| `{relative}` | {component} | ' + ', '.join(f'[{n}]({n})' for n in notices.split()) + ' |'
              for relative, component, notices in rows]
    (out / 'README.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return rows


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--bundle', type=Path, required=True, help='the PyInstaller onedir output')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    rows = assemble(args.bundle, args.out)
    print(f'{len(rows)} bundled file(s) covered:')
    for relative, component, notice in rows:
        print(f'  {relative}  ->  {notice}  ({component})')
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv[1:]))
