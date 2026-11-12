#!/bin/sh
# Install prerequisites and build the complete native toolchain locally.
# SPDX-License-Identifier: Apache-2.0
set -eu

usage() {
    cat <<'HELP'
Використання: ./build-all.sh [--jobs N]

Автоматично встановлює залежності, збирає всі шість інструментів,
перевіряє їх і створює архів у dist/.
За замовчуванням --jobs 1, щоб заощадити оперативну пам'ять.
Запускайте звичайним користувачем; sudo потрібен лише для залежностей.
У root-середовищі NetHunter запускайте без sudo.
HELP
}

build_jobs=1
while [ "$#" -gt 0 ]; do
    case "$1" in
        --jobs)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            build_jobs=$2
            shift 2
            ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$build_jobs" in
    ''|*[!0-9]*) echo '--jobs має бути числом від 1 до 64.' >&2; exit 2 ;;
esac
if ! { [ "$build_jobs" -ge 1 ] && [ "$build_jobs" -le 64 ]; } 2>/dev/null; then
    echo '--jobs має бути числом від 1 до 64.' >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
build_uid=$(id -u)
original_user=''
if [ "$build_uid" -eq 0 ] && [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != root ]; then
    original_user=$SUDO_USER
    command -v runuser >/dev/null 2>&1 || {
        echo 'Не знайдено runuser. Запустіть ./build-all.sh звичайним користувачем без sudo.' >&2
        exit 1
    }
fi

printf '\nВстановлення залежностей…\n'
if [ "$build_uid" -eq 0 ]; then
    "$script_dir/bootstrap.sh"
else
    command -v sudo >/dev/null 2>&1 || {
        echo 'Для залежностей потрібен sudo або запуск від root.' >&2
        exit 1
    }
    sudo -- "$script_dir/bootstrap.sh"
fi

run_forge() {
    if [ -n "$original_user" ]; then
        runuser -u "$original_user" -- "$script_dir/forge" "$@"
    else
        "$script_dir/forge" "$@"
    fi
}

run_forge doctor
run_forge build --jobs "$build_jobs"
run_forge verify
run_forge package
printf '\nГотово! Інструменти: %s/.forge/out/\nАрхів: %s/dist/\n' "$script_dir" "$script_dir"
