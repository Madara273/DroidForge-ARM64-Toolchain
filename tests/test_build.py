from pathlib import Path
import tempfile
import unittest

from droidforge.core import ForgeError
from droidforge.build import selected_targets, jobs_count


class BuildTests(unittest.TestCase):
    def test_rejects_unknown_or_empty_targets(self):
        for targets in ([], ['adb', 'flash-everything'], ['adb', 'adb']):
            with self.assertRaises(ForgeError): selected_targets(targets)
        self.assertEqual(selected_targets(['adb', 'fastboot']), ['adb', 'fastboot'])

    def test_conservative_parallelism(self):
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
            shutil.copy2(project / 'toolchain.toml', root / 'toolchain.toml')
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
