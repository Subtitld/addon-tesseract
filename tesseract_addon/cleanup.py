"""The usual Tesseract slips on subtitles, put right.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Subtitle fonts are sans-serif, where a capital I is a bare stroke, and
Tesseract often reads it as `|`. A subtitle never holds a `|`, so one that
stands as a word, starts a word or ends a line is an I, or noise at the edge
of an italic line. Chinese and Japanese subtitles use full-width punctuation,
which Tesseract returns as ASCII. Curly quotes are made straight when the
option asks for it, as most subtitle files write them.
"""

from __future__ import annotations

import re

_PIPE_WORD = re.compile(r'(?<![^\s"\'(\[¿¡«—–-])\|(?=[\s\'’,.!?…:;)]|$)')   # "| told", "- |'m"
_PIPE_START = re.compile(r'(?<![^\s"\'(\[¿¡«—–-])\|(?=[a-zà-ÿ])')             # "|t's", "|n"
_PIPE_EDGE = re.compile(r'\s*\|+\s*$|^\s*\|+\s+')                               # stray stroke at an edge
_SPACES = re.compile(r'[ \t]{2,}')
_CJK = r'[　-〿぀-ヿ㐀-鿿가-힯＀-￯]'
_CJK_PUNCT = {'?': '？', '!': '！', ',': '，', ':': '：', ';': '；', '(': '（', ')': '）'}
_CJK_ASCII = re.compile(rf'(?<={_CJK})[?!,:;()]|[?!,:;()](?={_CJK})')
_QUOTES = str.maketrans({'“': '"', '”': '"', '„': '"', '‘': "'", '’': "'", '‚': "'"})


def clean_line(text: str, cjk: bool = False, straight_quotes: bool = True) -> str:
    line = text.replace('\x0c', '').strip()
    if not cjk:
        line = _PIPE_WORD.sub('I', line)
        line = _PIPE_START.sub('I', line)
    line = _PIPE_EDGE.sub('', line)
    if cjk:
        line = _CJK_ASCII.sub(lambda m: _CJK_PUNCT[m.group(0)], line)
    elif straight_quotes:
        line = line.translate(_QUOTES)
    return _SPACES.sub(' ', line).strip()


def clean(lines: list[str], cjk: bool = False, straight_quotes: bool = True) -> str:
    """A subtitle's lines, cleaned and joined; empty lines dropped."""
    out = (clean_line(line, cjk, straight_quotes) for line in lines)
    return '\n'.join(line for line in out if line)
