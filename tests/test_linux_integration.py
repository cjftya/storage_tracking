"""Native POSIX checks on owned fixtures; no privileged mounts or drive scans."""
import os
from pathlib import Path
import sys
import tempfile
import unittest

from storage_scanner.folder.models import FolderConfig
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.folder.scanner import scan
from storage_scanner.platform_fs.volumes import discover_volumes, validate_roots


@unittest.skipUnless(sys.platform.startswith("linux"), "Requires native Linux filesystem semantics")
class LinuxIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def collect(self):
        config = FolderConfig((str(self.root),))
        store = FolderResultStore(config.roots)
        summary = scan(config, store.add_batch)
        store.finish(complete=not summary.cancelled and not summary.errors)
        return summary, store

    def test_symlink_cycle_and_file_link_are_not_followed(self):
        sub = self.root / "sub"
        sub.mkdir()
        file = sub / "file"
        file.write_bytes(b"123")
        (sub / "loop").symlink_to(self.root, target_is_directory=True)
        (self.root / "file-link").symlink_to(file)
        summary, store = self.collect()
        self.assertEqual(summary.errors, 0)
        self.assertEqual(summary.files_scanned, 1)
        self.assertEqual(summary.directories_scanned, 2)
        self.assertEqual(store.node(str(self.root)).size_bytes, 3)

    def test_case_sensitive_names_remain_distinct(self):
        for name, size in (("Data", 1), ("data", 2)):
            folder = self.root / name
            folder.mkdir()
            (folder / "file").write_bytes(b"x" * size)
        summary, store = self.collect()
        self.assertEqual(summary.bytes_scanned, 3)
        self.assertEqual(store.node(str(self.root / "Data")).size_bytes, 1)
        self.assertEqual(store.node(str(self.root / "data")).size_bytes, 2)

    def test_denied_directory_reports_partial_results(self):
        if os.geteuid() == 0:
            self.skipTest("Root bypasses ordinary directory permission checks")
        blocked = self.root / "blocked"
        blocked.mkdir()
        (blocked / "file").write_bytes(b"secret")
        (self.root / "readable").write_bytes(b"ok")
        try:
            blocked.chmod(0)
            summary, store = self.collect()
        finally:
            blocked.chmod(0o700)
        self.assertEqual(summary.errors, 1)
        self.assertEqual(summary.bytes_scanned, 2)
        self.assertEqual(store.state, "partial")

    def test_native_mount_discovery_accepts_owned_fixture(self):
        discovery = discover_volumes(include_removable=True)
        self.assertTrue(discovery.roots)
        self.assertIn("/proc", discovery.mandatory_exclusions)
        validate_roots(discovery, (str(self.root),))
