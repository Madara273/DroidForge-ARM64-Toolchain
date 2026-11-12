# Copyright 2026 Madara273
# SPDX-License-Identifier: Apache-2.0
"""Prepare a user's Android APK build environment without editing app projects."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .artifacts import install_sdk, validate_manifest
from .build import verify
from .core import ForgeError, TARGETS, digest
from .platforms import host_release, validate_host

REPOSITORY = 'Madara273/DroidForge-ARM64-Toolchain'
RELEASE = 'droidforge-linux-arm64-r01'
COMMAND_TOOLS_URL = 'https://dl.google.com/android/repository/commandlinetools-linux-13114758_latest.zip'
COMMAND_TOOLS_SHA = '7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee'


def parse_args(arguments=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, help='Use a downloaded DroidForge archive')
    parser.add_argument('--sha256', help='Optional expected SHA-256 for a local archive')
    parser.add_argument('--java-version', type=int, choices=(17, 21),
                        help='Select JDK 17 or 21 and replace the global Gradle Java setting')
    parser.add_argument('--release', default=RELEASE, help='Public GitHub release tag')
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local/share/droidforge')
    parser.add_argument('--sdk', type=Path, default=Path(os.environ.get('ANDROID_HOME') or
                                                     str(Path.home() / 'Android/Sdk')))
    parser.add_argument('--platform', action='append', help='SDK platform, e.g. android-37.0; repeatable')
    parser.add_argument('--build-tools', action='append', help='Build-tools version; repeatable')
    return parser.parse_args(arguments)


def download(url, destination, expected_sha=None):
    if urllib.parse.urlsplit(url).scheme != 'https':
        raise ForgeError('Downloads require HTTPS.')
    print(f'Downloading {url}', flush=True)
    request = urllib.request.Request(url, headers={'User-Agent': 'DroidForge-Android-Setup'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response, Path(destination).open('wb') as out:
            shutil.copyfileobj(response, out, 1024 * 1024)
    except urllib.error.HTTPError as error:
        raise ForgeError(f'Download returned HTTP {error.code}. The release must be public; '
                         'otherwise use --archive /path/to/downloaded.tar.xz.') from None
    if expected_sha and digest(destination) != expected_sha.lower():
        raise ForgeError(f'SHA-256 mismatch: {Path(destination).name}')


def select_release_asset(release, arch):
    name = f'droidforge-linux-{arch}.tar.xz'
    assets = release.get('assets', [])
    selected = [asset for asset in assets if asset.get('name') == name]
    if len(selected) != 1:
        raise ForgeError(f'Release must contain exactly one {name}.')
    asset = selected[0]
    sha = asset.get('digest') or ''
    if re.fullmatch(r'sha256:[0-9a-fA-F]{64}', sha):
        return asset, sha.split(':')[1].lower()
    if any(item.get('name') == name + '.sha256' for item in assets):
        return asset, None
    raise ForgeError('Release needs a GitHub SHA-256 digest or an archive .sha256 asset.')


def release_archive(tag, directory, arch):
    metadata = directory / 'release.json'
    download(f'https://api.github.com/repos/{REPOSITORY}/releases/tags/'
             + urllib.parse.quote(tag, safe=''), metadata)
    data = json.loads(metadata.read_text())
    asset, sha = select_release_asset(data, arch)
    if sha is None:
        checksum = next(item for item in data['assets'] if item['name'] == asset['name'] + '.sha256')
        checksum_file = directory / 'archive.sha256'
        download(checksum['browser_download_url'], checksum_file)
        sha = checksum_value(checksum_file)
    archive = directory / asset['name']
    download(asset['browser_download_url'], archive, sha)
    return archive, sha


def checksum_value(path):
    text = Path(path).read_text().strip()
    match = re.fullmatch(r'([0-9a-fA-F]{64})(?:[ \t]+\*?[^\r\n]+)?', text)
    if not match:
        raise ForgeError(f'Invalid SHA-256 file: {path}')
    return match[1].lower()


def safe_member(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or path == PurePosixPath('.'):
        raise ForgeError(f'Unsafe archive path: {name}')
    return path


def extract_tools(archive, destination):
    destination = Path(destination)
    with tarfile.open(archive, 'r:xz') as tar:
        members = tar.getmembers()
        if len(members) > 10000 or sum(item.size for item in members) > 256 * 1024 * 1024:
            raise ForgeError('Tool archive exceeds extraction limits.')
        names = set()
        for item in members:
            safe_member(item.name)
            if item.name in names or not (item.isfile() or item.isdir()):
                raise ForgeError(f'Duplicate or unsupported archive member: {item.name}')
            names.add(item.name)
        destination.mkdir(parents=True, exist_ok=True)
        for item in members:
            target = destination / item.name
            if item.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with tar.extractfile(item) as source, target.open('xb') as out:
                shutil.copyfileobj(source, out)
            target.chmod(0o755 if item.name in TARGETS else 0o644)
    manifest = validate_manifest(destination)
    files = {path.relative_to(destination).as_posix() for path in destination.rglob('*') if path.is_file()}
    if files != set(manifest['files']) | {'manifest.json', 'SHA256SUMS'}:
        raise ForgeError('Archive contains files outside its manifest.')
    if set(manifest['targets']) != set(TARGETS):
        raise ForgeError('The setup archive must contain all six DroidForge tools.')
    return manifest


def install_release(archive, prefix, arch, expected_sha=None):
    archive, prefix = Path(archive), Path(prefix)
    sha = digest(archive)
    if expected_sha and sha != expected_sha.lower():
        raise ForgeError('Local archive SHA-256 mismatch.')
    releases = prefix / 'releases'
    releases.mkdir(parents=True, exist_ok=True)
    destination = releases / sha
    if destination.exists():
        manifest = validate_manifest(destination)
    else:
        with tempfile.TemporaryDirectory(dir=releases, prefix='.install-') as directory:
            stage = Path(directory) / 'tools'
            manifest = extract_tools(archive, stage)
            if manifest['architecture'] != arch:
                raise ForgeError('Archive architecture does not match this CPU.')
            stage.rename(destination)
    if manifest['architecture'] != arch:
        raise ForgeError('Archive architecture does not match this CPU.')
    return destination


def update_file(path, content):
    path = Path(path)
    if path.exists() and path.read_text() == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        shutil.copy2(path, str(path) + '.bak-' + stamp)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.droidforge-')
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def property_value(path):
    return str(path).replace('\\', '\\\\').replace(' ', '\\ ').replace(':', '\\:')


def configure_user(home, sdk, java, tools, *, replace_java=False):
    home, sdk, java, tools = map(Path, (home, sdk, java, tools))
    properties = home / '.gradle/gradle.properties'
    settings = {'org.gradle.java.home': property_value(java),
                'android.aapt2FromMavenOverride': property_value(tools / 'aapt2')}
    previous = properties.read_text() if properties.exists() else ''
    if not replace_java and re.search(r'^\s*org\.gradle\.java\.home(?:\s*[=:]|\s+)', previous, re.M):
        settings.pop('org.gradle.java.home')
    lines = [line for line in previous.splitlines()
             if not any(re.match(r'^\s*' + re.escape(key) + r'(?:\s*[=:]|\s+|$)', line)
                        for key in settings)]
    lines += [f'{key}={value}' for key, value in settings.items()]
    update_file(properties, '\n'.join(lines) + '\n')
    environment = home / '.config/droidforge/env.sh'
    update_file(environment, '# Generated by DroidForge setup-android.sh\n'
                + f'export JAVA_HOME={shlex.quote(str(java))}\n'
                + f'export ANDROID_HOME={shlex.quote(str(sdk))}\n'
                + f'export ANDROID_SDK_ROOT={shlex.quote(str(sdk))}\n'
                + f'export PATH={shlex.quote(str(tools))}:{shlex.quote(str(java / "bin"))}:'
                + f'{shlex.quote(str(sdk / "cmdline-tools/latest/bin"))}:'
                + f'{shlex.quote(str(sdk / "platform-tools"))}:"$PATH"\n')
    source = f'[ ! -f {shlex.quote(str(environment))} ] || . {shlex.quote(str(environment))}'
    startup = ['.profile', '.bashrc', '.zshenv']
    startup += [name for name in ('.bash_profile', '.bash_login') if (home / name).exists()]
    for name in startup:
        path = home / name
        old = path.read_text() if path.exists() else ''
        if source not in old:
            update_file(path, old.rstrip('\n') + '\n\n# DroidForge Android environment\n' + source + '\n')
    return environment


def sdk_packages(platforms, versions):
    if any(not re.fullmatch(r'android-\d+(?:\.\d+)?', item) for item in platforms):
        raise ForgeError('Expected --platform android-37.0 or another Android SDK platform.')
    if any(not re.fullmatch(r'\d+\.\d+\.\d+(?:-[A-Za-z0-9.]+)?', item) for item in versions):
        raise ForgeError('Expected --build-tools 37.0.0 or another SDK build-tools version.')
    return [*(f'platforms;{item}' for item in platforms), *(f'build-tools;{item}' for item in versions)]


def execute(command, *, env=None, input_text=None):
    print('+ ' + shlex.join(map(str, command)), flush=True)
    subprocess.run(list(map(str, command)), check=True, env=env, input=input_text, text=True)


def privileged(command):
    if os.geteuid() == 0:
        execute(command)
    elif shutil.which('sudo'):
        execute(['sudo', '--', *command])
    else:
        raise ForgeError('Installing system dependencies requires sudo or root.')


def runtime_packages():
    release = host_release()
    family = {release.get('ID', ''), *release.get('ID_LIKE', '').split()}
    if family & {'debian', 'ubuntu', 'kali'}:
        packages = ['ca-certificates', 'libstdc++6', 'libgcc-s1', 'zlib1g', 'libusb-1.0-0', 'libudev1']
        missing = []
        for package in packages:
            result = subprocess.run(['dpkg-query', '-W', '-f=${Status}', package],
                                    capture_output=True, text=True)
            if result.returncode or result.stdout != 'install ok installed':
                missing.append(package)
        if missing:
            privileged(['apt-get', 'update'])
            privileged(['apt-get', 'install', '-y', *missing])
    elif 'arch' in family:
        packages = ['ca-certificates', 'gcc-libs', 'glibc', 'zlib', 'libusb', 'systemd-libs']
        missing = [item for item in packages if subprocess.run(
            ['pacman', '-Q', item], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode]
        if missing:
            privileged(['pacman', '-S', '--needed', '--noconfirm', *missing])
    else:
        raise ForgeError('Automatic package installation supports Debian/Kali/Ubuntu and Arch Linux.')


def java_home(prefix, temporary, arch, version=21, preferred=None):
    candidates = [preferred or '', os.environ.get('JAVA_HOME', ''), str(prefix / f'jdk-{version}')]
    if shutil.which('javac'):
        candidates.append(str(Path(shutil.which('javac')).resolve().parent.parent))
    for candidate in candidates:
        path = Path(candidate)
        if not candidate or not (path / 'bin/javac').is_file():
            continue
        result = subprocess.run([str(path / 'bin/javac'), '-version'], capture_output=True, text=True)
        if result.returncode == 0 and re.search(rf'javac {version}(?:\.|\s|$)', result.stdout + result.stderr):
            return path.resolve()
    # A user-local JDK also supports distributions whose repositories lack JDK 21.
    metadata = temporary / 'jdk.json'
    cpu = 'aarch64' if arch == 'arm64' else 'x64'
    download(f'https://api.adoptium.net/v3/assets/latest/{version}/hotspot?architecture={cpu}'
             '&image_type=jdk&os=linux&vendor=eclipse', metadata)
    assets = json.loads(metadata.read_text())
    if not assets:
        raise ForgeError(f'No Temurin JDK {version} download was returned.')
    package = assets[0]['binary']['package']
    archive = temporary / 'jdk.tar.gz'
    download(package['link'], archive, package['checksum'])
    unpacked = temporary / 'jdk'
    unpacked.mkdir()
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(unpacked, filter='data')
    roots = list(unpacked.iterdir())
    if len(roots) != 1 or not (roots[0] / 'bin/javac').is_file():
        raise ForgeError('Unexpected JDK archive layout.')
    target = prefix / f'jdk-{version}'
    if target.exists():
        raise ForgeError(f'Existing JDK is unusable; move it aside and retry: {target}')
    shutil.move(roots[0], target)
    return target


def command_tools(sdk, temporary):
    manager = sdk / 'cmdline-tools/latest/bin/sdkmanager'
    if manager.is_file() and os.access(manager, os.X_OK):
        return manager
    archive = temporary / 'command-tools.zip'
    download(COMMAND_TOOLS_URL, archive, COMMAND_TOOLS_SHA)
    unpacked = temporary / 'command-tools'
    with zipfile.ZipFile(archive) as zip:
        for info in zip.infolist():
            safe_member(info.filename)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise ForgeError('Symlinks in command-line tools ZIP are unsupported.')
        zip.extractall(unpacked)
    source = unpacked / 'cmdline-tools'
    for path in (source / 'bin').iterdir():
        path.chmod(0o755)
    latest = manager.parent.parent
    latest.parent.mkdir(parents=True, exist_ok=True)
    if latest.exists():
        raise ForgeError(f'Incomplete command-line tools installation; move it aside and retry: {latest}')
    shutil.move(source, latest)
    return manager


def main(arguments=None):
    args = parse_args(arguments)
    try:
        arch = validate_host(platform.system(), platform.machine(), platform.libc_ver()[0])
        platforms = args.platform or ['android-37.0']
        versions = args.build_tools or ['36.0.0', '37.0.0']
        packages = sdk_packages(platforms, versions)
        if args.sha256 and not re.fullmatch(r'[0-9a-fA-F]{64}', args.sha256):
            raise ForgeError('--sha256 must contain 64 hexadecimal characters.')
        if args.sha256 and not args.archive:
            raise ForgeError('--sha256 requires --archive.')
        if args.archive and not args.archive.is_file():
            raise ForgeError(f'Archive does not exist: {args.archive}')
        prefix, sdk = args.prefix.expanduser().absolute(), args.sdk.expanduser().absolute()
        prefix.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.setup-', dir=prefix) as directory:
            temporary = Path(directory)
            tools = None
            if not args.archive:
                runtime_packages()
            if arch == 'arm64' or args.archive:
                if args.archive:
                    archive, sha = args.archive, args.sha256
                    sidecar = Path(str(archive) + '.sha256')
                    if sha is None and sidecar.exists():
                        sha = checksum_value(sidecar)
                    if sha is None:
                        print('Local archive: checking manifest and file hashes; no external SHA-256 supplied.', flush=True)
                else:
                    archive, sha = release_archive(args.release, temporary, arch)
                tools = install_release(archive, prefix, arch, sha)
            if args.archive:
                runtime_packages()
            if tools is not None:
                # Refuse incompatible glibc/libstdc++ binaries before touching an SDK.
                verify(tools)
            preferred = None
            java_version = args.java_version or 21
            if args.java_version is None:
                properties = Path.home() / '.gradle/gradle.properties'
                if properties.exists():
                    match = re.search(r'^\s*org\.gradle\.java\.home\s*[=:]\s*(.+)$',
                                      properties.read_text(), re.M)
                    if match:
                        preferred = match[1].replace('\\ ', ' ').replace('\\:', ':').replace('\\\\', '\\')
                        javac = Path(preferred) / 'bin/javac'
                        if javac.is_file():
                            result = subprocess.run([str(javac), '-version'], capture_output=True, text=True)
                            version_match = re.search(r'javac (17|21)(?:\.|\s|$)', result.stdout + result.stderr)
                            if version_match:
                                java_version = int(version_match[1])
            java = java_home(prefix, temporary, arch, java_version, preferred)
            environment = dict(os.environ, JAVA_HOME=str(java), ANDROID_HOME=str(sdk), ANDROID_SDK_ROOT=str(sdk))
            environment['PATH'] = str(java / 'bin') + os.pathsep + os.environ.get('PATH', '')
            sdk.mkdir(parents=True, exist_ok=True)
            manager = command_tools(sdk, temporary)
            print('Installing Android SDK packages and accepting their licenses.', flush=True)
            execute([manager, f'--sdk_root={sdk}', *packages], env=environment, input_text='y\n' * 200)
            for item in packages:
                expected = sdk.joinpath(*item.split(';')) / 'source.properties'
                if not expected.is_file():
                    raise ForgeError(f'SDK package is missing after sdkmanager completed: {item}')
            if tools is not None:
                # Patch every installed build-tools version so explicit project versions work too.
                for version in sorted((sdk / 'build-tools').iterdir()):
                    if (version / 'source.properties').is_file():
                        install_sdk(tools, sdk, version.name)
            else:
                execute([manager, f'--sdk_root={sdk}', 'platform-tools'], env=environment, input_text='y\n' * 200)
                tools = sdk / 'build-tools' / versions[-1]
            if arch == 'x86_64' and not args.archive:
                execute([tools / 'aapt2', 'version'], env=environment)
            source = configure_user(Path.home(), sdk, java, tools,
                                    replace_java=args.java_version is not None)
        print('\nAndroid build environment is ready. No app project was modified.')
        print('For this terminal: . ' + shlex.quote(str(source)))
        print('Then, in your app directory: ./gradlew assembleDebug')
        print('For another compileSdk/build-tools version, rerun with --platform / --build-tools.')
    except (ForgeError, subprocess.CalledProcessError, OSError, ValueError, tarfile.TarError,
            zipfile.BadZipFile, subprocess.TimeoutExpired) as error:
        print(f'Setup failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
