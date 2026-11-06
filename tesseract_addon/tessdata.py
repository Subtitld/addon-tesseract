"""The language models: English in the archive, the others downloaded once, when first needed.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Models are tessdata_fast files (Apache-2.0), at the commit and with the size
and sha256 pinned in tessdata_pins.py. A downloaded model is kept in the
host's model folder ($SUBTITLD_MODELS_DIR/tesseract/), shared by every later
run and kept when the add-on is updated; a file that does not match its pin is
never used, and is downloaded again.

HTTPS: the frozen Linux bundle carries the build machine's OpenSSL, which
looks for CA certificates where AlmaLinux keeps them, so the CA bundle this
machine's distribution keeps is named explicitly. The macOS bundle's OpenSSL
does not read the keychain, so it trusts certifi's bundle. Windows reads its
own certificate store. SSL_CERT_FILE or SSL_CERT_DIR, when set, always win.
"""

from __future__ import annotations

import hashlib
import os
import ssl
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable

from tesseract_addon.tessdata_pins import PINS, TESSDATA_COMMIT, TESSDATA_URL

BUNDLED = ('eng',)
_CHUNK = 1 << 16

# Where Linux distributions keep their CA bundle (the list Go's crypto/x509 checks).
SYSTEM_CA_FILES = (
    '/etc/ssl/certs/ca-certificates.crt',                 # Debian, Ubuntu, Arch, Gentoo
    '/etc/pki/tls/certs/ca-bundle.crt',                   # Fedora, RHEL
    '/etc/ssl/ca-bundle.pem',                             # openSUSE
    '/etc/pki/tls/cacert.pem',                            # OpenELEC
    '/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem',  # CentOS, RHEL 7
    '/etc/ssl/cert.pem',                                  # Alpine, macOS
)


class TessdataError(Exception):
    """A model that cannot be had: unknown, or its download failed."""


def size_of(model: str) -> int:
    return PINS[model][0] if model in PINS else 0


def _cache_dir() -> Path:
    base = (os.environ.get('SUBTITLD_MODELS_DIR') or '').strip()
    if not base:
        base = os.path.join(os.path.expanduser('~'), '.cache', 'subtitld', 'models')
    return Path(base) / 'tesseract' / f'tessdata_fast-{TESSDATA_COMMIT[:12]}'


def _matches(path: Path, model: str) -> bool:
    size, digest = PINS[model]
    try:
        if path.stat().st_size != size:
            return False
        sha = hashlib.sha256()
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b''):
                sha.update(chunk)
        return sha.hexdigest() == digest
    except OSError:
        return False


def local_path(model: str, bundled_dir: Path | None) -> Path | None:
    """The model's file on this computer, verified, or None."""
    if model not in PINS:
        raise TessdataError(f'no tessdata_fast model named {model!r}')
    for folder in (bundled_dir, _cache_dir()):
        if folder is not None:
            candidate = folder / f'{model}.traineddata'
            if candidate.is_file() and _matches(candidate, model):
                return candidate
    return None


def ssl_context() -> ssl.SSLContext:
    if os.environ.get('SSL_CERT_FILE') or os.environ.get('SSL_CERT_DIR'):
        return ssl.create_default_context()
    if sys.platform == 'win32':
        return ssl.create_default_context()
    if sys.platform.startswith('linux'):
        for path in SYSTEM_CA_FILES:
            if os.path.isfile(path):
                return ssl.create_default_context(cafile=path)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def download(model: str, progress: Callable[[int, int], None] | None = None,
             cancelled: Callable[[], bool] | None = None) -> Path:
    """Download the model into the cache, verify it, and return its path.

    `progress(done, total)` is called as bytes arrive; `cancelled()` returning
    True stops the download (TessdataError)."""
    if model not in PINS:
        raise TessdataError(f'no tessdata_fast model named {model!r}')
    size, digest = PINS[model]
    folder = _cache_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f'{model}.traineddata'
    url = TESSDATA_URL.format(commit=TESSDATA_COMMIT, name=model)
    request = urllib.request.Request(url, headers={'User-Agent': 'subtitld-addon-tesseract'})
    handle, temporary = tempfile.mkstemp(prefix=f'.{model}.', suffix='.part', dir=folder)
    try:
        sha = hashlib.sha256()
        done = 0
        with os.fdopen(handle, 'wb') as out, \
                urllib.request.urlopen(request, timeout=60, context=ssl_context()) as response:
            while True:
                if cancelled is not None and cancelled():
                    raise TessdataError('cancelled')
                chunk = response.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
                sha.update(chunk)
                done += len(chunk)
                if done > size:
                    raise TessdataError(f'{model}.traineddata is larger than its pinned size')
                if progress is not None:
                    progress(done, size)
        if done != size or sha.hexdigest() != digest:
            raise TessdataError(f'{model}.traineddata does not match its pinned sha256; not used')
        os.replace(temporary, target)
        return target
    except OSError as exc:
        raise TessdataError(f'could not download {model}.traineddata: {exc}') from exc
    finally:
        if os.path.exists(temporary):
            os.remove(temporary)
