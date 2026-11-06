"""The add-on as Subtitld runs it: a process spoken to over stdio, reading pictures for real.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

By default the process is the source tree (`python -m tesseract_addon`) with the
assets from tools/stage_assets.py. With TESSERACT_TEST_BINARY set it is that
executable instead, which is how the release workflow runs these tests: on the
frozen binary unpacked from the release zip the way Subtitld installs it, with
nothing but the library and the English model inside its own bundle.

The Portuguese test downloads por.traineddata (2 MB) into a temporary model
folder, which proves the frozen bundle's HTTPS and the sha256 check.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from tesseract_addon import __main__ as addon  # noqa: E402

DATA = REPO_ROOT / 'tests' / 'data'
EXPECTED = json.loads((DATA / 'expected.json').read_text(encoding='utf-8'))
BINARY = os.environ.get('TESSERACT_TEST_BINARY')
ASSETS = Path(os.environ.get('TESSERACT_ADDON_ASSETS') or REPO_ROOT / 'build' / 'assets')
HAVE_ASSETS = bool(BINARY) or ((ASSETS / addon.library_name()).is_file()
                               and (ASSETS / 'tessdata' / 'eng.traineddata').is_file())

pytestmark = pytest.mark.skipif(not HAVE_ASSETS, reason='no library or model; run tools/stage_assets.py')


def _command(models: Path) -> tuple[list[str], dict]:
    env = dict(os.environ)
    env['SUBTITLD_MODELS_DIR'] = str(models)
    if BINARY:
        # The bundle's own assets and code, nothing from this checkout.
        env.pop('TESSERACT_ADDON_ASSETS', None)
        env.pop('PYTHONPATH', None)
        return [BINARY], env
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['TESSERACT_ADDON_ASSETS'] = str(ASSETS)
    return [sys.executable, '-m', 'tesseract_addon'], env


class Session:
    def __init__(self, cwd: Path):
        command, env = _command(cwd / 'models')
        self.proc = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.hello = self.recv()
        self.send({'type': 'ready', 'host': 'subtitld', 'host_version': 'test', 'protocol': 1})

    def send(self, frame: dict) -> None:
        self.proc.stdin.write((json.dumps(frame) + '\n').encode('utf-8'))
        self.proc.stdin.flush()

    def recv(self) -> dict:
        line = self.proc.stdout.readline()
        assert line, 'the add-on closed its output: ' + self.proc.stderr.read().decode('utf-8', 'replace')
        return json.loads(line)

    def request(self, req_id: str, params: dict, task: str = 'ocr.recognize') -> list[dict]:
        """Every frame for one request, up to and including its terminal frame."""
        self.send({'id': req_id, 'type': task, 'params': params})
        frames = []
        while True:
            frame = self.recv()
            assert frame.get('id') == req_id, frame
            frames.append(frame)
            if frame['type'] in ('result', 'error'):
                return frames

    def close(self) -> str:
        self.send({'type': 'shutdown'})
        _, err = self.proc.communicate(timeout=15)
        assert self.proc.returncode == 0, err.decode('utf-8', 'replace')
        return err.decode('utf-8', 'replace')


def _cer(reference: str, hypothesis: str) -> float:
    previous = list(range(len(hypothesis) + 1))
    for i, r in enumerate(reference, 1):
        current = [i]
        for j, h in enumerate(hypothesis, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (r != h)))
        previous = current
    return previous[-1] / max(1, len(reference))


@pytest.fixture()
def session(tmp_path):
    s = Session(tmp_path)
    yield s
    if s.proc.poll() is None:
        s.proc.kill()
        s.proc.wait()


def _pictures(model: str) -> list[str]:
    return [name for name, item in EXPECTED.items() if item['model'] == model]


def test_hello(session):
    assert session.hello['type'] == 'hello'
    assert session.hello['addon'] == 'tesseract'
    capability = session.hello['capabilities'][0]
    assert capability['task'] == 'ocr.recognize'
    assert 'en' in capability['languages'] and 'pt' in capability['languages']
    session.close()


def test_reads_english_with_the_bundled_model(session):
    names = _pictures('eng')
    frames = session.request('eng', {'images': [{'path': str(DATA / n)} for n in names], 'language': 'en'})
    result = frames[-1]
    assert result['type'] == 'result', result
    texts = result['data']['texts']
    assert result['data']['language'] == 'eng'
    assert result['data']['engine'].startswith('tesseract 5.5')
    partials = {f['data']['index']: f['data']['text'] for f in frames if f['type'] == 'partial'}
    assert partials == dict(enumerate(texts))
    for name, text in zip(names, texts):
        assert _cer(EXPECTED[name]['text'], text) <= 0.05, (name, text)
    assert all(0 < c <= 1 for c in result['data']['confidences'])
    stderr = session.close()
    assert 'Error in' not in stderr, stderr   # Leptonica is built quiet


def test_downloads_portuguese_once(session, tmp_path):
    names = _pictures('por')
    frames = session.request('por', {'images': [str(DATA / n) for n in names], 'language': 'pt-br'})
    result = frames[-1]
    assert result['type'] == 'result', result
    assert any('Downloading' in f.get('message', '') for f in frames if f['type'] == 'progress')
    for name, text in zip(names, result['data']['texts']):
        assert _cer(EXPECTED[name]['text'], text) <= 0.05, (name, text)
    downloaded = list((tmp_path / 'models' / 'tesseract').glob('*/por.traineddata'))
    assert len(downloaded) == 1
    again = session.request('por-again', {'images': [str(DATA / names[0])], 'language': 'por'})
    assert again[-1]['type'] == 'result'
    assert not any('Downloading' in f.get('message', '') for f in again if f['type'] == 'progress')
    session.close()


def test_unreadable_picture_is_an_empty_text(session, tmp_path):
    bad = tmp_path / 'bad.pgm'
    bad.write_bytes(b'not a picture')
    frames = session.request('bad', {'images': [str(bad), str(DATA / _pictures('eng')[0])], 'language': 'en'})
    assert frames[-1]['type'] == 'result'
    texts = frames[-1]['data']['texts']
    assert texts[0] == '' and texts[1]
    session.close()


def test_errors(session):
    assert session.request('lang', {'images': [], 'language': 'xx'})[-1]['code'] == 'unsupported_language'
    assert session.request('imgs', {'images': 'nope', 'language': 'en'})[-1]['code'] == 'bad_params'
    assert session.request('task', {}, task='asr.transcribe')[-1]['code'] == 'bad_params'
    empty = session.request('empty', {'images': [], 'language': 'en'})[-1]
    assert empty['type'] == 'result' and empty['data']['texts'] == []
    session.close()
