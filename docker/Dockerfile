# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
    git ca-certificates python3 cmake ninja-build clang build-essential \
    bison flex golang-go patch ccache pkg-config libusb-1.0-0-dev libudev-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
