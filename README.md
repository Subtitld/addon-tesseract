# Tesseract OCR add-on for Subtitld

Reads picture subtitles as text with [Tesseract](https://github.com/tesseract-ocr/tesseract) 5.5, so Subtitld can open DVD VobSub (`.idx`/`.sub`) and Blu-ray PGS (`.sup`) files, and the picture tracks inside videos, as subtitles you can edit.

- **Offline, CPU only.** No GPU, no account. About 20 ms a subtitle on a laptop, so a feature film takes well under a minute.
- **English is in the archive.** Any of 100 other languages is downloaded once, the first time a subtitle in it is read: 1–12 MB from [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast), checked against a pinned sha256, kept in Subtitld's model folder.
- **Subtitld does the rest.** It decodes the pictures, keeps their times and positions, and sends this add-on one picture per subtitle (task `ocr.recognize`).

## Settings

| Setting | Default | |
|---|---|---|
| Straight quotes | on | Writes “curly” quotes as "straight" ones, as most subtitle files do. |

## How it is built

`native/build.sh` builds libtesseract 5.5.4 with Leptonica 1.87.0 linked in, from pinned sources, as one library that needs nothing but the system's C library: no image codecs (the add-on hands Tesseract raw pixels), no libarchive or libcurl, no legacy engine, the C++ runtime linked statically. Linux builds run in AlmaLinux 8 (glibc 2.28), Windows builds are cross-compiled with MinGW-w64; `native/docker-build.sh` runs both in containers:

```bash
native/docker-build.sh linux
python tools/stage_assets.py
pytest
pyinstaller pyinstaller.spec --noconfirm
```

The Python side uses the standard library only (plus certifi's CA bundle for downloads on macOS): `tesseract_addon/engine.py` calls Tesseract's C API through ctypes, `images.py` cuts each picture into lines, `cleanup.py` fixes the usual slips (`|` read for `I`, ASCII punctuation in Chinese and Japanese). The release workflow builds and tests every platform's archive the way Subtitld installs it.

## Licence

Apache-2.0. Tesseract and tessdata_fast are Apache-2.0, Leptonica is BSD-2-Clause-style; every archive carries `THIRD_PARTY_LICENSES/` with the notices of everything in it.
