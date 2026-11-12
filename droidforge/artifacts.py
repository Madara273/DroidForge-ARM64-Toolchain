# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
"""Verified distributions and explicit SDK installation; no device operations."""
from datetime import datetime, timezone
import io
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile

from .core import ForgeError, TARGETS, atomic_json, digest


def elf_arch(path, arch):
    machines = {'arm64': 183, 'x86_64': 62}
    if arch not in machines:
        raise ForgeError(f'Невідома архітектура: {arch}')
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ForgeError(f'Не звичайний файл: {path}')
    with path.open('rb') as stream:
        header = stream.read(64)
    if (len(header) < 64 or header[:6] != b'\x7fELF\x02\x01'
            or int.from_bytes(header[18:20], 'little') != machines[arch]):
        raise ForgeError(f'Очікувався ELF64 little-endian {arch}: {path}')
    if not os.access(path, os.X_OK):
        raise ForgeError(f'Файл не виконуваний: {path}')


def elf_arm64(path):
    return elf_arch(path, 'arm64')


def make_manifest(stage, targets, provenance, arch='arm64'):
    stage = Path(stage)
    if not targets or set(targets) - set(TARGETS):
        raise ForgeError('Неприпустимий перелік артефактів.')
    for target in targets:
        elf_arch(stage / target, arch)
    files = {}
    for path in sorted(stage.rglob('*')):
        if path.is_symlink():
            raise ForgeError(f'Посилання в артефактах: {path}')
        if path.is_file() and path.name not in ('manifest.json', 'SHA256SUMS'):
            files[path.relative_to(stage).as_posix()] = digest(path)
    atomic_json(stage / 'manifest.json', {'schema': 1, 'architecture': arch, 'targets': list(targets),
                                        'files': files, 'provenance': provenance})
    sums = {**files, 'manifest.json': digest(stage / 'manifest.json')}
    (stage / 'SHA256SUMS').write_text(''.join(f'{sha}  {name}\n' for name, sha in sorted(sums.items())))


def validate_manifest(stage):
    stage = Path(stage)
    manifest = json.loads((stage / 'manifest.json').read_text())
    if manifest.get('schema') != 1:
        raise ForgeError('Невідома версія manifest.')
    targets = manifest.get('targets', [])
    if not targets or len(set(targets)) != len(targets) or set(targets) - set(TARGETS):
        raise ForgeError('Пошкоджений перелік артефактів.')
    for name, sha in manifest['files'].items():
        path = stage / name
        if (not name or Path(name).is_absolute() or '..' in Path(name).parts
                or path.is_symlink() or not path.is_file()
                or not path.resolve().is_relative_to(stage.resolve()) or digest(path) != sha):
            raise ForgeError(f'Перевірка SHA-256 не пройдена: {name}')
    for name in targets:
        if name not in manifest['files']:
            raise ForgeError(f'Артефакт відсутній у manifest: {name}')
        elf_arch(stage / name, manifest['architecture'])
    expected = {**manifest['files'], 'manifest.json': digest(stage / 'manifest.json')}
    actual = (stage / 'SHA256SUMS').read_text()
    if actual != ''.join(f'{sha}  {name}\n' for name, sha in sorted(expected.items())):
        raise ForgeError('SHA256SUMS не відповідає manifest.')
    return manifest


def package(stage, directory):
    stage, directory = Path(stage), Path(directory)
    manifest = validate_manifest(stage)
    directory.mkdir(parents=True, exist_ok=True)
    archive = directory / f'droidforge-linux-{manifest["architecture"]}.tar.xz'
    fd, name = tempfile.mkstemp(dir=directory, suffix='.tar.xz')
    os.close(fd)
    temporary = Path(name)
    try:
        with tarfile.open(temporary, 'w:xz') as tar:
            for name in sorted([*manifest['files'], 'manifest.json', 'SHA256SUMS']):
                data = (stage / name).read_bytes()
                info = tarfile.TarInfo(name)
                info.size = len(data)
                info.mode = 0o755 if name in manifest['targets'] else 0o644
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
        temporary.replace(archive)
    finally:
        temporary.unlink(missing_ok=True)
    (directory / (archive.name + '.sha256')).write_text(f'{digest(archive)}  {archive.name}\n')
    print(f'Архів: {archive}', flush=True)
    return archive


def install_sdk(stage, sdk, version):
    stage, sdk = Path(stage), Path(sdk).absolute()
    manifest = validate_manifest(stage)
    import platform
    from .platforms import normalize_arch
    if manifest['architecture'] != normalize_arch(platform.machine()):
        raise ForgeError('Архітектура інструментів не відповідає цьому CPU. '
                         'Використайте package для перенесення на потрібну систему.')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', version):
        raise ForgeError('Очікується номер build-tools, наприклад 36.0.0.')
    tools = sdk / 'build-tools' / version
    if not (tools / 'source.properties').is_file():
        raise ForgeError('Спочатку встановіть відповідні build-tools через sdkmanager.')
    backup = sdk / '.droidforge-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destinations = {target: (sdk / 'platform-tools' if target in ('adb', 'fastboot') else tools) / target
                    for target in manifest['targets']}
    # Validate the entire install before changing any files.
    for destination in [backup, *destinations.values()]:
        for path in [destination, *destination.parents]:
            if path.is_symlink():
                raise ForgeError(f'Встановлення через symlink заборонено: {path}')
        if destination.exists() and destination.is_dir() and destination != backup:
            raise ForgeError(f'Каталог замість виконуваного файлу: {destination}')
    for target, destination in destinations.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and digest(destination) == digest(stage / target):
            continue
        if destination.exists():
            old = backup / destination.relative_to(sdk)
            old.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(destination, old)
        fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix='.droidforge-')
        os.close(fd)
        try:
            shutil.copyfile(stage / target, temporary)
            os.chmod(temporary, 0o755)
            os.replace(temporary, destination)
        finally:
            Path(temporary).unlink(missing_ok=True)
    print(f'Встановлено. Резервні копії змінених файлів: {backup}')
    if 'aapt2' in manifest['targets']:
        print(f'Параметр для ~/.gradle/gradle.properties:\nandroid.aapt2FromMavenOverride={tools / "aapt2"}')
