#!/bin/sh
# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
# Prepare APK build dependencies without compiling tools or editing app projects.
set -eu
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
launch() {
    exec python3 -c 'import sys; sys.path.insert(0, sys.argv.pop(1)); from droidforge.android_setup import main; sys.exit(main())' "$script_dir" "$@"
}
case "${1:-}" in
    --help|-h)
        if command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
            launch --help
        fi
        printf '%s\n' 'Usage: ./setup-android.sh [--archive PATH] [--sdk PATH] [--platform android-37.0] [--build-tools 37.0.0]'
        exit 0 ;;
esac
[ "$(uname -s)" = Linux ] || { echo 'Linux/glibc or a Linux distribution in WSL2 is required.' >&2; exit 1; }
if [ "$(id -u)" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    exec runuser -u "$SUDO_USER" -- "$script_dir/setup-android.sh" "$@"
fi
as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo -- "$@"; fi
}
if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    if command -v apt-get >/dev/null 2>&1; then
        as_root apt-get update
        as_root apt-get install -y python3 ca-certificates
    elif command -v pacman >/dev/null 2>&1; then
        as_root pacman -S --needed --noconfirm python ca-certificates
    else
        echo 'Install Python 3.11+ with your package manager and retry.' >&2
        exit 1
    fi
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo 'Python 3.11+ is required.' >&2; exit 1;
}
launch "$@"
