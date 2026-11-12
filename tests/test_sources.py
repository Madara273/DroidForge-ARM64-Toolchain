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
                             'from pathlib import Path; import time; Path("payload").write_bytes(b"x"*32768); time.sleep(10)'],
                            cwd=root, budget=(root, 8192))
            self.assertTrue((root / 'payload').exists(), 'budget must stop an active transfer')

    def test_detects_edits_to_prepared_tracked_sources(self):
        from droidforge.sources import tracked_digest, verify_tracked
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            (repo / 'source.c').write_text('original')
            git('add', '.')
            git('commit', '-qm', 'initial')
            expected = tracked_digest(repo)
            verify_tracked(repo, expected)
            (repo / 'source.c').write_text('edited')
            with self.assertRaises(ForgeError): verify_tracked(repo, expected)

    def test_untracked_and_ignored_headers_change_fingerprint(self):
        from droidforge.sources import tracked_digest, verify_tracked
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            def git(*args):
                subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            (repo / '.gitignore').write_text('ignored.h\n')
            git('add', '.')
            git('commit', '-qm', 'initial')
            expected = tracked_digest(repo)
            for name in ('extra.h', 'ignored.h'):
                with self.subTest(name=name):
                    (repo / name).write_text('shadowed input')
                    with self.assertRaises(ForgeError): verify_tracked(repo, expected)
                    (repo / name).unlink()

    def test_initial_checkout_rejects_local_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'remote'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            (repo / 'source').write_text('original')
            git('add', '.')
            git('commit', '-qm', 'initial')
            commit = git('rev-parse', 'HEAD')
            destination = Path(directory) / 'checkout'
            checkout(str(repo), commit, destination)
            (destination / 'source').write_text('tampered')
            with self.assertRaises(ForgeError): checkout(str(repo), commit, destination)

    def test_cache_hit_checks_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory) / 'remote'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q')
            git('config', 'user.email', 'test@example.org')
            git('config', 'user.name', 'Test')
            (repo / 'source').write_text('data')
            git('add', '.')
            git('commit', '-qm', 'initial')
            commit = git('rev-parse', 'HEAD')
            with self.assertRaises(ForgeError):
                checkout(str(repo), commit, repo, budget=(Path(directory), 1))

    def test_cancel_event_stops_active_fetch_process(self):
        from droidforge.sources import guarded_run
        import sys, threading, time
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cancel = threading.Event()
            timer = threading.Timer(0.1, cancel.set)
            timer.start()
            started = time.monotonic()
            try:
                with self.assertRaises(ForgeError):
                    guarded_run([sys.executable, '-c', 'import time; time.sleep(10)'],
                                cwd=root, budget=(root, 1024**2), cancel=cancel)
            finally:
                timer.cancel()
            self.assertLess(time.monotonic() - started, 3)
