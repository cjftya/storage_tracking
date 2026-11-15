"""Check path export coverage, completion labels and literal path handling."""
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from storage_scanner.report import write_report
from storage_scanner.core.models import ScanConfig, ScanSummary, FileMatch
from storage_scanner.core.results import MemoryResultStore
from storage_scanner.folder.models import FolderConfig, FolderSummary, DirectoryDelta
from storage_scanner.folder.results import FolderResultStore
from storage_scanner.platform_fs.paths import path_key


class ReportTests(unittest.TestCase):
    def export(self, directory, mode, config, store, summary, error=None):
        snapshot = SimpleNamespace(finished=True, summary=summary, progress=summary,
                                   error=error, store=store, run_id=1, elapsed_seconds=1.0)
        return write_report(directory, mode=mode, config=config, snapshot=snapshot,
                            started_at=datetime.now().astimezone())

    def test_folder_report_has_unloaded_paths_without_size_tables(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = path_key(str(Path(directory) / 'source'))
            store = FolderResultStore((root,))
            child = str(Path(root) / 'child')
            store.add_batch((DirectoryDelta(root, 7), DirectoryDelta(child, 13)))
            store.finish(complete=True)
            path = self.export(directory, 'folder', FolderConfig((root,)), store,
                               FolderSummary(2, 2, 20, 0, 1, False))
            report = path.read_text(encoding='utf-8')
            self.assertIn('Status: complete', report)
            self.assertIn('    ' + child + '\n', report)
            self.assertNotIn('Largest entries', report)
            self.assertNotIn('|', report)
            self.assertIn('Stored paths: 2', report)
            self.assertFalse(list(Path(directory).glob('*.tmp')))

    def test_file_report_covers_more_than_a_page_and_partial_status(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = path_key(str(Path(directory) / 'source'))
            store = MemoryResultStore((root,))
            matches = tuple(FileMatch(str(Path(root) / f'file-{i}.bin'), 100+i) for i in range(420))
            store.add_batch(matches)
            path = self.export(directory, 'file', ScanConfig((root,), 100), store,
                               ScanSummary(420, 1, 420, 1, 1, False))
            report = path.read_text(encoding='utf-8')
            self.assertIn('Status: partial', report)
            self.assertIn('file-419.bin', report)
            self.assertIn('Stored paths: 421', report)
            cancelled = self.export(directory, 'file', ScanConfig((root,), 100), store,
                                    ScanSummary(420, 1, 420, 0, 1, True))
            self.assertNotEqual(path, cancelled)
            self.assertIn('Status: cancelled', cancelled.read_text(encoding='utf-8'))

    def test_paths_preserve_punctuation_as_literal_code(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            root = path_key(str(Path(directory) / 'source'))
            store = MemoryResultStore((root,))
            store.add_batch((FileMatch(str(Path(root) / 'a|`[x]<tag>\n.md'), 10),))
            path = self.export(directory, 'file', ScanConfig((root,), 0), store,
                               ScanSummary(1, 1, 1, 0, 1, False), error='collector failure')
            text = path.read_text(encoding='utf-8')
            self.assertIn('Status: failed', text)
            self.assertIn('a|`[x]<tag>\\n.md', text)
            self.assertNotIn('Largest entries', text)
