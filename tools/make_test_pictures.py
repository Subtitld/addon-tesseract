#!/usr/bin/env python3
"""Draw the subtitle pictures the tests read: tests/data/*.pgm and expected.json.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Development only (needs Pillow and the Liberation fonts); the pictures are
committed, so the tests need neither. Each subtitle is drawn the way a disc
carries it, then turned into what Subtitld sends the add-on: dark text on a
light background, 8-bit grayscale.

- dvd: VobSub-like, 24 px Liberation Sans Bold, white with a black outline,
  four palette colours, no antialiasing;
- pgs: Blu-ray-like, 54 px, antialiased;
- small: 19 px serif bold italic, yellow, 1 px outline, four colours.

    python tools/make_test_pictures.py
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / 'tests' / 'data'
FONTS = '/usr/share/fonts/truetype/liberation/'

PICTURES = [
    ('dvd', 'eng', "I told you not to come back here.\nWhy didn't you listen?"),
    ('dvd', 'eng', "- Are you okay?\n- I'm fine, thanks."),
    ('pgs', 'eng', 'Where were you last night?'),
    ('small', 'eng', "The meeting's at noon,\nroom 214."),
    ('dvd', 'por', 'Ação, coração e emoção\nnão faltam aqui.'),
    ('pgs', 'por', 'É difícil explicar\no que aconteceu.'),
]


def render(text: str, style: str) -> Image.Image:
    size, stroke, face = {'dvd': (24, 2, 'LiberationSans-Bold.ttf'), 'pgs': (54, 3, 'LiberationSans-Bold.ttf'),
                          'small': (19, 1, 'LiberationSerif-BoldItalic.ttf')}[style]
    fill = (240, 220, 40, 255) if style == 'small' else (235, 235, 235, 255)
    font = ImageFont.truetype(FONTS + face, size)
    probe = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
    box = [round(v) for v in probe.multiline_textbbox((0, 0), text, font=font, stroke_width=stroke,
                                                      align='center', spacing=size // 4)]
    picture = Image.new('RGBA', (box[2] - box[0] + 8, box[3] - box[1] + 8), (0, 0, 0, 0))
    ImageDraw.Draw(picture).multiline_text((4 - box[0], 4 - box[1]), text, font=font, fill=fill, stroke_width=stroke,
                                           stroke_fill=(16, 16, 16, 255), align='center', spacing=size // 4)
    if style != 'pgs':  # four colours, transparent or opaque, like a VobSub
        pixels = picture.load()
        for y in range(picture.height):
            for x in range(picture.width):
                r, g, b, a = pixels[x, y]
                lum = (r + g + b) / 3
                pixels[x, y] = (0, 0, 0, 0) if a <= 100 else fill if lum > 150 else \
                    (128, 128, 128, 255) if lum > 80 else (16, 16, 16, 255)
    return picture


def ink(picture: Image.Image) -> Image.Image:
    """Over black, then inverted: the fill becomes dark ink, the outline and background light."""
    black = Image.new('RGBA', picture.size, (0, 0, 0, 255))
    gray = Image.alpha_composite(black, picture).convert('L')
    return gray.point(lambda v: 255 - v)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    expected = {}
    for number, (style, model, text) in enumerate(PICTURES, 1):
        name = f'{number:02d}-{style}-{model}.pgm'
        ink(render(text, style)).save(OUT / name)
        expected[name] = {'model': model, 'text': text}
    (OUT / 'expected.json').write_text(json.dumps(expected, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print(f'{len(expected)} pictures in {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
