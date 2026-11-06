"""Subtitle pictures into the lines Tesseract reads best, with the standard library only.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The host sends each subtitle as an 8-bit grayscale PGM (binary, `P5`): dark
text on a light background, as Subtitld draws a decoded VobSub or PGS picture
for OCR. The picture is cut into its lines at the rows without ink, each line
cropped to its ink, enlarged to about the height Tesseract's models were
trained on, and given a white margin.

A line is read with page segmentation "single line", which is more accurate on
subtitles than letting Tesseract find the lines itself: it never merges two
lines, nor drops a short one such as "- No!".
"""

from __future__ import annotations

from dataclasses import dataclass

INK = 128          # a pixel darker than this is text
LINE_HEIGHT = 56   # pixels a line is enlarged to, about, before the margin
MAX_SCALE = 4
MARGIN = 12


@dataclass
class Gray:
    width: int
    height: int
    pixels: bytes  # row after row, one byte per pixel

    def row(self, y: int) -> bytes:
        return self.pixels[y * self.width:(y + 1) * self.width]


class ImageError(ValueError):
    """Not a picture the add-on can read."""


def _tokens(data: bytes, count: int) -> tuple[list[bytes], int]:
    """The first `count` header fields of a PNM file, skipping comments, and
    the offset just past the single whitespace that ends the header."""
    fields: list[bytes] = []
    i = 0
    while len(fields) < count:
        while i < len(data) and data[i:i + 1].isspace():
            i += 1
        if data[i:i + 1] == b'#':
            while i < len(data) and data[i:i + 1] not in (b'\n', b'\r'):
                i += 1
            continue
        start = i
        while i < len(data) and not data[i:i + 1].isspace():
            i += 1
        if start == i:
            raise ImageError('truncated header')
        fields.append(data[start:i])
    return fields, i + 1


def read_pgm(data: bytes) -> Gray:
    """A binary 8-bit PGM."""
    if data[:2] != b'P5':
        raise ImageError('not a binary PGM (P5)')
    (_, width, height, maxval), offset = _tokens(data, 4)
    try:
        width, height, maxval = int(width), int(height), int(maxval)
    except ValueError:
        raise ImageError('bad PGM header') from None
    if width <= 0 or height <= 0 or not 0 < maxval < 256:
        raise ImageError(f'unsupported PGM: {width}x{height}, maxval {maxval}')
    pixels = data[offset:offset + width * height]
    if len(pixels) != width * height:
        raise ImageError('truncated PGM')
    if maxval != 255:
        table = bytes(min(255, round(v * 255 / maxval)) for v in range(256))
        pixels = pixels.translate(table)
    return Gray(width, height, pixels)


def _inverted(image: Gray) -> Gray:
    return Gray(image.width, image.height, image.pixels.translate(bytes(range(255, -1, -1))))


def light_background(image: Gray) -> Gray:
    """The picture with a light background: inverted when its border is dark,
    so a host that sent light text on dark still gets read."""
    border = image.row(0) + image.row(image.height - 1)
    border += bytes(image.pixels[y * image.width] for y in range(image.height))
    border += bytes(image.pixels[y * image.width + image.width - 1] for y in range(image.height))
    return _inverted(image) if sum(border) < 128 * len(border) else image


def _bands(image: Gray) -> list[list[int]]:
    """Runs of rows with ink, as [first, last + 1]."""
    bands: list[list[int]] = []
    start = None
    for y in range(image.height):
        inked = min(image.row(y)) < INK
        if inked and start is None:
            start = y
        elif not inked and start is not None:
            bands.append([start, y])
            start = None
    if start is not None:
        bands.append([start, image.height])
    return bands


def _merge_marks(bands: list[list[int]]) -> list[list[int]]:
    """Fold bands too short to be a line (accents above capitals, the dots of
    an ellipsis drawn apart) into the nearer neighbour."""
    if len(bands) < 2:
        return bands
    tallest = max(b - a for a, b in bands)
    bands = [list(b) for b in bands]
    changed = True
    while changed and len(bands) > 1:
        changed = False
        for i, (a, b) in enumerate(bands):
            if b - a >= 0.45 * tallest:
                continue
            above = a - bands[i - 1][1] if i > 0 else None
            below = bands[i + 1][0] - b if i + 1 < len(bands) else None
            j = i - 1 if below is None or (above is not None and above <= below) else i + 1
            bands[j] = [min(bands[j][0], a), max(bands[j][1], b)]
            del bands[i]
            changed = True
            break
    return bands


def _crop(image: Gray, top: int, bottom: int) -> Gray:
    rows = [image.row(y) for y in range(top, bottom)]
    columns = [x for x in range(image.width) if any(row[x] < INK for row in rows)] if rows else []
    if not columns:
        return Gray(0, 0, b'')
    left, right = columns[0], columns[-1] + 1
    return Gray(right - left, bottom - top, b''.join(row[left:right] for row in rows))


def _enlarge(image: Gray, factor: int) -> Gray:
    """Nearest-neighbour enlargement by a whole factor, at C speed."""
    if factor == 1:
        return image
    width = image.width * factor
    out = bytearray()
    for y in range(image.height):
        row = image.row(y)
        wide = bytearray(width)
        for k in range(factor):
            wide[k::factor] = row
        out += bytes(wide) * factor
    return Gray(width, image.height * factor, bytes(out))


def _margin(image: Gray, margin: int = MARGIN) -> Gray:
    width = image.width + 2 * margin
    blank = b'\xff' * width
    side = b'\xff' * margin
    rows = [blank] * margin + [side + image.row(y) + side for y in range(image.height)] + [blank] * margin
    return Gray(width, image.height + 2 * margin, b''.join(rows))


def lines(image: Gray) -> list[Gray]:
    """The picture's text lines, top to bottom, ready for Tesseract."""
    image = light_background(image)
    out = []
    for top, bottom in _merge_marks(_bands(image)):
        line = _crop(image, top, bottom)
        if line.width == 0 or line.height < 3:
            continue
        factor = max(1, min(MAX_SCALE, round(LINE_HEIGHT / line.height)))
        out.append(_margin(_enlarge(line, factor)))
    return out
