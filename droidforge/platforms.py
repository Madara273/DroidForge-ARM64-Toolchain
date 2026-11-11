"""Linux distribution package plans and host CPU detection."""
import platform
import re
from pathlib import Path
from .core import ForgeError


def normalize_arch(name):
    aliases = {'aarch64': 'arm64', 'arm64': 'arm64',
               'x86_64': 'x86_64', 'amd64': 'x86_64'}
    if name.lower() not in aliases:
        raise ForgeError(f'Непідтримувана архітектура: {name}')
    return aliases[name.lower()]


def validate_host(system, machine, libc):
    if system != 'Linux' or libc != 'glibc':
        raise ForgeError('Потрібен Linux/glibc або Linux-дистрибутив у WSL2.')
    return normalize_arch(machine)


def verify_cache_arch(state, arch):
    for name in ('build', 'host-protoc'):
        directory = Path(state) / name
        if not directory.exists() or not any(directory.iterdir()):
            continue
        systems = list(directory.glob('CMakeFiles/*/CMakeSystem.cmake'))
        if not systems:
            raise ForgeError(f'Кеш без метаданих CPU: {directory}. Використайте окремий checkout.')
        for system in systems:
            match = re.search(r'set\(CMAKE_SYSTEM_PROCESSOR\s+"([^"\n]+)"\)', system.read_text())
            if not match or normalize_arch(match.group(1)) != arch:
                raise ForgeError(f'Кеш для іншого CPU: {directory}. Використайте окремий checkout.')


def package_commands(release):
    family = {release.get('ID', ''), *release.get('ID_LIKE', '').split()}
    if 'arch' in family:
        return [['pacman', '-S', '--needed', '--noconfirm', 'base-devel', 'git',
                 'ca-certificates', 'python', 'cmake', 'ninja', 'clang', 'bison',
                 'flex', 'go', 'patch', 'ccache', 'pkgconf', 'libusb', 'systemd-libs']]
    if family & {'debian', 'ubuntu', 'kali'}:
        return [['apt-get', 'update'],
                ['apt-get', 'install', '-y', 'git', 'ca-certificates', 'python3', 'cmake',
                 'ninja-build', 'clang', 'build-essential', 'bison', 'flex', 'golang-go',
                 'patch', 'ccache', 'pkg-config', 'libusb-1.0-0-dev', 'libudev-dev']]
    raise ForgeError('Автовстановлення підтримує Debian/Kali/Ubuntu та Arch Linux. '
                     'На іншій системі встановіть залежності з README вручну.')


def host_release():
    try:
        return platform.freedesktop_os_release()
    except OSError:
        return {'ID': 'unknown', 'PRETTY_NAME': platform.system()}


def is_wsl():
    return 'microsoft' in platform.release().lower() or 'wsl' in platform.release().lower()
