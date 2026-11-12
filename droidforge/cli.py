# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
"""DroidForge command line entrypoint."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from . import __version__
from .core import ForgeError, TARGETS, config, process_lock


def main():
    parser = argparse.ArgumentParser(description='DroidForge: Android host tools із джерел для Linux ARM64/x86_64')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('doctor', 'bootstrap', 'fetch', 'verify', 'package'):
        commands.add_parser(name)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--targets', nargs='+', choices=TARGETS)
    build_parser.add_argument('--jobs', type=int)
    build_parser.add_argument('--arch', choices=('auto', 'arm64', 'x86_64'), default='auto')
    install_parser = commands.add_parser('install')
    install_parser.add_argument('--sdk', required=True, type=Path)
    install_parser.add_argument('--build-tools', required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    try:
        config(root)
        # sudo bootstrap must never create root-owned .forge state in a user's clone.
        if args.command == 'bootstrap':
            if os.geteuid() != 0:
                raise ForgeError('Для пакетів: sudo ./forge bootstrap (лише цей крок потребує root).')
            from .core import run
            from .platforms import host_release, package_commands
            for command in package_commands(host_release()):
                run(command)
            return
        with process_lock(root):
            if args.command == 'doctor':
                from .build import doctor
                doctor(root)
            elif args.command == 'fetch':
                from .sources import fetch
                fetch(root)
            elif args.command == 'build':
                from .build import build
                build(root, args.targets, args.jobs, arch=args.arch)
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
