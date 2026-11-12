import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class BuildAllTests(unittest.TestCase):
    def invoke(self, arguments=(), *, uid='0', sudo_user='', fail=''):
        with tempfile.TemporaryDirectory(prefix='forge auto ') as directory:
            root = Path(directory)
            script = Path(__file__).resolve().parents[1] / 'build-all.sh'
            self.assertTrue(script.is_file(), 'automatic build entrypoint is missing')
            shutil.copy2(script, root / 'build-all.sh')
            binaries = root / 'bin'
            binaries.mkdir()
            (root / 'bootstrap.sh').write_text('#!/bin/sh\nprintf "bootstrap\\n" >> "$FLOW_LOG"\n')
            (root / 'bootstrap.sh').chmod(0o755)
            (root / 'forge').write_text('''#!/bin/sh
printf 'forge:%s:user=%s\n' "$*" "${RUN_AS:-direct}" >> "$FLOW_LOG"
[ "$1" != "$FAIL_STAGE" ] || exit 23
if [ "$1" = package ]; then touch "$RESULT_MARKER"; fi
''')
            (root / 'forge').chmod(0o755)
            helpers = {
                'id': '#!/bin/sh\nprintf "%s\\n" "$FAKE_UID"\n',
                'sudo': '#!/bin/sh\n[ "$1" != -- ] || shift\nexec "$@"\n',
                'runuser': '#!/bin/sh\nexport RUN_AS="$2"\nshift 3\nexec "$@"\n',
            }
            for name, text in helpers.items():
                path = binaries / name
                path.write_text(text)
                path.chmod(0o755)
            log = root / 'flow.log'
            result = root / 'archive.created'
            environment = dict(os.environ, PATH=str(binaries) + ':' + os.environ['PATH'],
                               FAKE_UID=uid, SUDO_USER=sudo_user, FAIL_STAGE=fail,
                               FLOW_LOG=str(log), RESULT_MARKER=str(result))
            completed = subprocess.run(['/bin/sh', root / 'build-all.sh', *arguments],
                                       env=environment, capture_output=True, text=True)
            return completed, log.read_text().splitlines() if log.exists() else [], result.exists()

    def test_one_command_bootstraps_builds_verifies_and_packages(self):
        completed, events, archive = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(events, ['bootstrap', 'forge:doctor:user=direct',
                                 'forge:build --jobs 1:user=direct',
                                 'forge:verify:user=direct', 'forge:package:user=direct'])
        self.assertTrue(archive)

    def test_build_failure_stops_before_verification_and_packaging(self):
        completed, events, archive = self.invoke(fail='build')
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse(archive)
        self.assertFalse(any('forge:verify' in event or 'forge:package' in event for event in events))

    def test_non_root_uses_sudo_only_for_bootstrap(self):
        completed, events, archive = self.invoke(('--jobs', '2'), uid='1000')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('forge:build --jobs 2:user=direct', events)
        self.assertTrue(archive)

    def test_sudo_invocation_builds_as_original_user(self):
        completed, events, archive = self.invoke(sudo_user='builder')
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn('forge:build --jobs 1:user=builder', events)
        self.assertTrue(archive)

    def test_invalid_jobs_and_help_do_not_install_anything(self):
        for arguments in (('--jobs', '0'), ('--jobs', '999'), ('--jobs', 'bad'), ('--jobs',)):
            with self.subTest(arguments=arguments):
                completed, events, archive = self.invoke(arguments)
                self.assertNotEqual(completed.returncode, 0)
                self.assertEqual(events, [])
                self.assertFalse(archive)
        completed, events, archive = self.invoke(('--help',))
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(events, [])
