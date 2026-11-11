"""Native host build orchestration, source preparation and smoke checks."""
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

from .core import ForgeError, TARGETS, atomic_json, config, digest, run, timestamp
from .sources import cache_size, checkout, fetch, tracked_digest
from .artifacts import elf_arch, make_manifest, validate_manifest
from .platforms import normalize_arch, host_release, is_wsl

DEPENDENCIES = ('git', 'cmake', 'ninja', 'clang', 'clang++', 'gcc', 'g++', 'bison', 'flex', 'go', 'patch')


def rewrite(text, before, after, label):
    if before in after and after in text:
        return text
    if before in text:
        return text.replace(before, after)
    if after not in text:
        raise ForgeError(f'Неочікуваний вміст pinned source: {label}')
    return text


def selected_targets(targets):
    if not targets or len(set(targets)) != len(targets) or set(targets) - set(TARGETS):
        raise ForgeError('Оберіть неповторювані цілі: ' + ', '.join(TARGETS))
    return list(targets)


def jobs_count(value, settings):
    value = settings['jobs'] if value is None else value
    if not isinstance(value, int) or not 1 <= value <= 64:
        raise ForgeError('jobs має бути в межах 1..64.')
    return value


def doctor(root):
    if platform.system() != 'Linux':
        raise ForgeError('Запустіть у Linux/glibc або WSL2 з Linux-дистрибутивом.')
    arch = normalize_arch(platform.machine())
    print(f'Система: {host_release().get("PRETTY_NAME", "Linux")} | {arch}'
          + (' | WSL' if is_wsl() else ''))
    missing = []
    for name in DEPENDENCIES:
        location = shutil.which(name)
        print(f'{name:12} {location or "НЕ ВСТАНОВЛЕНО"}')
        if not location:
            missing.append(name)
    print(f'Вільне місце: {shutil.disk_usage(root).free / 1024**3:.1f} GiB')
    print(f'Кеш джерел: {cache_size(Path(root) / ".forge/recipe") / 1024**3:.2f} GiB / '
          f'{config(root)["build"]["max_source_gib"]} GiB')
    if missing:
        raise ForgeError('Виконайте ./forge bootstrap; відсутні: ' + ', '.join(missing))


def prepare(root, backend):
    """Apply the pinned recipe's generated source helpers and our six-tool graph.

    Legacy patches for ART are deliberately out of scope. All applied text edits
    and generated helper copies are recorded with hashes in preparation.json.
    """
    changes = {}
    def write(path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
        changes[path.relative_to(backend).as_posix()] = digest(path)
    def copy(source, target):
        write(backend / target, (backend / source).read_bytes())
    def replace(name, before, after):
        path = backend / name
        text = rewrite(path.read_text(), before, after, name)
        write(path, text.encode())

    copy('patches/misc/IncrementalProperties.sysprop.h',
         'src/incremental_delivery/sysprop/include/IncrementalProperties.sysprop.h')
    copy('patches/misc/IncrementalProperties.sysprop.cpp',
         'src/incremental_delivery/sysprop/IncrementalProperties.sysprop.cpp')
    # Do not embed the recipe's precompiled classes.dex payload. Fastdeploy is
    # rejected by the command parser; these source-only placeholders are unused.
    for name, symbol in (('deployagent.inc', 'kDeployAgent'), ('deployagentscript.inc', 'kDeployAgentScript')):
        write(backend / 'src/adb/fastdeploy/deployagent' / name,
              f'// DroidForge: fastdeploy disabled; no embedded DEX.\nunsigned char {symbol}[] = {{0}};\n'.encode())
    replace('src/adb/client/adb_install.cpp', '*use_fastdeploy = true;',
            'error_exit("DroidForge: --fastdeploy is disabled (no precompiled agent).");')
    replace('src/adb/client/commandline.cpp', '--fastdeploy: use fast deploy',
            '--fastdeploy: unavailable in this DroidForge build')
    copy('patches/misc/platform_tools_version.h',
         'src/soong/cc/libbuildversion/include/platform_tools_version.h')
    # AOSP adds config.h to protobuf sources; upstream CMake omits its directory.
    replace('src/protobuf/CMakeLists.txt', '${protobuf_SOURCE_DIR}/src)',
            '${protobuf_SOURCE_DIR}/src\n  ${protobuf_SOURCE_DIR}/config)')
    # The forced compatibility header initializes glibc feature macros before
    # this file's _GNU_SOURCE undef. Select the actual POSIX ABI explicitly.
    replace('src/libbase/posix_strerror_r.cpp', '#include <string.h>',
            '#include <string.h>\n#ifdef __GLIBC__\nextern "C" int __xpg_strerror_r(int, char*, size_t) noexcept;\n#endif')
    replace('src/libbase/posix_strerror_r.cpp', '  return strerror_r(errnum, buf, buflen);',
            '#ifdef __GLIBC__\n  return __xpg_strerror_r(errnum, buf, buflen);\n'
            '#else\n  return strerror_r(errnum, buf, buflen);\n#endif')
    replace('src/base/libs/androidfw/AssetsProvider.cpp', 'struct stat sb{.st_mtime = -1};',
            'struct stat sb{};\n  sb.st_mtime = -1;')
    for name, imports in {'ApkInfo.proto': ['Resources.proto'],
                          'Resources.proto': ['Configuration.proto'],
                          'ResourcesInternal.proto': ['Configuration.proto', 'Resources.proto']}.items():
        for imported in imports:
            replace('src/base/tools/aapt2/' + name,
                    'frameworks/base/tools/aapt2/' + imported, imported)
    path = backend / 'src/abseil-cpp/CMakeLists.txt'
    if '/usr/src/googletest' in path.read_text():
        replace('src/abseil-cpp/CMakeLists.txt', '/usr/src/googletest', '${CMAKE_SOURCE_DIR}/src/googletest')
    destination = backend / 'src/boringssl/src/third_party/googletest'
    if destination.parent.exists() and not destination.exists():
        destination.symlink_to(backend / 'src/googletest')
    # Select only our six executable targets; retain e2fs libraries for fastboot.
    for category, includes in {
        'build-tools': ['aapt2', 'aidl', 'zipalign', 'split-select'],
        'platform-tools': ['e2fsprogs', 'fastboot', 'adb'],
    }.items():
        content = (f'# DroidForge six-tool overlay; upstream recipes retain their notices.\n'
                   f'project({category})\nset(EXECUTABLE_OUTPUT_PATH ${{CMAKE_BINARY_DIR}}/bin/{category})\n'
                   + ''.join(f'include({name}.cmake)\n' for name in includes))
        write(backend / category / 'CMakeLists.txt', content.encode())
    top = backend / 'CMakeLists.txt'
    text = top.read_text().replace('add_subdirectory(others)', '# DroidForge: other executables excluded')
    if '# DroidForge glibc entropy declarations' not in text:
        text += ('\n# DroidForge glibc entropy declarations\n'
                 'target_compile_definitions(expat PRIVATE _DEFAULT_SOURCE)\n')
    write(top, text.encode())
    # Keep generated Flex/Bison C++ out of the immutable source checkout.
    aidl_recipe = backend / 'build-tools/aidl.cmake'
    aidl_text = aidl_recipe.read_text()
    for generated in ('aidl_language_y.cpp', 'aidl_language_y.h',
                      'aidl_language_l.cpp', 'aidl_language_l.h'):
        aidl_text = aidl_text.replace('${SRC}/aidl/' + generated,
                                      '${CMAKE_BINARY_DIR}/generated/aidl/' + generated)
    if '# DroidForge generated parser directory' not in aidl_text:
        aidl_text = ('# DroidForge generated parser directory\n'
                     'file(MAKE_DIRECTORY ${CMAKE_BINARY_DIR}/generated/aidl)\n' + aidl_text
                     + '\ntarget_include_directories(aidl PRIVATE ${SRC}/aidl '
                     '${CMAKE_BINARY_DIR}/generated/aidl)\n')
    write(aidl_recipe, aidl_text.encode())
    lock = json.loads((Path(root) / 'sources.lock.json').read_text())
    diffs = {'.': tracked_digest(backend, nested_sources=True)}
    diffs.update({entry['path']: tracked_digest(backend / entry['path']) for entry in lock['sources']})
    atomic_json(Path(root) / '.forge/preparation.json', {
        'files': changes, 'diffs': diffs,
        'compat_sha256': digest(Path(root) / 'compat/linux_glibc.h')})


def build(root, targets=None, jobs=None, arch='auto'):
    root = Path(root)
    settings = config(root)
    targets = selected_targets(targets or settings['build']['targets'])
    jobs = jobs_count(jobs, settings['build'])
    doctor(root)
    host_arch = normalize_arch(platform.machine())
    if arch != 'auto' and arch != host_arch:
        raise ForgeError('Ця версія збирає інструменти для поточного процесора; '
                         f'на цьому хості виберіть --arch {host_arch}.')
    arch = host_arch
    state = root / '.forge'
    host_stamp = state / 'build-host.json'
    if host_stamp.exists() and json.loads(host_stamp.read_text())['architecture'] != arch:
        raise ForgeError('Кеш створено на іншій архітектурі. Використайте окремий checkout проєкту.')
    atomic_json(host_stamp, {'architecture': arch})
    backend = fetch(root)
    prepare(root, backend)
    log = state / 'logs/build.log'
    environment = os.environ.copy()
    environment.update(CC='gcc', CXX='g++')
    environment.update(CCACHE_DIR=str(state / 'ccache'), CCACHE_MAXSIZE='256M')
    host = state / 'host-protoc'
    common = ['-GNinja', '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
              '-DCMAKE_C_COMPILER=clang', '-DCMAKE_CXX_COMPILER=clang++']
    if shutil.which('ccache'):
        common += ['-DCMAKE_C_COMPILER_LAUNCHER=ccache', '-DCMAKE_CXX_COMPILER_LAUNCHER=ccache']
    print(f'Компіляція з jobs={jobs}. Журнал: {log}', flush=True)
    run(['cmake', '-S', backend / 'src/protobuf', '-B', host, *common,
         '-Dprotobuf_BUILD_TESTS=OFF', '-Dprotobuf_WITH_ZLIB=OFF'], log=log, env=environment)
    run(['cmake', '--build', host, '--target', 'protoc', '-j', jobs], log=log, env=environment)
    protoc = host / 'protoc'
    if not protoc.is_file():
        candidates = list(host.glob('protoc-*'))
        if not candidates:
            raise ForgeError('Не знайдено зібраний protoc.')
        protoc = candidates[0]
    target_dir = state / 'build'
    compat = root / 'compat/linux_glibc.h'
    target_common = [item.replace('-DCMAKE_C_COMPILER=clang', '-DCMAKE_C_COMPILER=gcc')
                    .replace('-DCMAKE_CXX_COMPILER=clang++', '-DCMAKE_CXX_COMPILER=g++') for item in common]
    run(['cmake', '-S', backend, '-B', target_dir, *target_common, '-DSDK_STATIC=OFF', '-DSDK_STRIP=ON',
         '-DPROTOC_PATH=' + str(protoc), '-DCMAKE_CXX_FLAGS=-include ' + str(compat)], log=log, env=environment)
    run(['cmake', '--build', target_dir, '--target', *targets, '-j', jobs], log=log, env=environment)
    stage = Path(tempfile.mkdtemp(prefix='stage-', dir=state))
    try:
        for target in targets:
            category = 'platform-tools' if target in ('adb', 'fastboot') else 'build-tools'
            source = target_dir / 'bin' / category / target
            elf_arch(source, arch)
            shutil.copy2(source, stage / target)
        collect_notices(root, backend, stage)
        lock = json.loads((root / 'sources.lock.json').read_text())
        provenance = {'created': timestamp(), 'sources': lock,
                      'preparation': json.loads((state / 'preparation.json').read_text()),
                      'compiler': run(['g++', '--version'], capture=True),
                      'host': platform.platform(), 'jobs': jobs,
                      'features': {'adb_fastdeploy': False},
                      'orchestrator_sha256': {
                          path.relative_to(root).as_posix(): digest(path)
                          for folder in ('droidforge', 'compat')
                          for path in (root / folder).rglob('*')
                          if path.is_file() and '__pycache__' not in path.parts}}
        make_manifest(stage, targets, provenance, arch=arch)
        verify(stage)
        output = state / 'out'
        if output.exists():
            previous = state / 'previous-out'
            if previous.exists(): shutil.rmtree(previous)
            output.rename(previous)
        stage.rename(output)
        print(f'Збірка і перевірка успішні: {output}', flush=True)
    finally:
        if stage.exists(): shutil.rmtree(stage)


def collect_notices(root, backend, stage):
    shutil.copy2(Path(root) / 'NOTICE', stage / 'NOTICES')
    shutil.copy2(backend / 'LICENSE.txt', stage / 'RECIPE-LICENSE.txt')
    lock = json.loads((Path(root) / 'sources.lock.json').read_text())
    for entry in lock['sources']:
        source = backend / entry['path']
        for path in source.rglob('*'):
            # Include notices shipped in subprojects, but never Git internals.
            if '.git' in path.parts or not path.is_file() or path.is_symlink():
                continue
            if path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                destination = stage / 'licenses' / source.name / path.relative_to(source)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)


def verify(stage):
    manifest = validate_manifest(stage)
    checks = {}
    commands = {'aapt2': ['version'], 'aidl': ['--version'], 'zipalign': [],
                'split-select': ['--help'], 'adb': ['version'], 'fastboot': ['--version']}
    for target in manifest['targets']:
        result = subprocess.run([str(Path(stage) / target), *commands[target]],
                                capture_output=True, text=True, timeout=30)
        output = result.stdout + result.stderr
        # zipalign and split-select use exit 1 for help/usage.
        expected = (0, 1, 2) if target in ('zipalign', 'split-select') else (0,)
        if result.returncode not in expected or not output.strip():
            raise ForgeError(f'Smoke test failed: {target}\n{output}')
        checks[target] = output.strip()[:3000]
        print(f'{target}: запуск OK')
    if 'aapt2' in manifest['targets']:
        with tempfile.TemporaryDirectory(prefix='forge-resource-') as directory:
            root = Path(directory)
            values = root / 'res/values'
            values.mkdir(parents=True)
            (values / 'strings.xml').write_text('<resources><string name="app_name">DroidForge</string></resources>')
            run([Path(stage) / 'aapt2', 'compile', '--dir', root / 'res', '-o', root / 'resources.zip'])
            if not (root / 'resources.zip').is_file():
                raise ForgeError('AAPT2 не створив скомпільовані ресурси.')
        checks['aapt2_compile_resource'] = 'passed'
    atomic_json(Path(stage).parent / 'verification.json', {'verified': timestamp(), 'checks': checks})
    return checks
