"""Which tessdata_fast model reads the language a host asks for.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The host sends BCP-47-ish tags (`pt-br`, `zh-tw`, `sr-latn`), and a disc's
own language codes arrive as they are written there: two letters in a VobSub
index (`id: en`), three in a Matroska track, sometimes the bibliographic
ISO 639-2 form (`fre`, `ger`, `chi`). `model_for` turns any of them into a
tessdata name (`por`, `chi_tra`, `srp_latn`); `LANGUAGES` lists what the
manifest advertises.
"""

from __future__ import annotations

# ISO 639-1 -> tessdata_fast. Historic models (enm, frm, grc, ita_old ...),
# Fraktur, vertical CJK and the orientation model are left out: subtitles are
# none of those.
_BY_639_1 = {
    'af': 'afr', 'am': 'amh', 'ar': 'ara', 'as': 'asm', 'az': 'aze', 'be': 'bel', 'bg': 'bul',
    'bn': 'ben', 'bo': 'bod', 'br': 'bre', 'bs': 'bos', 'ca': 'cat', 'co': 'cos', 'cs': 'ces',
    'cy': 'cym', 'da': 'dan', 'de': 'deu', 'dv': 'div', 'dz': 'dzo', 'el': 'ell', 'en': 'eng',
    'eo': 'epo', 'es': 'spa', 'et': 'est', 'eu': 'eus', 'fa': 'fas', 'fi': 'fin', 'fo': 'fao',
    'fr': 'fra', 'fy': 'fry', 'ga': 'gle', 'gd': 'gla', 'gl': 'glg', 'gu': 'guj', 'he': 'heb',
    'hi': 'hin', 'hr': 'hrv', 'ht': 'hat', 'hu': 'hun', 'hy': 'hye', 'id': 'ind', 'is': 'isl',
    'it': 'ita', 'iu': 'iku', 'ja': 'jpn', 'jv': 'jav', 'ka': 'kat', 'kk': 'kaz', 'km': 'khm',
    'kn': 'kan', 'ko': 'kor', 'ku': 'kmr', 'ky': 'kir', 'la': 'lat', 'lb': 'ltz', 'lo': 'lao',
    'lt': 'lit', 'lv': 'lav', 'mi': 'mri', 'mk': 'mkd', 'ml': 'mal', 'mn': 'mon', 'mr': 'mar',
    'ms': 'msa', 'mt': 'mlt', 'my': 'mya', 'ne': 'nep', 'nl': 'nld', 'no': 'nor', 'oc': 'oci',
    'or': 'ori', 'pa': 'pan', 'pl': 'pol', 'ps': 'pus', 'pt': 'por', 'qu': 'que', 'ro': 'ron',
    'ru': 'rus', 'sa': 'san', 'sd': 'snd', 'si': 'sin', 'sk': 'slk', 'sl': 'slv', 'sq': 'sqi',
    'sr': 'srp', 'su': 'sun', 'sv': 'swe', 'sw': 'swa', 'ta': 'tam', 'te': 'tel', 'tg': 'tgk',
    'th': 'tha', 'ti': 'tir', 'tl': 'fil', 'to': 'ton', 'tr': 'tur', 'tt': 'tat', 'ug': 'uig',
    'uk': 'ukr', 'ur': 'urd', 'uz': 'uzb', 'vi': 'vie', 'yi': 'yid', 'yo': 'yor', 'zh': 'chi_sim',
    # Old or alternative codes still found in files.
    'iw': 'heb', 'in': 'ind', 'ji': 'yid', 'nb': 'nor', 'nn': 'nor', 'mo': 'ron',
}

# Three-letter codes that are not tessdata names: ISO 639-2/B, and a few T
# codes tessdata spells differently.
_BY_639_2 = {
    'alb': 'sqi', 'arm': 'hye', 'baq': 'eus', 'bur': 'mya', 'chi': 'chi_sim', 'cze': 'ces',
    'dut': 'nld', 'fre': 'fra', 'geo': 'kat', 'ger': 'deu', 'gre': 'ell', 'ice': 'isl',
    'mac': 'mkd', 'mao': 'mri', 'may': 'msa', 'per': 'fas', 'rum': 'ron', 'slo': 'slk',
    'tib': 'bod', 'wel': 'cym', 'zho': 'chi_sim', 'nob': 'nor', 'nno': 'nor', 'tgl': 'fil',
    'kur': 'kmr', 'fil': 'fil', 'ceb': 'ceb', 'chr': 'chr', 'syr': 'syr', 'uzb': 'uzb',
}

# Every model `model_for` can return.
MODELS = frozenset(_BY_639_1.values()) | frozenset(_BY_639_2.values()) | {
    'chi_tra', 'srp_latn', 'aze_cyrl', 'uzb_cyrl'}

# What the manifest advertises: the host's two-letter tags, plus the three
# languages that have none.
LANGUAGES = sorted({code for code in _BY_639_1 if code not in ('iw', 'in', 'ji', 'nb', 'nn', 'mo', 'tl')}
                   | {'fil', 'ceb', 'chr', 'syr'})

_TRADITIONAL = {'tw', 'hk', 'mo', 'hant'}


def model_for(tag) -> str | None:
    """The tessdata model for a language tag, or None when Tesseract has none.

    '' and 'und' read as English, the language most subtitles without a
    declared one are in.
    """
    raw = str(tag or '').strip().lower().replace('_', '-')
    if not raw or raw in ('und', 'mul', 'zxx', 'auto'):
        return 'eng'
    parts = [p for p in raw.split('-') if p]
    primary, rest = parts[0], set(parts[1:])
    if primary in ('zh', 'chi', 'zho') and rest & _TRADITIONAL:
        return 'chi_tra'
    if primary in ('sr', 'srp') and 'latn' in rest:
        return 'srp_latn'
    if primary in ('az', 'aze') and 'cyrl' in rest:
        return 'aze_cyrl'
    if primary in ('uz', 'uzb') and 'cyrl' in rest:
        return 'uzb_cyrl'
    if len(primary) == 2:
        return _BY_639_1.get(primary)
    if primary in _BY_639_2:
        return _BY_639_2[primary]
    return primary if primary in MODELS else None


def uses_cjk_punctuation(model: str) -> bool:
    """Whether the language writes full-width punctuation (？！，)."""
    return model in ('chi_sim', 'chi_tra', 'jpn')
