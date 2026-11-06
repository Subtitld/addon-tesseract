"""Tesseract's C API, through ctypes.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The add-on bundles its own libtesseract (native/build.sh): Tesseract 5.5 with
Leptonica linked in and no image codecs, since every image reaches it as raw
8-bit grayscale pixels. A `Reader` is one TessBaseAPI handle with one language
loaded. Handles are not thread-safe, so each worker thread owns one; ctypes
releases the GIL during the native calls, so several readers work in
parallel.

The model is handed over as bytes (TessBaseAPIInit5), not as a folder:
Tesseract opens files with the C library's fopen, which on Windows cannot open
a path outside the ANSI code page, such as a user folder with an accented name.
"""

from __future__ import annotations

import ctypes
import os
import threading

# TessPageSegMode
PSM_SINGLE_BLOCK = 6
PSM_SINGLE_LINE = 7
# TessOcrEngineMode: the library is built without the legacy engine.
_OEM_LSTM_ONLY = 1

# Quiet the library: no "Estimating resolution" or "Empty page" chatter on
# stderr for every subtitle.
_QUIET = {'debug_file': os.devnull}


class EngineError(Exception):
    """The library failed to load, or refused a model."""


class Engine:
    """The loaded library. One per process."""

    def __init__(self, library_path: str):
        try:
            lib = ctypes.CDLL(library_path)
        except OSError as exc:
            raise EngineError(f'cannot load {library_path}: {exc}') from exc
        handle = ctypes.c_void_p
        lib.TessVersion.restype = ctypes.c_char_p
        lib.TessBaseAPICreate.restype = handle
        lib.TessBaseAPIDelete.argtypes = [handle]
        lib.TessBaseAPIEnd.argtypes = [handle]
        lib.TessBaseAPIInit5.restype = ctypes.c_int
        lib.TessBaseAPIInit5.argtypes = [
            handle, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p), ctypes.c_int,
            ctypes.POINTER(ctypes.c_char_p), ctypes.POINTER(ctypes.c_char_p), ctypes.c_size_t,
            ctypes.c_int]
        lib.TessBaseAPISetVariable.restype = ctypes.c_int
        lib.TessBaseAPISetVariable.argtypes = [handle, ctypes.c_char_p, ctypes.c_char_p]
        lib.TessBaseAPISetPageSegMode.argtypes = [handle, ctypes.c_int]
        lib.TessBaseAPISetImage.argtypes = [handle, ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
                                            ctypes.c_int, ctypes.c_int]
        lib.TessBaseAPISetSourceResolution.argtypes = [handle, ctypes.c_int]
        # c_void_p, not c_char_p: the string must be freed with TessDeleteText.
        lib.TessBaseAPIGetUTF8Text.restype = ctypes.c_void_p
        lib.TessBaseAPIGetUTF8Text.argtypes = [handle]
        lib.TessDeleteText.argtypes = [ctypes.c_void_p]
        lib.TessBaseAPIMeanTextConf.restype = ctypes.c_int
        lib.TessBaseAPIMeanTextConf.argtypes = [handle]
        lib.TessBaseAPIClear.argtypes = [handle]
        self._lib = lib

    @property
    def version(self) -> str:
        return self._lib.TessVersion().decode('ascii', 'replace')

    def reader(self, model: str, data: bytes) -> 'Reader':
        return Reader(self._lib, model, data)


class Reader:
    """One TessBaseAPI handle, one language. Use from one thread at a time."""

    def __init__(self, lib, model: str, data: bytes):
        self._lib = lib
        self._lock = threading.Lock()
        self._handle = lib.TessBaseAPICreate()
        if not self._handle:
            raise EngineError('TessBaseAPICreate failed')
        names = (ctypes.c_char_p * len(_QUIET))(*(k.encode() for k in _QUIET))
        values = (ctypes.c_char_p * len(_QUIET))(*(v.encode() for v in _QUIET))
        status = lib.TessBaseAPIInit5(self._handle, data, len(data), model.encode('ascii'), _OEM_LSTM_ONLY,
                                      None, 0, names, values, len(_QUIET), 0)
        if status != 0:
            lib.TessBaseAPIDelete(self._handle)
            self._handle = None
            raise EngineError(f'Tesseract could not load the {model} model')

    def read(self, pixels: bytes, width: int, height: int, mode: int = PSM_SINGLE_LINE) -> tuple[str, int]:
        """Text in an 8-bit grayscale image (dark text on a light background),
        and Tesseract's mean confidence, 0-100."""
        with self._lock:
            lib, handle = self._lib, self._handle
            lib.TessBaseAPISetPageSegMode(handle, mode)
            lib.TessBaseAPISetImage(handle, pixels, width, height, 1, width)
            # Subtitle lines are scaled to about 300 dpi text heights (images.py).
            lib.TessBaseAPISetSourceResolution(handle, 300)
            pointer = lib.TessBaseAPIGetUTF8Text(handle)
            try:
                text = ctypes.string_at(pointer).decode('utf-8', 'replace') if pointer else ''
            finally:
                if pointer:
                    lib.TessDeleteText(pointer)
            confidence = lib.TessBaseAPIMeanTextConf(handle)
            lib.TessBaseAPIClear(handle)
            return text, confidence

    def close(self) -> None:
        with self._lock:
            if self._handle:
                self._lib.TessBaseAPIEnd(self._handle)
                self._lib.TessBaseAPIDelete(self._handle)
                self._handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:  # noqa: BLE001 — interpreter shutdown
            pass
