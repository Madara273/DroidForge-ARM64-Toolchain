import io
from pathlib import Path
import tarfile
import tempfile
import subprocess
import sys
import zipfile
import unittest
from unittest.mock import patch

from droidforge.android_setup import (extract_tools, configure_user, select_release_asset,
                                     sdk_packages, install_release, parse_args, main, command_tools, java_home)
from droidforge.artifacts import make_manifest, package
from droidforge.core import ForgeError, TARGETS


class AndroidSetupTests(unittest.TestCase):
    def archive(self, root):
        stage = root / 'stage'
        stage.mkdir()
        for target in TARGETS:
            header = bytearray(64)
            header[:6] = b'\x7fELF\x02\x01'
            header[18:20] = (183).to_bytes(2, 'little')
            path = stage / target
            path.write_bytes(header)
            path.chmod(0o755)
        make_manifest(stage, TARGETS, {}, 'arm64')
        return package(stage, root / 'dist')

    def test_valid_archive_and_idempotent_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            first = install_release(archive, root / 'prefix', 'arm64')
            self.assertEqual((first / 'aapt2').read_bytes(), (root / 'stage/aapt2').read_bytes())
            self.assertEqual(install_release(archive, root / 'prefix', 'arm64'), first)
            with self.assertRaises(ForgeError):
                install_release(archive, root / 'other', 'x86_64')

    def test_rejects_unsafe_members_before_extraction(self):
        for kind in ('traversal', 'symlink', 'duplicate'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                archive = root / 'unsafe.tar.xz'
                with tarfile.open(archive, 'w:xz') as tar:
                    member = tarfile.TarInfo('../escape' if kind == 'traversal' else 'aapt2')
                    if kind == 'symlink':
                        member.type = tarfile.SYMTYPE
                        member.linkname = '/etc/passwd'
                    else:
                        member.size = 1
                    tar.addfile(member, io.BytesIO(b'x'))
                    if kind == 'duplicate':
                        tar.addfile(member, io.BytesIO(b'y'))
                with self.assertRaises(ForgeError):
                    extract_tools(archive, root / 'out')
                self.assertFalse((root / 'escape').exists())

    def test_corruption_and_checksum_mismatch_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            with self.assertRaises(ForgeError):
                install_release(archive, root / 'prefix', 'arm64', expected_sha='0' * 64)
            stage = root / 'stage'
            (stage / 'aidl').write_bytes(b'corrupt')
            bad = root / 'bad.tar.xz'
            with tarfile.open(bad, 'w:xz') as tar:
                for path in stage.iterdir():
                    tar.add(path, arcname=path.name)
            with self.assertRaises(ForgeError):
                install_release(bad, root / 'prefix', 'arm64')

    def test_configuration_preserves_user_options_and_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix='android home ') as directory:
            home = Path(directory)
            properties = home / '.gradle/gradle.properties'
            properties.parent.mkdir()
            properties.write_text('# keep\ncustom.setting=value\nandroid.aapt2FromMavenOverride=old\n'
                                  'android.aapt2FromMavenOverride:duplicate\n')
            profile = home / '.bashrc'
            profile.write_text('# my shell\n')
            configure_user(home, home / 'sdk', home / 'jdk', home / 'tools')
            first = properties.read_text()
            self.assertIn('custom.setting=value', first)
            self.assertEqual(first.count('android.aapt2FromMavenOverride'), 1)
            self.assertIn('\\ ', first)
            snapshot = profile.read_text()
            configure_user(home, home / 'sdk', home / 'jdk', home / 'tools')
            self.assertEqual(properties.read_text(), first)
            self.assertEqual(profile.read_text(), snapshot)
            self.assertIn('# my shell', snapshot)
            self.assertTrue(list(properties.parent.glob('gradle.properties.bak-*')))
            self.assertFalse((home / 'local.properties').exists())

    def test_release_selection_requires_checksum(self):
        name = 'droidforge-linux-arm64.tar.xz'
        asset = {'name': name, 'browser_download_url': 'https://github.com/test/' + name,
                 'digest': 'sha256:' + 'a' * 64}
        selected, checksum = select_release_asset({'assets': [asset]}, 'arm64')
        self.assertEqual(selected, asset)
        self.assertEqual(checksum, 'a' * 64)
        with self.assertRaises(ForgeError):
            select_release_asset({'assets': []}, 'arm64')
        with self.assertRaises(ForgeError):
            select_release_asset({'assets': [{**asset, 'digest': None}]}, 'arm64')

    def test_defaults_and_sdk_package_validation(self):
        args = parse_args([])
        self.assertEqual(args.release, 'droidforge-linux-arm64-r01')
        self.assertEqual(sdk_packages(['android-37.0'], ['36.0.0']),
                         ['platforms;android-37.0', 'build-tools;36.0.0'])
        for platforms, versions in ((['../x'], ['36.0.0']), (['android-37.0'], ['../x'])):
            with self.assertRaises(ForgeError):
                sdk_packages(platforms, versions)

    def test_failed_release_preflight_does_not_modify_sdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = self.archive(root)
            sdk = root / 'sdk'
            with patch('droidforge.android_setup.runtime_packages'), \
                 patch('droidforge.android_setup.verify', side_effect=ForgeError('glibc incompatible')), \
                 patch('droidforge.android_setup.install_sdk') as install:
                result = main(['--archive', str(archive), '--prefix', str(root / 'prefix'), '--sdk', str(sdk)])
            self.assertEqual(result, 1)
            self.assertFalse(sdk.exists())
            install.assert_not_called()

    def test_existing_java_setting_is_preserved_unless_explicitly_replaced(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            properties = home / '.gradle/gradle.properties'
            properties.parent.mkdir()
            properties.write_text('org.gradle.java.home=/existing/jdk17\n')
            configure_user(home, home / 'sdk', home / 'jdk21', home / 'tools')
            self.assertIn('org.gradle.java.home=/existing/jdk17', properties.read_text())
            configure_user(home, home / 'sdk', home / 'jdk21', home / 'tools', replace_java=True)
            self.assertNotIn('/existing/jdk17', properties.read_text())

    def test_entrypoint_preserves_callers_relative_paths(self):
        script = Path(__file__).resolve().parents[1] / 'setup-android.sh'
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(['sh', str(script), '--archive', './missing.tar.xz',
                                     '--prefix', './prefix'], cwd=directory, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Archive does not exist', result.stderr)
            self.assertFalse((Path(directory) / 'prefix').exists())
            # A real relative archive must be seen before it can fail its checksum.
            (Path(directory) / 'tools.tar.xz').write_bytes(b'not an archive')
            result = subprocess.run(['sh', str(script), '--archive', './tools.tar.xz',
                                     '--sha256', '0' * 64, '--prefix', './prefix'],
                                    cwd=directory, capture_output=True, text=True)
            self.assertIn('Local archive SHA-256 mismatch', result.stderr)
            self.assertTrue((Path(directory) / 'prefix').exists())

    def test_command_tools_zip_install_and_reuse(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sdk = root / 'sdk'
            def fake_download(url, path, sha):
                with zipfile.ZipFile(path, 'w') as archive:
                    archive.writestr('cmdline-tools/bin/sdkmanager', '#!/bin/sh\nexit 0\n')
                    archive.writestr('cmdline-tools/lib/test.jar', 'fixture')
            with patch('droidforge.android_setup.download', side_effect=fake_download) as download:
                manager = command_tools(sdk, root)
                self.assertTrue(manager.stat().st_mode & 0o111)
                self.assertEqual(command_tools(sdk, root), manager)
                self.assertEqual(download.call_count, 1)

    def test_java_version_selection_reuses_matching_jdk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jdk = root / 'custom jdk17'
            (jdk / 'bin').mkdir(parents=True)
            compiler = jdk / 'bin/javac'
            compiler.write_text('#!/bin/sh\nprintf "javac 17.0.1\\n"\n')
            compiler.chmod(0o755)
            with patch('droidforge.android_setup.download') as download:
                self.assertEqual(java_home(root, root, 'arm64', 17, str(jdk)), jdk)
                download.assert_not_called()
