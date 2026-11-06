"""Manifest <-> code guards.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

The host reads `display_name`, `languages`, `config_schema`, `tasks`,
`executable`, `protocol` and `platforms` straight from manifest.json, never
from the running add-on. These tests keep the manifest and the code from
drifting apart.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import tesseract_addon  # noqa: E402
from tesseract_addon import __main__ as addon  # noqa: E402
from tesseract_addon.languages import LANGUAGES  # noqa: E402

MANIFEST = json.loads((REPO_ROOT / 'manifest.json').read_text(encoding='utf-8'))
PYPROJECT = (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
SPEC = (REPO_ROOT / 'pyinstaller.spec').read_text(encoding='utf-8')


def test_identity():
    assert MANIFEST['id'] == 'tesseract' == addon.ADDON_ID == tesseract_addon.ADDON_ID
    assert MANIFEST['tasks'] == ['ocr.recognize']
    assert MANIFEST['protocol'] == 1 == addon.PROTOCOL_VERSION
    assert MANIFEST['manifest_version'] == 1
    assert MANIFEST['executable'] == 'bin/tesseract-addon'
    assert "name='tesseract-addon'" in SPEC
    assert MANIFEST['platforms'] == ['linux', 'macos', 'windows']
    assert MANIFEST['compute'] == 'cpu'


def test_all_version_sites_agree():
    pyproject_version = re.search(r'^version\s*=\s*"([^"]+)"', PYPROJECT, re.M).group(1)
    versions = {MANIFEST['version'], pyproject_version, tesseract_addon.__version__, addon.ADDON_VERSION}
    assert len(versions) == 1, versions
    assert re.fullmatch(r'\d\d\.\d{1,2}\.\d+', MANIFEST['version'])


def test_languages_match_the_code():
    assert MANIFEST['languages'] == LANGUAGES


def test_config_schema_matches_the_options():
    fields = {f['key']: f for f in MANIFEST['config_schema']['fields']}
    assert set(fields) == {'straight_quotes'}
    assert fields['straight_quotes']['type'] == 'bool' and fields['straight_quotes']['default'] is True
