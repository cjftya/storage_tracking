import os
from pathlib import Path
import unittest

from storage_scanner.core.models import FileMatch
from storage_scanner.core.results import MemoryResultStore
from storage_scanner.platform_fs.paths import path_key


class ResultTests(unittest.TestCase):
    def setUp(self):
        self.root = path_key(str(Path.cwd() / "sample"))
        self.store = MemoryResultStore((self.root,))

    def match(self, name, size=100):
        return FileMatch(os.path.join(self.root, name), size)

    def test_only_matching_ancestors_and_deduplication(self):
        match = self.match(os.path.join("a", "b", "file"))
        self.store.add_batch((match, match))
        self.assertEqual(self.store.match_count, 1)
        self.assertEqual([n.path for n in self.store.children(None)], [self.root])
        self.assertEqual(self.store.child_count(self.root), 1)
        parent = os.path.dirname(match.path)
        node = self.store.children(parent)[0]
        self.assertFalse(node.is_directory)
        self.assertEqual(node.size_bytes, 100)
        self.assertEqual(len(self.store.take_changes()), 4)
        self.assertFalse(self.store.take_changes())

    def test_paged_large_results(self):
        self.store.add_batch(tuple(self.match(f"{i}.dat", i) for i in range(20000)))
        self.assertEqual(self.store.match_count, 20000)
        self.assertEqual(len(self.store.children(self.root, 500, 200)), 200)
        self.assertEqual(self.store.children(self.root, 500, 1)[0].size_bytes, 500)

    def test_reject_results_outside_roots(self):
        with self.assertRaises(ValueError):
            self.store.add_batch((FileMatch(str(Path.cwd() / "outside"), 1),))

    def test_single_file_root_is_a_leaf(self):
        self.store.add_batch((FileMatch(self.root, 1),))
        node = self.store.children(None)[0]
        self.assertFalse(node.is_directory)
        self.assertEqual(node.size_bytes, 1)
        self.assertEqual(self.store.match_count, 1)

    def test_nested_roots_and_separate_scans(self):
        nested = os.path.join(self.root, "nested")
        store = MemoryResultStore((self.root, nested))
        store.add_batch((FileMatch(os.path.join(nested, "f"), 1), self.match("a")))
        self.assertEqual(store.child_count(None), 2)
        self.assertEqual(self.store.match_count, 0)

    def test_folder_totals_include_all_matching_descendants_once(self):
        matches = tuple(self.match(os.path.join(name, "nested", "file"), 100)
                        for name in ("a", "b", "c"))
        self.store.add_batch(matches)
        self.store.add_batch(matches)  # A duplicate batch must not inflate totals.
        self.assertEqual(self.store.children(None)[0].size_bytes, 300)
        self.assertEqual([n.size_bytes for n in self.store.children(self.root)], [100, 100, 100])
        for name in ("a", "b", "c"):
            parent = os.path.join(self.root, name)
            self.assertEqual(self.store.children(parent)[0].size_bytes, 100)

    def test_totals_across_chunks_and_nested_roots(self):
        nested = os.path.join(self.root, "nested")
        store = MemoryResultStore((self.root, nested))
        store.add_batch(tuple(self.match(os.path.join("a", f"{i}.bin"), 100) for i in range(130)))
        store.add_batch((FileMatch(os.path.join(nested, "separate.bin"), 500),))
        totals = {node.path: node.size_bytes for node in store.children(None)}
        self.assertEqual(totals[self.root], 13000)
        self.assertEqual(totals[nested], 500)
