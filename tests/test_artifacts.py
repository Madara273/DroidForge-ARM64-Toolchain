import json
from pathlib import Path
import tempfile
import unittest

from droidforge.core import ForgeError, atomic_json
from droidforge.artifacts import elf_arm64, make_manifest, validate_manifest, install_sdk, package


def fixture(root):
    stage = root / 'out'
    stage.mkdir()
    binary = bytearray(64)
    binary[:6] = b'\x7fELF\x02\x01'
    binary[18:20] = (183).to_bytes(2, 'little')
    for name in ('aapt2', 'adb'):
        (stage / name).write_bytes(binary)
        (stage / name).chmod(0o755)
    (stage / 'NOTICES').write_text('Fixture attribution')
    make_manifest(stage, ('aapt2', 'adb'), {'sources': []})
    return stage


class ArtifactTests(unittest.TestCase):
    def test_rejects_wrong_arch_and_truncated_elf(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'binary'
            for data in (b'hello', b'\x7fELF\x02\x01' + bytes(58)):
                path.write_bytes(data)
                with self.assertRaises(ForgeError): elf_arm64(path)

    def test_manifest_detects_tampering(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = fixture(Path(directory))
            validate_manifest(stage)
            (stage / 'aapt2').write_bytes(b'tampered')
            with self.assertRaises(ForgeError): validate_manifest(stage)

    def test_install_backs_up_and_rejects_symlinks_and_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = fixture(root)
            sdk = root / 'sdk'
            tools = sdk / 'build-tools/36.0.0'
            tools.mkdir(parents=True)
            (tools / 'source.properties').write_text('Pkg.Revision=36.0.0')
            (tools / 'aapt2').write_bytes(b'original')
            install_sdk(stage, sdk, '36.0.0')
            self.assertTrue(any(p.read_bytes() == b'original' for p in (sdk / '.droidforge-backups').rglob('aapt2')))
            (tools / 'aapt2').unlink()
            outside = root / 'outside'
            outside.write_bytes(b'do not touch')
            (tools / 'aapt2').symlink_to(outside)
            with self.assertRaises(ForgeError): install_sdk(stage, sdk, '36.0.0')
            self.assertEqual(outside.read_bytes(), b'do not touch')
            with self.assertRaises(ForgeError): install_sdk(stage, sdk, '../../outside')

    def test_archive_only_contains_manifest_files(self):
        import tarfile
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stage = fixture(root)
            (stage / 'fastboot').write_text('stale binary')
            archive = package(stage, root / 'dist')
            with tarfile.open(archive) as tar:
                names = tar.getnames()
                self.assertNotIn('fastboot', names)
                self.assertIn('NOTICES', names)
                self.assertIn('manifest.json', names)
