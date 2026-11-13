import unittest

from storage_scanner.core.filters import PathFilter, bytes_to_mib, matches_size, mib_to_bytes


class FilterTests(unittest.TestCase):
    def test_size_is_inclusive(self):
        self.assertFalse(matches_size(99, 100))
        self.assertTrue(matches_size(100, 100))
        self.assertTrue(matches_size(101, 100))

    def test_exact_byte_conversion(self):
        self.assertEqual(mib_to_bytes("0.5"), 524288)
        self.assertEqual(mib_to_bytes("0.00000095367431640625"), 1)
        for value in (0, 1, 100 * 1024 * 1024, 2**100 + 1):
            self.assertEqual(mib_to_bytes(bytes_to_mib(value)), value)
        for invalid in ("-1", "NaN", "Infinity", "x", "0.0000001"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                mib_to_bytes(invalid)

    def test_windows_case_and_boundary(self):
        excluded = PathFilter((r"C:\Windows",), windows=True)
        self.assertTrue(excluded.excludes(r"c:\WINDOWS\System32"))
        self.assertTrue(excluded.excludes("C:/Windows/"))
        self.assertFalse(excluded.excludes(r"C:\WindowsBackup"))
        self.assertFalse(excluded.excludes(r"D:\Windows"))

    def test_linux_boundary_and_case(self):
        excluded = PathFilter(("/usr", "/home/a/specific.dat"), windows=False)
        self.assertTrue(excluded.excludes("/usr/lib"))
        self.assertFalse(excluded.excludes("/usr2"))
        self.assertFalse(excluded.excludes("/USR/lib"))
        self.assertTrue(excluded.excludes("/home/a/specific.dat"))
        self.assertFalse(excluded.excludes("/home/a/specific.dat.bak"))
