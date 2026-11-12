import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class DockerBuildTests(unittest.TestCase):
    def invoke(self, args=(), fail=''):
        with tempfile.TemporaryDirectory(prefix='forge docker ') as directory:
            root = Path(directory)
            shutil.copy2(Path(__file__).resolve().parents[1] / 'docker-build-arm64.sh', root)
            shutil.copytree(Path(__file__).resolve().parents[1] / 'docker', root / 'docker')
            binary = root / 'bin'
            binary.mkdir()
            (binary / 'nproc').write_text('#!/bin/sh\nprintf \"8\\n\"\n')
            (binary / 'nproc').chmod(0o755)
            docker = binary / 'docker'
            docker.write_text('''#!/bin/sh
printf '%s\\n' "$*" >> "$DOCKER_LOG"
[ "$1" != "$FAIL_COMMAND" ] || exit 23
if [ "$1" = buildx ] && [ "$2" = build ]; then
    while [ "$#" -gt 0 ]; do
        if [ "$1" = -f ]; then shift; [ -f "$1" ] || exit 31; fi
        shift
    done
fi
''')
            docker.chmod(0o755)
            log = root / 'calls'
            env = dict(os.environ, PATH=str(binary) + ':' + os.environ['PATH'],
                       DOCKER_LOG=str(log), FAIL_COMMAND=fail)
            result = subprocess.run(['sh', root / 'docker-build-arm64.sh', *args],
                                    env=env, capture_output=True, text=True)
            return result, log.read_text().splitlines() if log.exists() else []

    def test_arm64_build_and_run_use_separate_cache(self):
        result, calls = self.invoke(('--jobs', '2'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--platform linux/arm64 --load', calls[2])
        self.assertIn('/docker/Dockerfile', calls[2])
        self.assertTrue(calls[2].endswith('/docker'))
        self.assertIn('--platform linux/arm64', calls[3])
        self.assertIn('.docker-cache/arm64:/workspace/.forge', calls[3])
        self.assertIn('--arch arm64 --jobs', '\n'.join(calls[3:]))
        self.assertIn('sh 2', '\n'.join(calls[3:]))

    def test_default_uses_available_processors(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('sh 8', '\n'.join(calls))

    def test_failure_stops_before_container_run(self):
        result, calls = self.invoke(fail='buildx')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call.startswith('run ') for call in calls))

    def test_invalid_arguments_and_help_skip_docker(self):
        for args in (('--jobs', '0'), ('--jobs', 'bad'), ('--jobs', '65'), ('--jobs',)):
            result, calls = self.invoke(args)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(calls, [])
        result, calls = self.invoke(('--help',))
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls, [])
