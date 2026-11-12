from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from droidforge.core import ForgeError
from droidforge.build import selected_targets, jobs_count


class BuildTests(unittest.TestCase):
    def test_build_configuration_is_grouped_with_its_source_lock(self):
        import json
        from droidforge.core import config
        project = Path(__file__).resolve().parents[1]
        settings = config(project)
        self.assertTrue((project / 'config/toolchain.toml').is_file())
        lock = json.loads((project / 'config/sources.lock.json').read_text())
        self.assertEqual(lock['recipe']['commit'], settings['recipe']['commit'])
        self.assertFalse((project / 'toolchain.toml').exists())
        self.assertFalse((project / 'sources.lock.json').exists())

    def test_rejects_unknown_or_empty_targets(self):
        for targets in ([], ['adb', 'flash-everything'], ['adb', 'adb']):
            with self.assertRaises(ForgeError): selected_targets(targets)
        self.assertEqual(selected_targets(['adb', 'fastboot']), ['adb', 'fastboot'])

    def test_automatic_parallelism(self):
        with patch('os.sched_getaffinity', return_value=set(range(8))):
            self.assertEqual(jobs_count(None, {'jobs': 'auto'}), 8)
            self.assertEqual(jobs_count(2, {'jobs': 'auto'}), 2)
        with patch('os.sched_getaffinity', return_value=set(range(128))):
            self.assertEqual(jobs_count(None, {'jobs': 'auto'}), 64)
        with patch('os.sched_getaffinity', side_effect=OSError), patch('os.cpu_count', return_value=4):
            self.assertEqual(jobs_count(None, {'jobs': 'auto'}), 4)

    def test_explicit_parallelism(self):
        self.assertEqual(jobs_count(None, {'jobs': 1}), 1)
        self.assertEqual(jobs_count(2, {'jobs': 1}), 2)
        for jobs in (0, -1, 100):
            with self.assertRaises(ForgeError): jobs_count(jobs, {'jobs': 1})

    def test_bootstrap_does_not_create_root_owned_build_cache(self):
        import os
        import shutil
        import subprocess
        import sys
        if os.geteuid() != 0:
            self.skipTest('bootstrap privilege boundary requires root')
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(project / 'droidforge', root / 'droidforge')
            shutil.copytree(project / 'config', root / 'config')
            shutil.copy2(project / 'forge', root / 'forge')
            fake_bin = root / 'bin'
            fake_bin.mkdir()
            apt = fake_bin / 'apt-get'
            apt.write_text('#!/bin/sh\nexit 0\n')
            apt.chmod(0o755)
            environment = dict(os.environ, PATH=str(fake_bin) + ':' + os.environ['PATH'])
            result = subprocess.run([sys.executable, root / 'forge', 'bootstrap'],
                                    env=environment, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((root / '.forge').exists(), 'sudo bootstrap must not own user build state')

    def test_source_rewrite_is_idempotent_when_replacement_contains_original(self):
        from droidforge.build import rewrite
        once = rewrite('include', 'include', 'include\nhelper', 'fixture')
        self.assertEqual(once, 'include\nhelper')
        self.assertEqual(rewrite(once, 'include', 'include\nhelper', 'fixture'), once)
        self.assertEqual(rewrite('prefix/Resource.proto', 'prefix/Resource.proto', 'Resource.proto', 'fixture'), 'Resource.proto')

    def test_shell_bootstrap_can_start_without_python_on_path(self):
        import os, shutil, subprocess, sys
        if os.geteuid() != 0:
            self.skipTest('package bootstrap requires root')
        project = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(project / 'droidforge', root / 'droidforge')
            shutil.copytree(project / 'config', root / 'config')
            for name in ('forge', 'bootstrap.sh'):
                shutil.copy2(project / name, root / name)
            fake = root / 'bin'
            fake.mkdir()
            for name in ('id', 'dirname'):
                (fake / name).symlink_to(shutil.which(name))
            apt = fake / 'apt-get'
            apt.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_APT_LOG"
case "$*" in
  *python3*) /bin/ln -sf "$REAL_PYTHON" "$FAKE_BIN/python3"
             /bin/ln -sf /usr/bin/git "$FAKE_BIN/git" ;;
esac
''')
            apt.chmod(0o755)
            environment = dict(os.environ, PATH=str(fake), FAKE_BIN=str(fake),
                               FAKE_APT_LOG=str(root / 'apt.log'), REAL_PYTHON=sys.executable)
            before = subprocess.run([root / 'forge', 'bootstrap'], env=environment,
                                    capture_output=True, text=True)
            self.assertNotEqual(before.returncode, 0, 'fixture must start without Python')
            after = subprocess.run(['/bin/sh', root / 'bootstrap.sh'], env=environment,
                                   capture_output=True, text=True)
            self.assertEqual(after.returncode, 0, after.stderr)
            self.assertIn('build-essential', (root / 'apt.log').read_text())
            self.assertFalse((root / '.forge').exists())
