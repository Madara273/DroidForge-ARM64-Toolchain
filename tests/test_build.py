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
