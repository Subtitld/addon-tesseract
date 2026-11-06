"""Pictures, clean-up and languages, without the library.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tesseract_addon import cleanup, images, tessdata  # noqa: E402
from tesseract_addon.languages import LANGUAGES, MODELS, model_for  # noqa: E402
from tesseract_addon.tessdata_pins import PINS  # noqa: E402

DATA = REPO_ROOT / 'tests' / 'data'


def _pgm(rows: list[str]) -> bytes:
    """A PGM from text art: '#' ink, '.' paper."""
    width = len(rows[0])
    pixels = bytes(0 if c == '#' else 255 for row in rows for c in row)
    return f'P5\n# test\n{width} {len(rows)}\n255\n'.encode() + pixels


def test_reads_pgm_and_rejects_others():
    picture = images.read_pgm(_pgm(['#.', '.#']))
    assert (picture.width, picture.height, picture.pixels) == (2, 2, b'\x00\xff\xff\x00')
    with pytest.raises(images.ImageError):
        images.read_pgm(b'P6\n1 1\n255\n\x00\x00\x00')
    with pytest.raises(images.ImageError):
        images.read_pgm(b'P5\n4 4\n255\n\x00')


def test_maxval_is_scaled():
    picture = images.read_pgm(b'P5 2 1 15 \x00\x0f')
    assert picture.pixels == b'\x00\xff'


def test_lines_split_at_blank_rows_and_keep_accents():
    rows = ['....................',
            '...#................',   # an accent, apart from its letter
            '....................',
            '..######..######....',
            '..######..######....',
            '..######..######....',
            '..######..######....',
            '....................',
            '....................',
            '..########..........',
            '..########..........',
            '..########..........',
            '..########..........',
            '....................']
    lines = images.lines(images.read_pgm(_pgm(rows)))
    assert len(lines) == 2
    first, second = lines
    # Six rows (accent, gap, four of letters) enlarged, plus the margin.
    factor = min(images.MAX_SCALE, round(images.LINE_HEIGHT / 6))
    assert first.height == 6 * factor + 2 * images.MARGIN
    assert first.width == 14 * factor + 2 * images.MARGIN
    assert second.width == 8 * min(images.MAX_SCALE, round(images.LINE_HEIGHT / 4)) + 2 * images.MARGIN


def test_light_text_on_dark_is_inverted():
    rows = ['######', '#....#', '######']
    picture = images.light_background(images.read_pgm(_pgm(rows)))
    assert picture.pixels[0] == 255 and picture.pixels[7] == 0


def test_real_pictures_split_into_their_lines():
    counts = {'01-dvd-eng.pgm': 2, '03-pgs-eng.pgm': 1, '06-pgs-por.pgm': 2}
    for name, count in counts.items():
        assert len(images.lines(images.read_pgm((DATA / name).read_bytes()))) == count, name


@pytest.mark.parametrize('raw, cleaned', [
    ('| told you', 'I told you'),
    ("- |'m fine", "- I'm fine"),
    ("|t's late", "It's late"),
    ('Well... | guess', 'Well... I guess'),
    ('Você já almoçou hoje?|', 'Você já almoçou hoje?'),
    ('“Hurry up,” she said.', '"Hurry up," she said.'),
    ('It’s  fine', "It's fine"),
    ('a|b', 'a|b'),
])
def test_cleanup(raw, cleaned):
    assert cleanup.clean_line(raw) == cleaned


def test_cleanup_keeps_curly_quotes_when_asked():
    assert cleanup.clean_line('“Hi”', straight_quotes=False) == '“Hi”'


def test_cleanup_cjk_punctuation():
    assert cleanup.clean_line('你去哪儿了?', cjk=True) == '你去哪儿了？'
    assert cleanup.clean_line('快点!', cjk=True) == '快点！'
    assert cleanup.clean(['', 'one', '  ', 'two']) == 'one\ntwo'


@pytest.mark.parametrize('tag, model', [
    ('en', 'eng'), ('pt-br', 'por'), ('PT_BR', 'por'), ('', 'eng'), ('und', 'eng'),
    ('zh-cn', 'chi_sim'), ('zh-tw', 'chi_tra'), ('zh-Hant', 'chi_tra'), ('chi', 'chi_sim'),
    ('sr-latn', 'srp_latn'), ('sr', 'srp'), ('fre', 'fra'), ('ger', 'deu'), ('fra', 'fra'),
    ('iw', 'heb'), ('nb', 'nor'), ('fil', 'fil'), ('xx', None), ('qqq', None),
])
def test_model_for(tag, model):
    assert model_for(tag) == model


def test_every_model_is_pinned():
    assert MODELS <= set(PINS)
    assert all(model_for(code) in PINS for code in LANGUAGES)
    assert tessdata.BUNDLED == ('eng',)
    assert all(len(digest) == 64 and size > 0 for size, digest in PINS.values())
