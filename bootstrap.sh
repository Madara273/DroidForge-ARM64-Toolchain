#!/bin/sh
# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
# Minimal entry point for hosts without Python yet.
set -eu
if [ "$(id -u)" -ne 0 ]; then
    echo 'Запустіть sudo ./bootstrap.sh (компіляція далі працює без root).' >&2
    exit 1
fi
need_python=0
if ! command -v python3 >/dev/null 2>&1; then
    need_python=1
elif ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))'; then
    need_python=1
fi
if [ "$need_python" -eq 1 ] || ! command -v git >/dev/null 2>&1; then
    if command -v pacman >/dev/null 2>&1; then
        pacman -S --needed --noconfirm python git ca-certificates
    elif command -v apt-get >/dev/null 2>&1; then
        apt-get update
        apt-get install -y python3 git ca-certificates
    else
        echo 'Встановіть Python 3.11+ і Git через менеджер пакетів вашої системи.' >&2
        exit 1
    fi
fi
python3 -c 'import sys; sys.exit(sys.version_info < (3, 11))' || {
    echo 'Репозиторії цієї системи не надали Python 3.11+. Потрібен новіший Python.' >&2
    exit 1
}
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/forge" bootstrap
