"""DroidForge command line entrypoint."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from .core import ForgeError, TARGETS, config, process_lock


def main():
    parser = argparse.ArgumentParser(description='DroidForge — Android host tools із джерел для Linux ARM64')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'bootstrap', 'fetch', 'verify', 'package'):
        commands.add_parser(name)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--targets', nargs='+', choices=TARGETS)
    build_parser.add_argument('--jobs', type=int)
    install_parser = commands.add_parser('install')
    install_parser.add_argument('--sdk', required=True, type=Path)
    install_parser.add_argument('--build-tools', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        config(root)
        with process_lock(root):
            if args.command == 'bootstrap':
                if os.geteuid() != 0:
                    raise ForgeError('Для пакетів: sudo ./forge bootstrap (лише цей крок потребує root).')
                from .core import run
                run(['apt-get', 'update'])
                run(['apt-get', 'install', '-y', 'git', 'ca-certificates', 'python3', 'cmake',
                     'ninja-build', 'clang', 'bison', 'flex', 'golang-go', 'patch', 'ccache',
                     'pkg-config', 'libusb-1.0-0-dev', 'libudev-dev'])
            elif args.command == 'doctor':
                from .build import doctor
                doctor(root)
            elif args.command == 'fetch':
                from .sources import fetch
                fetch(root)
            elif args.command == 'build':
                from .build import build
                build(root, args.targets, args.jobs)
            elif args.command == 'verify':
                from .build import verify
                verify(root / '.forge/out')
            elif args.command == 'package':
                from .artifacts import package
                package(root / '.forge/out', root / 'dist')
            elif args.command == 'install':
                from .artifacts import install_sdk
                install_sdk(root / '.forge/out', args.sdk, args.build_tools)
    except (ForgeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(f'Помилка: {error}', file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print('\nПерервано. Повторіть команду для продовження.', file=sys.stderr)
        sys.exit(130)
