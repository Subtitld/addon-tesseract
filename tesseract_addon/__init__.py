"""Tesseract OCR add-on for Subtitld.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The package exists so the entry point can run both from source
(`python -m tesseract_addon`) and as the frozen PyInstaller binary
(`bin/tesseract-addon`).
"""

__version__ = '26.11.1'
ADDON_ID = 'tesseract'
PROTOCOL_VERSION = 1
