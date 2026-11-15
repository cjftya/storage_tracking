"""Root failures never become successful zero-byte scans in either engine."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storage_scanner.core.models import ScanConfig
from storage_scanner.core.scanner import scan as file_scan
from storage_scanner.folder.models import FolderConfig
from storage_scanner.folder.scanner import scan as folder_scan
from storage_scanner.gui.controller import ScanController
from storage_scanner.folder.controller import FolderController


class RootValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def engines(self):
        return ((ScanConfig, file_scan), (FolderConfig, folder_scan))

    def assert_rejected(self, root, reason, **settings):
        for config, scan in self.engines():
            with self.subTest(engine=config.__name__):
                batches = []
                with self.assertRaisesRegex(ValueError, reason):
                    scan(config((str(root),), **settings), batches.extend)
                self.assertEqual(batches, [])

    def test_normal_directory_and_empty_directory_succeed(self):
        for config, scan in self.engines():
            result = scan(config((str(self.root),)), lambda batch: None)
            self.assertEqual(result.directories_scanned, 1)
            self.assertEqual(result.errors, 0)

    def test_excluded_missing_and_no_roots_fail(self):
        self.assert_rejected(self.root, 'excluded', excluded_paths=(str(self.root),))
        self.assert_rejected(self.root / 'missing', 'Cannot access scan root')
        for config, scan in self.engines():
            with self.assertRaisesRegex(ValueError, 'No scan roots'):
                scan(config(()), lambda batch: None)

    def test_mixed_invalid_roots_fail_before_any_results(self):
        (self.root / 'file').write_bytes(b'x')
        for config, scan in self.engines():
            batches = []
            with self.assertRaisesRegex(ValueError, 'Cannot access scan root'):
                scan(config((str(self.root), str(self.root / 'missing'))), batches.extend)
            self.assertEqual(batches, [])

    def test_denied_root_is_not_a_child_error(self):
        with patch('storage_scanner.platform_fs.paths.os.scandir', side_effect=PermissionError('denied')):
            self.assert_rejected(self.root, 'Cannot access scan root.*denied')

    def test_symlink_root_fails(self):
        link = self.root / 'link'
        try:
            link.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest('Symlink creation unavailable')
        self.assert_rejected(link, 'Unsupported symbolic link')

    @unittest.skipUnless(os.name == 'nt', 'Windows junction scenario')
    def test_windows_junction_root_fails(self):
        link = self.root / 'junction'
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.root)], capture_output=True)
        if result.returncode:
            self.skipTest('Junction creation unavailable')
        self.assert_rejected(link, 'Unsupported symbolic link')

    def test_controller_reports_failed_root_without_summary(self):
        import time
        for cls, config in ((ScanController, ScanConfig), (FolderController, FolderConfig)):
            controller = cls()
            self.addCleanup(controller.close)
            controller.start(config((str(self.root / 'missing'),)))
            deadline = time.monotonic() + 3
            while not controller.snapshot().finished and time.monotonic() < deadline:
                time.sleep(.002)
            result = controller.snapshot()
            self.assertTrue(result.finished)
            self.assertIn('Cannot access scan root', result.error)
            self.assertIsNone(result.summary)
