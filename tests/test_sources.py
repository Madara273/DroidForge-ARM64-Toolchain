import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from droidforge.sources import validate_entry, checkout
from droidforge.core import ForgeError, atomic_json


class SourceTests(unittest.TestCase):
    def test_rejects_traversal_and_non_https_sources(self):
        for path in ('../outside', '/tmp/outside', 'src/../outside', 'src'):
            with self.subTest(path=path), self.assertRaises(ForgeError):
                validate_entry({'path': path, 'url': 'https://example.org/source'})
        with self.assertRaises(ForgeError):
            validate_entry({'path': 'src/base', 'url': 'http://example.org/source'})

    def test_atomic_json_preserves_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lock.json'
            atomic_json(path, {'name': 'Джерела'})
            self.assertEqual(json.loads(path.read_text()), {'name': 'Джерела'})

    def test_checkout_resume_rejects_wrong_commit_and_partial_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote = root / 'remote'
            remote.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(remote), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            (remote / 'hello').write_text('first')
            git('add', '.')
            git('commit', '-qm', 'first')
            first = git('rev-parse', 'HEAD')
            destination = root / 'checkout'
            checkout(str(remote), first, destination)
            checkout(str(remote), first, destination)
            (remote / 'hello').write_text('second')
            git('commit', '-qam', 'second')
            second = git('rev-parse', 'HEAD')
            with self.assertRaises(ForgeError):
                checkout(str(remote), second, destination)
            partial = root / 'partial'
            partial.mkdir()
            with self.assertRaises(ForgeError):
                checkout(str(remote), first, partial)

    def test_selected_manifest_excludes_unbuilt_tools(self):
        from droidforge.sources import selected_manifest
        entries = [{'path': 'src/' + name, 'url': 'https://example.org/' + name}
                   for name in ('base', 'adb', 'art', 'dalvik', 'development', 'f2fs-tools', 'sqlite')]
        self.assertEqual([item['path'] for item in selected_manifest(entries)], ['src/base', 'src/adb'])

    def test_budget_stops_download_and_terminates_process(self):
        from droidforge.sources import guarded_run
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ForgeError):
                guarded_run([sys.executable, '-c',
                             'from pathlib import Path; import time; Path("payload").write_bytes(b"x"*8192); time.sleep(10)'],
                            cwd=root, budget=(root, 4096))
