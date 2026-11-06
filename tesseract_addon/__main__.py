"""Tesseract OCR add-on for Subtitld — JSON-line protocol entry point.

Copyright 2026 Subtitld contributors
SPDX-License-Identifier: Apache-2.0

Licensed under the Apache License, Version 2.0 (the "License"); you may not
use this file except in compliance with the License. You may obtain a copy of
the License at http://www.apache.org/licenses/LICENSE-2.0. Unless required by
applicable law or agreed to in writing, software distributed under the License
is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.

What this add-on is
-------------------
Reads the text of picture subtitles (DVD VobSub, Blu-ray PGS) with Tesseract
5.5, so Subtitld can open them as text. Serves `ocr.recognize`. Subtitld
decodes the pictures and keeps their times and positions; the add-on only
reads one picture per subtitle. English is in the archive; another language
is downloaded once, the first time a subtitle in it is read (1-12 MB).

Wire format
-----------
One JSON object per line on stdin/stdout. Stderr is free-form logging, which
the host prefixes with `[addon:tesseract]` and surfaces.

`ocr.recognize` params:
    images    [{"path": "<file>.pgm"}, ...]  one per subtitle; 8-bit binary
              PGM, dark text on a light background (images.py)
    language  the subtitles' language: "pt-br", "fre", "zh-tw" ...
    options   {"straight_quotes": bool}
Frames: `progress`; one `partial` per picture as it is read,
{"index", "text", "confidence"} (pictures finish out of order); then
`result` {"texts": [...], "confidences": [...], "language": <model>,
"engine": "tesseract <version>"}. A picture that cannot be read gives ''.

Options
-------
`straight_quotes`, or $TESSERACT_STRAIGHT_QUOTES (default on): “curly” quotes
as "straight" ones, as most subtitle files write them.
`threads`, or $TESSERACT_THREADS (default 0 = up to 4): pictures read at once.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import queue
import sys
import threading
import time
import traceback
from pathlib import Path

from tesseract_addon import ADDON_ID, PROTOCOL_VERSION, __version__
from tesseract_addon import cleanup, images, tessdata
from tesseract_addon.engine import PSM_SINGLE_LINE, Engine, EngineError
from tesseract_addon.languages import LANGUAGES, model_for, uses_cjk_punctuation

ADDON_VERSION = __version__
MAX_THREADS = 4

ERR_MODEL_MISSING = 'model_missing'
ERR_UNSUPPORTED_LANGUAGE = 'unsupported_language'
ERR_BAD_PARAMS = 'bad_params'
ERR_INTERNAL = 'internal'
ERR_CANCELLED = 'cancelled'
ERR_OOM = 'oom'
ERR_NETWORK = 'network'

log = logging.getLogger('tesseract_addon')


# ---------------------------------------------------------------------------
# stdio ownership + protocol frames
# ---------------------------------------------------------------------------
_PROTO_IN = None   # binary reader for host frames
_PROTO_OUT = None  # binary writer for our frames
_proto_lock = threading.Lock()


def _claim_protocol_stdio() -> None:
    """Give the protocol exclusive use of the real stdin/stdout.

    fd-level, not just `sys.stdout = sys.stderr`: Tesseract is native code and
    may write to fd 1 directly. Falls back to the Python-level swap if the fd
    dance fails.
    """
    global _PROTO_IN, _PROTO_OUT
    try:
        sys.stdout.flush()
        in_fd = os.dup(0)
        out_fd = os.dup(1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        try:
            os.dup2(devnull, 0)
        finally:
            os.close(devnull)
        os.dup2(2, 1)
        _PROTO_IN = os.fdopen(in_fd, 'rb')
        _PROTO_OUT = os.fdopen(out_fd, 'wb')
    except (OSError, ValueError, AttributeError):
        _PROTO_IN = sys.stdin.buffer
        _PROTO_OUT = sys.stdout.buffer
    sys.stdin = open(os.devnull, encoding='utf-8')  # stray input() -> EOFError
    sys.stdout = sys.stderr                        # stray print() -> the log


def _emit(frame: dict) -> None:
    data = (json.dumps(frame, ensure_ascii=False, separators=(',', ':')) + '\n').encode('utf-8')
    out = _PROTO_OUT if _PROTO_OUT is not None else sys.stdout.buffer
    with _proto_lock:
        try:
            out.write(data)
            out.flush()
        except (BrokenPipeError, OSError, ValueError):
            pass  # host went away; the frame loop exits on the next empty read


def _emit_progress(req_id: str, value: float, message: str = '') -> None:
    _emit({'id': req_id, 'type': 'progress',
           'value': max(0.0, min(1.0, float(value))), 'message': message})


def _emit_partial(req_id: str, data: dict) -> None:
    _emit({'id': req_id, 'type': 'partial', 'data': data})


def _emit_result(req_id: str, data: dict) -> None:
    _emit({'id': req_id, 'type': 'result', 'data': data})


def _emit_error(req_id: str, code: str, message: str, retryable: bool = False) -> None:
    _emit({'id': req_id, 'type': 'error', 'code': code,
           'message': message, 'retryable': retryable})


class _AddonError(Exception):
    """An expected failure that already knows its protocol code."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class _Cancelled(Exception):
    """The host cancelled this request."""


# ---------------------------------------------------------------------------
# Options and environment
# ---------------------------------------------------------------------------
def _env(*names: str) -> str:
    """First non-empty value among `names`, stripped. '' when none are set."""
    for name in names:
        value = (os.environ.get(name) or '').strip()
        if value:
            return value
    return ''


def _flag(options: dict, key: str, env: str, default: bool) -> bool:
    value = options.get(key)
    if value is None or value == '':
        value = _env(env)
    if value is None or value == '':
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ('0', 'false', 'no', 'off')
    return bool(value)


def _threads(options: dict, count: int) -> int:
    raw = options.get('threads')
    if raw in (None, ''):
        raw = _env('TESSERACT_THREADS') or 0
    try:
        wanted = int(raw)
    except (TypeError, ValueError):
        wanted = 0
    if wanted <= 0:
        wanted = min(MAX_THREADS, os.cpu_count() or 1)
    return max(1, min(wanted, count))


# ---------------------------------------------------------------------------
# Bundled assets
# ---------------------------------------------------------------------------
def _asset_dir() -> Path:
    """Where the library and the bundled model are. Source runs use `build/assets/`."""
    override = _env('TESSERACT_ADDON_ASSETS')
    if override:
        return Path(os.path.expanduser(override))
    bundled = getattr(sys, '_MEIPASS', None)  # PyInstaller onedir: the `_internal` folder
    if bundled:
        return Path(bundled) / 'assets'
    return Path(__file__).resolve().parent.parent / 'build' / 'assets'


def library_name() -> str:
    if sys.platform == 'darwin':
        return 'libtesseract.dylib'
    if sys.platform == 'win32':
        return 'libtesseract.dll'
    return 'libtesseract.so'


# ---------------------------------------------------------------------------
# The engine: loaded on first use, shared by every request
# ---------------------------------------------------------------------------
_engine: Engine | None = None
_engine_lock = threading.Lock()


def _load_engine() -> Engine:
    global _engine
    with _engine_lock:
        if _engine is None:
            library = _asset_dir() / library_name()
            if not library.is_file():
                raise _AddonError(ERR_MODEL_MISSING,
                                  f'{library.name} is missing from the add-on ({library}). Reinstall the add-on.')
            try:
                _engine = Engine(str(library))
            except EngineError as exc:
                raise _AddonError(ERR_INTERNAL, f'Tesseract failed to load: {exc}') from exc
            log.info('tesseract %s loaded from %s', _engine.version, library)
        return _engine


def _model_data(req_id: str, model: str, cancel: threading.Event) -> bytes:
    """The model's bytes: bundled, already downloaded, or downloaded now (0.00 -> 0.10)."""
    bundled = _asset_dir() / 'tessdata'
    path = tessdata.local_path(model, bundled)
    if path is None:
        size = tessdata.size_of(model)
        message = f'Downloading the {model} language model ({size / 2**20:.1f} MB)...'
        log.info('downloading %s.traineddata, %d bytes', model, size)
        _emit_progress(req_id, 0.0, message)
        try:
            path = tessdata.download(
                model, progress=lambda done, total: _emit_progress(req_id, 0.1 * done / max(1, total), message),
                cancelled=cancel.is_set)
        except tessdata.TessdataError as exc:
            if cancel.is_set():
                raise _Cancelled() from exc
            raise _AddonError(ERR_NETWORK, f'{exc}. Check the internet connection and try again.',
                              retryable=True) from exc
    return path.read_bytes()


# ---------------------------------------------------------------------------
# The request
# ---------------------------------------------------------------------------
class _Request:
    def __init__(self, req_id: str):
        self.req_id = req_id
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


_active: dict[str, _Request] = {}
_active_lock = threading.Lock()


def _image_paths(params: dict) -> list[str]:
    raw = params.get('images')
    if not isinstance(raw, list):
        raise _AddonError(ERR_BAD_PARAMS, 'images must be a list')
    paths = []
    for item in raw:
        path = item.get('path') if isinstance(item, dict) else item
        if not isinstance(path, str) or not path:
            raise _AddonError(ERR_BAD_PARAMS, 'every image needs a path')
        paths.append(path)
    return paths


def read_picture(reader, path: str, cjk: bool, straight_quotes: bool) -> tuple[str, float]:
    """One subtitle picture's text and Tesseract's confidence in it, 0-1."""
    with open(path, 'rb') as handle:
        picture = images.read_pgm(handle.read())
    texts, confidences = [], []
    for line in images.lines(picture):
        text, confidence = reader.read(line.pixels, line.width, line.height, PSM_SINGLE_LINE)
        if text.strip():
            texts.append(text)
            confidences.append(max(0, confidence))
    text = cleanup.clean(texts, cjk=cjk, straight_quotes=straight_quotes)
    return text, (sum(confidences) / len(confidences) / 100.0 if confidences else 0.0)


def _handle_recognize(state: _Request, params: dict) -> None:
    req_id = state.req_id
    options = params.get('options')
    if not isinstance(options, dict):
        options = {}
    outcome: tuple = ('error', ERR_INTERNAL, 'no outcome', False)
    readers = []
    try:
        paths = _image_paths(params)
        model = model_for(params.get('language'))
        if model is None:
            raise _AddonError(ERR_UNSUPPORTED_LANGUAGE,
                              f'Tesseract has no model for {params.get("language")!r}.')
        straight_quotes = _flag(options, 'straight_quotes', 'TESSERACT_STRAIGHT_QUOTES', True)
        cjk = uses_cjk_punctuation(model)
        log.info('recognize %s: %d picture(s), model %s', req_id, len(paths), model)

        engine = _load_engine()
        data = _model_data(req_id, model, state.cancel)
        if state.cancel.is_set():
            raise _Cancelled()
        _emit_progress(req_id, 0.1, 'Reading the subtitles...')
        try:
            readers = [engine.reader(model, data) for _ in range(_threads(options, len(paths)))]
        except EngineError as exc:
            raise _AddonError(ERR_MODEL_MISSING, str(exc)) from exc

        texts = [''] * len(paths)
        confidences = [0.0] * len(paths)
        todo: queue.Queue = queue.Queue()
        for index in range(len(paths)):
            todo.put(index)
        done = [0]
        done_lock = threading.Lock()
        failures: list[str] = []
        started = time.monotonic()

        def work(reader) -> None:
            while not state.cancel.is_set():
                try:
                    index = todo.get_nowait()
                except queue.Empty:
                    return
                try:
                    text, confidence = read_picture(reader, paths[index], cjk, straight_quotes)
                except (OSError, images.ImageError) as exc:
                    text, confidence = '', 0.0
                    failures.append(f'{paths[index]}: {exc}')
                texts[index], confidences[index] = text, round(confidence, 3)
                _emit_partial(req_id, {'index': index, 'text': text, 'confidence': round(confidence, 3)})
                with done_lock:
                    done[0] += 1
                    count = done[0]
                if count == len(paths) or count % 5 == 0:
                    _emit_progress(req_id, 0.1 + 0.9 * count / len(paths), 'Reading the subtitles...')

        workers = [threading.Thread(target=work, args=(reader,), daemon=True) for reader in readers]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        if state.cancel.is_set():
            raise _Cancelled()
        for failure in failures[:10]:
            log.warning('recognize %s: unreadable picture %s', req_id, failure)
        log.info('recognize %s: %d picture(s) in %.2fs with %d thread(s), %d unreadable', req_id, len(paths),
                 time.monotonic() - started, len(readers), len(failures))
        outcome = ('result', {'texts': texts, 'confidences': confidences, 'language': model,
                              'engine': f'tesseract {engine.version}'})
    except _Cancelled:
        log.info('recognize %s: cancelled', req_id)
        outcome = ('error', ERR_CANCELLED, 'cancelled', False)
    except _AddonError as exc:
        log.warning('recognize %s failed: [%s] %s', req_id, exc.code, exc.message)
        outcome = ('error', exc.code, exc.message, exc.retryable)
    except MemoryError:
        outcome = ('error', ERR_OOM, 'Out of memory while reading the subtitles.', False)
    except Exception as exc:  # noqa: BLE001 — every path must end in one terminal frame
        log.exception('recognize %s failed', req_id)
        outcome = ('error', ERR_INTERNAL, f'{type(exc).__name__}: {exc}', False)
    finally:
        for reader in readers:
            reader.close()

    if outcome[0] == 'result':
        _emit_progress(req_id, 1.0, '')
        _emit_result(req_id, outcome[1])
    else:
        _emit_error(req_id, *outcome[1:])


def _spawn(req_id: str, target, *args) -> None:
    state = _Request(req_id)
    with _active_lock:
        _active[req_id] = state

    def _run():
        try:
            target(state, *args)
        except BaseException:  # noqa: BLE001
            log.exception('worker crashed')
            tail = traceback.format_exc().strip().splitlines()[-1]
            _emit_error(req_id, ERR_INTERNAL, f'worker crashed: {tail}')
        finally:
            with _active_lock:
                _active.pop(req_id, None)

    thread = threading.Thread(target=_run, name=f'tesseract-{req_id}', daemon=True)
    state.thread = thread
    thread.start()


# ---------------------------------------------------------------------------
# Handshake + frame loop
# ---------------------------------------------------------------------------
def _send_hello() -> None:
    _emit({
        'type': 'hello',
        'protocol': PROTOCOL_VERSION,
        'addon': ADDON_ID,
        'version': ADDON_VERSION,
        'capabilities': [{
            'task': 'ocr.recognize',
            'languages': list(LANGUAGES),
            'image_formats': ['pgm'],
        }],
    })


def _read_frame() -> dict | None:
    source = _PROTO_IN if _PROTO_IN is not None else sys.stdin.buffer
    line = source.readline()
    if not line:
        return None  # stdin closed: the host went away
    text = line.decode('utf-8', errors='replace').strip().lstrip('﻿')
    if not text:
        return {}
    try:
        frame = json.loads(text)
    except ValueError as exc:
        log.warning('malformed frame %r: %s', line[:200], exc)
        return {}
    return frame if isinstance(frame, dict) else {}


def _main_loop() -> int:
    while True:
        frame = _read_frame()
        if frame is None:
            log.info('stdin closed; exiting')
            return 0
        if not frame:
            continue
        ftype = frame.get('type')

        if ftype == 'shutdown':
            log.info('shutdown requested')
            return 0

        if ftype == 'ready':
            log.info('handshake complete; host=%s host_version=%s',
                     frame.get('host'), frame.get('host_version'))
            continue

        if ftype == 'cancel':
            target = frame.get('target')
            with _active_lock:
                state = _active.get(target) if isinstance(target, str) else None
            if state is not None:
                log.info('cancel %s', target)
                state.cancel.set()
            continue

        if ftype == 'ocr.recognize':
            req_id = frame.get('id')
            params = frame.get('params')
            if not isinstance(req_id, str) or not req_id:
                log.warning('ocr.recognize without id')
                continue
            if params is None:
                params = {}
            if not isinstance(params, dict):
                _emit_error(req_id, ERR_BAD_PARAMS, 'params must be an object')
                continue
            _spawn(req_id, _handle_recognize, params)
            continue

        # Unknown task: still answer, so the host's future resolves instead of
        # hanging until its timeout.
        req_id = frame.get('id')
        if isinstance(req_id, str) and req_id:
            _emit_error(req_id, ERR_BAD_PARAMS, f'unsupported frame type {ftype!r}')


def _stop_workers(budget_sec: float = 3.5) -> bool:
    """Cancel and join every worker. True when none is left running.

    The host allows 5 s between `shutdown` and SIGTERM, so stay under it.
    """
    with _active_lock:
        states = list(_active.values())
    for state in states:
        state.cancel.set()
    deadline = time.monotonic() + budget_sec
    for state in states:
        if state.thread is not None:
            state.thread.join(timeout=max(0.0, deadline - time.monotonic()))
    return not any(s.thread is not None and s.thread.is_alive() for s in states)


def main() -> int:
    _claim_protocol_stdio()
    logging.basicConfig(
        level=(_env('TESSERACT_ADDON_LOG_LEVEL') or 'INFO').upper(),
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        stream=sys.stderr,
    )
    log.info('tesseract-addon %s on %s %s, python %s',
             ADDON_VERSION, platform.system(), platform.machine(), platform.python_version())
    _send_hello()
    rc = 1
    try:
        rc = _main_loop()
    except KeyboardInterrupt:
        rc = 130
    except Exception:  # noqa: BLE001
        log.exception('main loop crashed')
        rc = 1
    finally:
        stopped = _stop_workers()
    if not stopped:
        # A worker is still inside Tesseract. Normal teardown would free state
        # under it, so leave without teardown.
        log.warning('a recognition is still running; exiting without cleanup')
        try:
            sys.stderr.flush()
        finally:
            os._exit(rc)
    return rc


if __name__ == '__main__':
    sys.exit(main())
