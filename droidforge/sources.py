# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
"""Pinned Git sources. Never treat a half-cloned directory as a cache hit."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import os
import signal
import subprocess
import hashlib
import threading

from .core import ForgeError, atomic_json, config, digest, run


EXCLUDED_SOURCES = {'src/art', 'src/dalvik', 'src/development', 'src/f2fs-tools', 'src/sqlite'}


def tracked_digest(path, *, nested_sources=False):
    path = Path(path)
    result = subprocess.run(['git', 'diff', '--binary', 'HEAD'], cwd=path,
                            capture_output=True, check=True)
    fingerprint = hashlib.sha256(result.stdout)
    extras = set()
    for options in (['--others', '--exclude-standard'], ['--others', '--ignored', '--exclude-standard']):
        output = subprocess.check_output(['git', 'ls-files', '-z', *options], cwd=path)
        extras.update(os.fsdecode(name) for name in output.split(b'\0') if name)
    for name in sorted(extras):
        if nested_sources and name.startswith('src/'):
            continue
        candidate = path / name
        # CMake regenerates these exact protobuf outputs before compilation.
        generated_proto = ((path.name == 'base' and name.startswith('tools/aapt2/'))
                           or (path.name == 'adb' and name.startswith(('proto/', 'fastdeploy/proto/'))))
        if (generated_proto and name.endswith(('.pb.cc', '.pb.h'))
                and candidate.with_name(candidate.name.rsplit('.pb.', 1)[0] + '.proto').is_file()):
            continue
        fingerprint.update(name.encode() + b'\0')
        if candidate.is_symlink():
            fingerprint.update(os.readlink(candidate).encode())
        elif candidate.is_file():
            fingerprint.update(digest(candidate).encode())
        else:
            fingerprint.update(b'unexpected-directory')
    return fingerprint.hexdigest()


def verify_tracked(path, expected, *, nested_sources=False):
    if tracked_digest(path, nested_sources=nested_sources) != expected:
        raise ForgeError(f'Змінені підготовлені джерела: {path}. Відновіть їх перед збіркою.')


def verify_preparation(root):
    root = Path(root)
    preparation = root / '.forge/preparation.json'
    backend = root / '.forge/recipe'
    if not preparation.exists():
        return
    data = json.loads(preparation.read_text())
    for name, expected in data.get('diffs', {}).items():
        path = backend / name
        if not path.resolve().is_relative_to(backend.resolve()):
            raise ForgeError('Неприпустимий шлях preparation.')
        verify_tracked(path, expected, nested_sources=name == '.')
    for name, expected in data['files'].items():
        path = backend / name
        if (not path.resolve().is_relative_to(backend.resolve())
                or not path.is_file() or digest(path) != expected):
            raise ForgeError(f'Змінений файл підготовки: {path}')


def selected_manifest(entries):
    sparse = {
        'src/base': ['libs/androidfw', 'tools/aapt', 'tools/aapt2', 'tools/split-select',
                     'cmds/idmap2/libidmap2_policies/include'],
        'src/build': ['tools/zipalign'],
        'src/soong': ['cc/libbuildversion'],
    }
    return [{**entry, 'sparse': sparse.get(entry['path'], [])}
            for entry in entries if entry['path'] not in EXCLUDED_SOURCES]


def cache_size(root):
    total = 0
    for parent, _, files in os.walk(root, followlinks=False):
        try:
            directory_stat = Path(parent).lstat()
            total += max(directory_stat.st_size, directory_stat.st_blocks * 512)
        except FileNotFoundError:
            continue
        for name in files:
            try:
                metadata = (Path(parent) / name).lstat()
                total += max(metadata.st_size, metadata.st_blocks * 512)
            except FileNotFoundError:
                pass
    return total


def guarded_run(command, *, cwd, budget, cancel=None):
    if cancel is not None and cancel.is_set():
        raise ForgeError('Завантаження скасовано.')
    if budget is None:
        return run(command, cwd=cwd)
    root, limit = budget
    if cache_size(root) >= limit:
        raise ForgeError('Досягнуто ліміт кешу джерел. Завантаження зупинено.')
    print('+ ' + ' '.join(map(str, command)), flush=True)
    process = subprocess.Popen(list(map(str, command)), cwd=cwd, start_new_session=True)
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise ForgeError('Завантаження скасовано.')
            try:
                code = process.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                code = None
            if cache_size(root) >= limit:
                raise ForgeError('Досягнуто ліміт кешу джерел. Завантаження зупинено.')
            if code is not None:
                if code:
                    raise ForgeError(f'Git завершився з кодом {code}.')
                return
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()


def validate_entry(entry):
    path = PurePosixPath(entry['path'])
    if (len(path.parts) != 2 or path.parts[0] != 'src'
            or path.parts[1] in ('.', '..') or str(path) != entry['path']):
        raise ForgeError(f'Неприпустимий шлях джерела: {entry["path"]}')
    if not entry['url'].startswith('https://'):
        raise ForgeError(f'Джерело має використовувати HTTPS: {entry["url"]}')


def checkout(url, commit, destination, *, sparse=(), budget=None, prepared=False, nested_sources=False, cancel=None):
    destination = Path(destination)
    if cancel is not None and cancel.is_set():
        raise ForgeError('Завантаження скасовано.')
    if budget is not None and cache_size(budget[0]) >= budget[1]:
        raise ForgeError('Досягнуто ліміт кешу джерел.')
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ForgeError('Очікується повний SHA коміту.')
    if destination.is_symlink():
        raise ForgeError(f'Символічне посилання замість checkout: {destination}')
    if destination.exists():
        if not (destination / '.git').is_dir():
            raise ForgeError(f'Незавершений або сторонній каталог: {destination}; перемістіть його вручну.')
        actual = run(['git', 'rev-parse', 'HEAD'], cwd=destination, capture=True)
        remote = run(['git', 'remote', 'get-url', 'origin'], cwd=destination, capture=True)
        if actual != commit or remote != url:
            raise ForgeError(f'Checkout не відповідає lock: {destination}')
        if not prepared:
            verify_tracked(destination, hashlib.sha256(b'').hexdigest(), nested_sources=nested_sources)
        if sparse:
            enabled = subprocess.run(['git', 'config', '--bool', 'core.sparseCheckout'],
                                     cwd=destination, capture_output=True, text=True)
            current = (run(['git', 'sparse-checkout', 'list'], cwd=destination, capture=True).splitlines()
                       if enabled.stdout.strip() == 'true' else [])
            if sorted(current) != sorted(sparse):
                guarded_run(['git', 'sparse-checkout', 'set', '--cone', *sparse], cwd=destination, budget=budget, cancel=cancel)
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=destination.name + '.partial-', dir=destination.parent))
    try:
        run(['git', 'init', '-q', temporary])
        run(['git', 'remote', 'add', 'origin', url], cwd=temporary)
        if sparse:
            run(['git', 'sparse-checkout', 'set', '--cone', *sparse], cwd=temporary)
        guarded_run(['git', '-c', 'http.lowSpeedLimit=1024', '-c', 'http.lowSpeedTime=120',
                     'fetch', '--depth=1', '--filter=blob:none', 'origin', commit],
                    cwd=temporary, budget=budget, cancel=cancel)
        guarded_run(['git', '-c', 'advice.detachedHead=false', 'checkout', '-q', 'FETCH_HEAD'],
                    cwd=temporary, budget=budget, cancel=cancel)
        if run(['git', 'rev-parse', 'HEAD'], cwd=temporary, capture=True) != commit:
            raise ForgeError(f'Отримано неправильний commit: {url}')
        temporary.rename(destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def resolve(url, ref):
    lines = run(['git', 'ls-remote', '--exit-code', url,
                 f'refs/tags/{ref}', f'refs/tags/{ref}^{{}}', f'refs/heads/{ref}'], capture=True).splitlines()
    refs = {line.split()[1]: line.split()[0] for line in lines}
    commit = refs.get(f'refs/tags/{ref}^{{}}') or refs.get(f'refs/tags/{ref}') or refs.get(f'refs/heads/{ref}')
    if not commit:
        raise ForgeError(f'Не знайдено ref {ref} у {url}')
    return commit


def fetch(root):
    root = Path(root)
    verify_preparation(root)
    settings = config(root)
    recipe = settings['recipe']
    backend = root / '.forge/recipe'
    budget = (backend, int(settings['build']['max_source_gib'] * 1024**3) - 128 * 1024**2)
    prepared = (root / '.forge/preparation.json').exists()
    checkout(recipe['url'], recipe['commit'], backend, budget=budget,
             prepared=prepared, nested_sources=True)
    manifest = selected_manifest(json.loads((backend / 'repository.json').read_text()))
    for entry in manifest:
        validate_entry(entry)
    paths = [entry['path'] for entry in manifest]
    if len(set(paths)) != len(paths):
        raise ForgeError('Дубльований шлях у маніфесті джерел.')
    lock_path = root / 'sources.lock.json'
    identity = {**recipe, 'manifest_sha256': digest(backend / 'repository.json')}
    if lock_path.exists():
        lock = json.loads(lock_path.read_text())
        if lock.get('recipe') != identity or lock.get('schema') != 1:
            raise ForgeError('Конфігурація не відповідає sources.lock.json. Для нової версії використайте окремий checkout.')
        expected = {entry['path']: entry for entry in manifest}
        actual = {entry['path']: entry for entry in lock['sources']}
        if len(actual) != len(lock['sources']) or set(actual) != set(expected):
            raise ForgeError('Перелік джерел lock не відповідає recipe.')
        for path, entry in actual.items():
            validate_entry(entry)
            if (entry['url'] != expected[path]['url']
                    or entry.get('sparse') != expected[path]['sparse']
                    or entry['ref'] != expected[path].get('branch', recipe['aosp_ref'])):
                raise ForgeError(f'Lock не відповідає маніфесту: {path}')
    else:
        def pin(entry):
            ref = entry.get('branch', recipe['aosp_ref'])
            return {**entry, 'ref': ref, 'commit': resolve(entry['url'], ref)}
        with ThreadPoolExecutor(max_workers=settings['build']['fetch_jobs']) as pool:
            entries = list(pool.map(pin, manifest))
        lock = {'schema': 1, 'recipe': identity, 'sources': entries}
        atomic_json(lock_path, lock)
    cancel = threading.Event()
    def obtain(entry):
        # Reserve 128 MiB for bytes in flight and filesystem metadata.
        checkout(entry['url'], entry['commit'], backend / entry['path'],
                 sparse=entry['sparse'], budget=budget, prepared=prepared, cancel=cancel)
    with ThreadPoolExecutor(max_workers=settings['build']['fetch_jobs']) as pool:
        futures = [pool.submit(obtain, entry) for entry in lock['sources']]
        try:
            for future in as_completed(futures):
                future.result()
        except BaseException:
            cancel.set()
            for future in futures:
                future.cancel()
            raise
    if cache_size(backend) >= budget[1]:
        raise ForgeError('Досягнуто ліміт кешу джерел.')
    print(f'Джерела зафіксовано: {len(lock["sources"])} репозиторіїв.', flush=True)
    return backend
