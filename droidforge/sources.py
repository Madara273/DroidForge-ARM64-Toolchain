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

from .core import ForgeError, atomic_json, config, digest, run


EXCLUDED_SOURCES = {'src/art', 'src/dalvik', 'src/development', 'src/f2fs-tools', 'src/sqlite'}


def tracked_digest(path):
    result = subprocess.run(['git', 'diff', '--binary', 'HEAD'], cwd=path,
                            capture_output=True, check=True)
    return hashlib.sha256(result.stdout).hexdigest()


def verify_tracked(path, expected):
    if tracked_digest(path) != expected:
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
        verify_tracked(path, expected)
    for name, expected in data['files'].items():
        path = backend / name
        if (not path.resolve().is_relative_to(backend.resolve())
                or not path.is_file() or digest(path) != expected):
            raise ForgeError(f'Змінений файл підготовки: {path}')


def selected_manifest(entries):
    sparse = {
        'src/base': ['libs/androidfw', 'tools/aapt2'],
        'src/build': ['tools/zipalign'],
        'src/soong': ['cc/libbuildversion'],
    }
    return [{**entry, 'sparse': sparse.get(entry['path'], [])}
            for entry in entries if entry['path'] not in EXCLUDED_SOURCES]


def cache_size(root):
    total = 0
    for parent, _, files in os.walk(root, followlinks=False):
        for name in files:
            try:
                total += (Path(parent) / name).lstat().st_size
            except FileNotFoundError:
                pass
    return total


def guarded_run(command, *, cwd, budget):
    if budget is None:
        return run(command, cwd=cwd)
    root, limit = budget
    if cache_size(root) >= limit:
        raise ForgeError('Досягнуто ліміт кешу джерел. Завантаження зупинено.')
    print('+ ' + ' '.join(map(str, command)), flush=True)
    process = subprocess.Popen(list(map(str, command)), cwd=cwd, start_new_session=True)
    try:
        while True:
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


def checkout(url, commit, destination, *, sparse=(), budget=None):
    destination = Path(destination)
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
                    cwd=temporary, budget=budget)
        guarded_run(['git', '-c', 'advice.detachedHead=false', 'checkout', '-q', 'FETCH_HEAD'],
                    cwd=temporary, budget=budget)
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
    checkout(recipe['url'], recipe['commit'], backend)
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
    def obtain(entry):
        # Reserve 128 MiB for bytes in flight and filesystem metadata.
        budget = (backend, int(settings['build']['max_source_gib'] * 1024**3) - 128 * 1024**2)
        checkout(entry['url'], entry['commit'], backend / entry['path'],
                 sparse=entry['sparse'], budget=budget)
    with ThreadPoolExecutor(max_workers=settings['build']['fetch_jobs']) as pool:
        futures = [pool.submit(obtain, entry) for entry in lock['sources']]
        for future in as_completed(futures):
            future.result()
    print(f'Джерела зафіксовано: {len(lock["sources"])} репозиторіїв.', flush=True)
    return backend
