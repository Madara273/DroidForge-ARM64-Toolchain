import os
import selectors
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class ProcessTests(unittest.TestCase):
    def test_logged_output_is_visible_before_process_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            release, log = root / 'release', root / 'build.log'
            child = ('import sys,time; from pathlib import Path; '
                     'print("[1/2] compiling", flush=True); '
                     f'release=Path({str(release)!r}); '
                     '\nwhile not release.exists(): time.sleep(0.02)\n'
                     'print("[2/2] linking", file=sys.stderr, flush=True)')
            worker = ('from pathlib import Path; from droidforge.core import run; '
                      f'run([{sys.executable!r}, "-c", {child!r}], log=Path({str(log)!r}))')
            process = subprocess.Popen([sys.executable, '-c', worker], stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    output = b''
                    deadline = time.monotonic() + 3
                    while b'\n[1/2] compiling\n' not in output and time.monotonic() < deadline:
                        if selector.select(timeout=0.1):
                            output += os.read(process.stdout.fileno(), 65536)
                    self.assertIn(b'\n[1/2] compiling\n', output)
                    self.assertIsNone(process.poll())
                    self.assertIn('[1/2] compiling', log.read_text())
                release.touch()
                remaining, _ = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0)
                self.assertIn(b'[2/2] linking', remaining)
                self.assertIn('[2/2] linking', log.read_text())
            finally:
                release.touch()
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def test_logged_failure_remains_an_error(self):
        from droidforge.core import run, ForgeError
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'failure.log'
            with self.assertRaises(ForgeError):
                run([sys.executable, '-c', 'print("compile failed"); raise SystemExit(7)'], log=log)
            self.assertIn('compile failed', log.read_text())


    def test_interrupt_terminates_subprocess_descendants(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready, marker = root / 'ready', root / 'unexpected'
            grandchild = f'import time; from pathlib import Path; time.sleep(1.5); Path({str(marker)!r}).touch()'
            child = (f'import subprocess,sys,time; from pathlib import Path; '
                     f'subprocess.Popen([sys.executable,"-c",{grandchild!r}]); '
                     f'Path({str(ready)!r}).touch(); time.sleep(30)')
            worker = f'from droidforge.core import run; run([{sys.executable!r},"-c",{child!r}], capture=True)'
            process = subprocess.Popen([sys.executable, '-c', worker], stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic() + 10
                while not ready.exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(ready.exists())
                process.send_signal(signal.SIGINT)
                process.wait(timeout=5)
                time.sleep(1.7)
                self.assertFalse(marker.exists(), 'interrupted forge left an active descendant')
            finally:
                if process.poll() is None:
                    process.kill(); process.wait()
