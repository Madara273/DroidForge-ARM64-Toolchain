"""Linux distribution package plans and host CPU detection."""
import platform
from .core import ForgeError


def normalize_arch(name):
    aliases = {'aarch64': 'arm64', 'arm64': 'arm64',
               'x86_64': 'x86_64', 'amd64': 'x86_64'}
    if name.lower() not in aliases:
        raise ForgeError(f'Непідтримувана архітектура: {name}')
    return aliases[name.lower()]


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
