#!/bin/sh
# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
set -eu
usage() {
    printf '%s\n' 'Використання: ./docker-build-arm64.sh [--jobs 1..64]' \
        'Збирає ARM64 інструменти в Docker; на x86 потрібна емуляція ARM64.'
}
build_jobs=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || printf '1')
case "$build_jobs" in ''|*[!0-9]*) build_jobs=1 ;; esac
[ "$build_jobs" -ge 1 ] || build_jobs=1
[ "$build_jobs" -le 64 ] || build_jobs=64
while [ "$#" -gt 0 ]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --jobs)
            [ "$#" -ge 2 ] || { usage >&2; exit 2; }
            build_jobs=$2; shift 2 ;;
        *) usage >&2; exit 2 ;;
    esac
done
case "$build_jobs" in ''|*[!0-9]*|0*|???*) usage >&2; exit 2 ;; esac
[ "$build_jobs" -le 64 ] || { usage >&2; exit 2; }
command -v docker >/dev/null 2>&1 || {
    printf '%s\n' 'Спочатку встановіть Docker Engine + Buildx або Docker Desktop з WSL2 integration.' >&2
    exit 1
}
docker info >/dev/null
docker buildx version >/dev/null
project_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
mkdir -p "$project_dir/.docker-cache/arm64" "$project_dir/.forge" "$project_dir/dist"
docker buildx build --platform linux/arm64 --load \
    -t droidforge-arm64:local -f "$project_dir/Dockerfile" "$project_dir"
printf '%s\n' 'Запуск ARM64. Якщо виникне exec format error, увімкніть QEMU/binfmt (див. README).'
docker run --rm --init --platform linux/arm64 \
    --user "$(id -u):$(id -g)" \
    -v "$project_dir:/workspace:ro" \
    -v "$project_dir/.docker-cache/arm64:/workspace/.forge" \
    -v "$project_dir/dist:/workspace/dist" \
    -e GOCACHE=/workspace/.forge/go-cache \
    -e GOPATH=/workspace/.forge/go \
    -e XDG_CACHE_HOME=/workspace/.forge/xdg-cache \
    -e XDG_CONFIG_HOME=/workspace/.forge/xdg-config \
    -e GIT_CONFIG_GLOBAL=/dev/null -e GIT_CONFIG_NOSYSTEM=1 \
    droidforge-arm64:local sh -eu -c '
        ./forge doctor
        ./forge build --arch arm64 --jobs "$1"
        ./forge verify
        ./forge package
    ' sh "$build_jobs"
printf '%s\n' "Архів: $project_dir/dist/droidforge-linux-arm64.tar.xz"
