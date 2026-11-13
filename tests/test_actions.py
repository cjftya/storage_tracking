import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from storage_scanner.platform_fs.actions import open_folder


class FolderActionTests(unittest.TestCase):
    def test_missing_folder_fails_without_launching_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(NotADirectoryError):
                open_folder(str(Path(directory) / "missing"))

    @unittest.skipUnless(os.name == "nt", "Windows shell opener")
    def test_windows_uses_native_folder_open(self):
        with tempfile.TemporaryDirectory() as directory, patch("os.startfile") as start:
            open_folder(directory)
            start.assert_called_once_with(os.path.abspath(directory), "open")

    def test_linux_uses_argument_list_and_propagates_launch_failure(self):
        path = os.path.abspath("folder with spaces & symbols")
        with patch("storage_scanner.platform_fs.actions.os.path.isdir", return_value=True), patch(
                "storage_scanner.platform_fs.actions.os.name", "posix"), patch(
                "storage_scanner.platform_fs.actions.sys.platform", "linux"), patch(
                "storage_scanner.platform_fs.actions.subprocess.Popen") as launch:
            launch.return_value.wait.return_value = 0
            open_folder(path)
            self.assertEqual(launch.call_args.args[0], ["xdg-open", path])
            self.assertNotIn("shell", launch.call_args.kwargs)
            launch.return_value.wait.return_value = 1
            with self.assertRaises(subprocess.CalledProcessError):
                open_folder(path)
            launch.return_value.wait.side_effect = subprocess.TimeoutExpired(["xdg-open", path], 0.5)
            open_folder(path)
            launch.return_value.kill.assert_not_called()
            launch.side_effect = FileNotFoundError("xdg-open unavailable")
            with self.assertRaises(FileNotFoundError):
                open_folder(path)
