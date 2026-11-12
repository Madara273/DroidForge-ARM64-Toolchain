# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
"""Shared filesystem, process and configuration primitives (stdlib only)."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import tomllib
import signal
import sys

TARGETS = ('aapt2', 'aidl', 'zipalign', 'split-select', 'adb', 'fastboot')


class ForgeError(RuntimeError):
    """An actionable user-facing failure."""


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=path.name + '.')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False, sort_keys=True)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run(command, *, cwd=None, capture=False, log=None, env=None):
    command = [str(item) for item in command]
    print('+ ' + shlex.join(command), flush=True)
    stream = None
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        stream = log.open('a')
        stream.write('\n+ ' + shlex.join(command) + '\n')
        stream.flush()
    process = None
    try:
        process = subprocess.Popen(command, cwd=cwd, text=True, env=env,
                                   stdout=subprocess.PIPE if capture or stream else None,
                                   stderr=subprocess.PIPE if capture else (subprocess.STDOUT if stream else None),
                                   start_new_session=True)
        if stream is not None and not capture:
            # Tee each Ninja/CMake line immediately; retain the complete build log.
            for line in process.stdout:
                stream.write(line)
                stream.flush()
                sys.stdout.write(line)
                sys.stdout.flush()
            process.stdout.close()
            process.wait()
            output, errors = '', ''
        else:
            output, errors = process.communicate()
        if process.returncode:
            raise ForgeError(f'{shlex.join(command)}\n{(errors or "").strip()}' if capture
                             else f'Команда завершилася з кодом {process.returncode}'
                             + (f'; журнал: {log}' if log else ''))
        return (output or '').strip()
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if process is not None and process.stdout is not None:
            process.stdout.close()
        if stream is not None:
            stream.close()


@contextmanager
def process_lock(root):
    state = Path(root) / '.forge'
    state.mkdir(exist_ok=True)
    with (state / 'process.lock').open('a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ForgeError('Інший процес forge вже працює в цьому проєкті.') from error
        yield


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def config(root):
    data = tomllib.loads((Path(root) / 'config/toolchain.toml').read_text())
    import re
    recipe = data['recipe']
    if not re.fullmatch(r'[0-9a-f]{40}', recipe['commit']):
        raise ForgeError('recipe.commit має бути повним Git SHA (40 hex).')
    if not recipe['url'].startswith('https://'):
        raise ForgeError('URL recipe має використовувати HTTPS.')
    if not recipe['aosp_ref'] or recipe['aosp_ref'].startswith('-'):
        raise ForgeError('Неприпустимий AOSP ref.')
    for key in ('jobs', 'fetch_jobs'):
        if key == 'jobs' and data['build'][key] == 'auto':
            continue
        if not isinstance(data['build'][key], int) or not 1 <= data['build'][key] <= 64:
            raise ForgeError(f'build.{key} має бути в межах 1..64.')
    limit = data['build']['max_source_gib']
    if not isinstance(limit, (int, float)) or not 0.25 <= limit <= 5:
        raise ForgeError('build.max_source_gib має бути в межах 0.25..5 GiB.')
    selected = data['build']['targets']
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(TARGETS):
        raise ForgeError('Неприпустимий перелік targets.')
    return data
