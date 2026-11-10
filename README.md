# DroidForge ARM64 Toolchain

**English** | [Українська](README.uk.md)

Android tools for Linux ARM64 and x86_64: `aapt2`, `aidl`, `zipalign`,
`split-select`, `adb`, and `fastboot`. Install a release to build APKs,
or compile the tools from source.

Supports Linux/glibc: Kali, Debian/Ubuntu, Arch, and these distributions in WSL2.
Tested on Kali ARM64. In WSL2, use a Linux terminal and keep the repository under `~/`.

```sh
git clone https://github.com/Madara273/DroidForge-ARM64-Toolchain.git
cd DroidForge-ARM64-Toolchain
```

## Set up APK builds

```sh
./setup-android.sh
. "$HOME/.config/droidforge/env.sh"
```

Installs or reuses JDK and Android SDK, installs native tools, and configures
Gradle's AAPT2 path. Defaults: JDK 21, platform `android-37.0`, build-tools
`36.0.0` and `37.0.0`. SDK licenses are accepted automatically.

On ARM64, setup downloads release `droidforge-linux-arm64-r01` from the public
repository. On x86_64, it uses Google's SDK tools. To use a downloaded archive:

```sh
./setup-android.sh --archive "$HOME/droidforge-linux-arm64.tar.xz"
```

Then build your app:

```sh
cd /path/to/your/android-app
./gradlew assembleDebug
```

Useful options:

```sh
./setup-android.sh --platform android-36 --build-tools 36.0.0
./setup-android.sh --java-version 17
./setup-android.sh --sdk "$HOME/Android/Sdk"
```

Existing Gradle Java settings are preserved unless `--java-version` is supplied.
The SDK defaults to `ANDROID_HOME` or `~/Android/Sdk`. Tools are stored under
`~/.local/share/droidforge/`. Modified configuration and SDK files are backed up.
Projects using NDK/CMake or other SDK/JDK versions need those dependencies too.

ARM64 release r01 requires **GLIBC_2.38** and **GLIBCXX_3.4.32**. On older
systems, build from source. Gallery was successfully built with the local r01
archive; fresh SDK/JDK and public-release downloads have not been tested end to end.

## Build the tools from source

Build for the current CPU:

```sh
./build-all.sh
```

Dependencies are installed automatically through apt or pacman. The script
builds, verifies, and packages all six tools. It uses available CPU threads,
up to 64; use `--jobs 4` to set a limit. Repeated runs reuse the cache.
Run as your regular user; sudo is requested for dependencies. In a root
NetHunter environment, run without sudo.

To build ARM64 tools on an x86_64 Linux or WSL2 host, install Docker with
Buildx and run:

```sh
./docker-build-arm64.sh
```

This uses an ARM64 container with QEMU on x86_64, so compilation is slower.
If Docker reports `exec format error`, enable ARM64 emulation once:

```sh
docker run --privileged --rm tonistiigi/binfmt --install arm64
```

Docker Desktop includes emulation. The Docker build has not yet been tested end to end.

## Output and logs

| | Native build | Docker build |
| --- | --- | --- |
| Tools | `.forge/out/` | `.docker-cache/arm64/out/` |
| Log | `.forge/logs/build.log` | `.docker-cache/arm64/logs/build.log` |
| Archive | `dist/droidforge-linux-<arch>.tar.xz` | `dist/droidforge-linux-arm64.tar.xz` |

Build progress is shown in the terminal. Archives include checksums and licenses.
The source cache is limited to **4.5 GiB**; SDK/JDK packages, Docker images,
and compilation output need additional space. Total disk usage is not capped at 5 GB.

For individual steps or selected tools:

```sh
./forge doctor
./forge fetch
./forge build --targets aapt2 adb --jobs 4
./forge verify
./forge package
```

## Source and license

Source baseline: AOSP `platform-tools-35.0.2`, with pinned Git commits.
Build recipes: [lzhiyong/android-sdk-tools](https://github.com/lzhiyong/android-sdk-tools).
ADB `--fastdeploy` is disabled. Output targets Linux/glibc, including a glibc
userland in NetHunter, rather than Termux/bionic.

Copyright 2026 Madara273. Original scripts and DroidForge code: Apache-2.0.
Third-party tools retain their own licenses.
See [LICENSE](LICENSE) and [NOTICE](NOTICE).
