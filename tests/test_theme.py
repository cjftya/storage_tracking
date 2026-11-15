"""Appearance detection and live palette integration checks."""
import os
import tkinter as tk
import unittest
from unittest.mock import patch
from storage_scanner.gui.theme import ThemeManager, PALETTES, system_appearance
from storage_scanner.gui.app import ScannerApp


class ThemeTests(unittest.TestCase):
    def test_system_detection_falls_back_when_unavailable(self):
        if os.name == "nt":
            with patch("winreg.OpenKey", side_effect=OSError):
                self.assertEqual(system_appearance(), "day")
        else:
            with patch("subprocess.run", side_effect=FileNotFoundError):
                self.assertEqual(system_appearance(), "day")

    def test_live_switch_preserves_tree_and_scan_controller(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(str(exc))
        root.withdraw()
        settings = dict(min_size_bytes=0, roots=None, extra_exclusions=(),
                        include_removable=False, use_default_exclusions=True)
        with patch("storage_scanner.gui.theme.system_appearance", return_value="night"):
            app = ScannerApp(root, settings, notify_on_completion=False)
            try:
                manager = app.theme_manager
                original_tree = app.results.tree
                original_controller = app.controller
                app.results.tree.insert("", "end", iid="retained", text="Keep this row", open=True)
                app.results.tree.selection_set("retained")
                for mode in ("night", "day", "night"):
                    manager.select(mode)
                    root.update_idletasks()
                    self.assertIs(app.results.tree, original_tree)
                    self.assertIs(app.controller, original_controller)
                    self.assertEqual(app.results.tree.selection(), ("retained",))
                    self.assertEqual(app.excludes_text.cget("background"), PALETTES[mode]["input"])
                    app.results.reset(None)
                    original_tree = app.results.tree
                    app.results.tree.insert("", "end", iid="retained", text="Keep this row", open=True)
                    app.results.tree.selection_set("retained")
                    self.assertEqual(str(app.results.tree.tag_configure("row_light", "background")), PALETTES[mode]["card"])
                manager.select("day")
                manager.system_mode = "night"
                manager.select("system")
                self.assertEqual(manager.active, "night")
                manager.select("day")
                manager._updates.get_nowait() if not manager._updates.empty() else None
                manager._updates.put_nowait("night")
                manager.poll()
                self.assertEqual(manager.active, "day")
                manager.select("system")
                self.assertEqual(manager.active, "night")
            finally:
                app.close()

    @unittest.skipUnless(os.name == "nt", "Windows native caption")
    def test_windows_caption_color_matches_palette(self):
        import ctypes
        from ctypes import wintypes
        import sys
        if sys.getwindowsversion().build < 22000:
            self.skipTest("Custom caption colors require Windows 11")
        from storage_scanner.gui.theme import apply_window_theme
        root = tk.Tk()
        try:
            root.update_idletasks()
            user = ctypes.WinDLL("user32")
            user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
            user.GetAncestor.restype = wintypes.HWND
            hwnd = user.GetAncestor(root.winfo_id(), 2)
            getter = ctypes.WinDLL("dwmapi").DwmGetWindowAttribute
            getter.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
            getter.restype = ctypes.c_long
            for mode in ("night", "day"):
                self.assertEqual(apply_window_theme(root, mode, PALETTES[mode]), (0, 0, 0, 0))
                dark = wintypes.BOOL()
                self.assertEqual(getter(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark)), 0)
                self.assertEqual(bool(dark.value), mode == "night")
        finally:
            root.destroy()

    def test_invalid_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            ThemeManager(None, lambda p: None, "invalid")
