import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest


class ProcessTests(unittest.TestCase):
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
