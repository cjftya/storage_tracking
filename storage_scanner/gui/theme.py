"""Application palettes and background OS appearance detection (no Tk worker calls)."""
import os
import queue
import subprocess
from threading import Event, Thread
from tkinter import ttk

PALETTES = {
    "day": dict(bg="#f3f3f3", card="#ffffff", stripe="#fafafa", input="#f7f7f7",
                text="#202020", muted="#666666", border="#dedede", accent="#426b60", hover="#33564d", selected="#e1ece7"),
    "night": dict(bg="#181818", card="#222222", stripe="#262626", input="#1c1c1c",
                  text="#eeeeee", muted="#aaaaaa", border="#393939", accent="#527e70", hover="#426b60", selected="#33483f"),
}


def apply_window_theme(root, mode, palette):
    """Best effort native caption styling; older Windows may reject color attributes.

    https://learn.microsoft.com/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute
    """
    if os.name != "nt":
        return
    import ctypes
    from ctypes import wintypes
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        dwm = ctypes.WinDLL("dwmapi")
        ancestor = user32.GetAncestor
        ancestor.argtypes = (wintypes.HWND, wintypes.UINT)
        ancestor.restype = wintypes.HWND
        hwnd = ancestor(root.winfo_id(), 2)  # GA_ROOT: Tk's decorated wrapper.
        setter = dwm.DwmSetWindowAttribute
        setter.argtypes = (wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD)
        setter.restype = ctypes.c_long
        dark = wintypes.BOOL(mode == "night")
        results = [setter(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))]
        for attribute, color in ((34, palette["border"]), (35, palette["bg"]), (36, palette["text"])):
            # COLORREF is 0x00BBGGRR.
            rgb = color.lstrip("#")
            value = wintypes.DWORD(int(rgb[0:2], 16) | int(rgb[2:4], 16) << 8 | int(rgb[4:6], 16) << 16)
            results.append(setter(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)))
        return tuple(results)
    except (OSError, AttributeError):
        pass


def system_appearance():
    """Return day on unsupported desktops or unavailable OS settings."""
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
                return "day" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "night"
        except OSError:
            return "day"
    try:
        result = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
                                capture_output=True, text=True, timeout=1, check=False)
        if result.returncode == 0 and "prefer-dark" in result.stdout:
            return "night"
        # Older GNOME and compatible desktops expose only the GTK theme.
        result = subprocess.run(["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                                capture_output=True, text=True, timeout=1, check=False)
        return "night" if result.returncode == 0 and "dark" in result.stdout.lower() else "day"
    except (OSError, subprocess.TimeoutExpired):
        return "day"


class ThemeManager:
    def __init__(self, root, on_change, mode="system"):
        if mode not in ("system", "day", "night"):
            raise ValueError("theme_mode must be system, day or night")
        self.root = root
        self.on_change = on_change
        self.mode = mode
        self.system_mode = "day"
        self.active = None
        self._updates = queue.Queue(maxsize=1)
        self._stop = Event()
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        root.bind("<Map>", self._window_mapped, add="+")
        self.select(mode)
        Thread(target=self._watch, name="system-appearance", daemon=True).start()
        # Also stop when a host destroys the root directly.
        root.bind("<Destroy>", lambda e: self.close() if e.widget is root else None, add="+")

    def _window_mapped(self, event):
        if event.widget is self.root and self.active:
            apply_window_theme(self.root, self.active, PALETTES[self.active])

    def _watch(self):
        while not self._stop.is_set():
            value = system_appearance()
            try:
                self._updates.put_nowait(value)
            except queue.Full:
                pass
            self._stop.wait(3)

    def poll(self):
        try:
            self.system_mode = self._updates.get_nowait()
        except queue.Empty:
            return
        if self.mode == "system":
            self._apply(self.system_mode)

    def select(self, mode):
        if mode not in ("system", "day", "night"):
            raise ValueError("Unknown appearance mode")
        self.mode = mode
        self._apply(self.system_mode if mode == "system" else mode)

    def _apply(self, mode):
        if mode == self.active:
            return
        self.active = mode
        p = PALETTES[mode]
        s = self.style
        self.root.configure(background=p["bg"])
        s.configure(".", font=("Segoe UI", 10), background=p["bg"], foreground=p["text"])
        s.configure("TFrame", background=p["bg"])
        s.configure("Card.TFrame", background=p["card"])
        for name, bg, fg, font in (
            ("TLabel", p["bg"], p["text"], ("Segoe UI", 10)),
            ("Title.TLabel", p["bg"], p["text"], ("Segoe UI", 16, "bold")),
            ("Section.TLabel", p["bg"], p["text"], ("Segoe UI", 12, "bold")),
            ("Muted.TLabel", p["bg"], p["muted"], ("Segoe UI", 10)),
            ("Caption.TLabel", p["bg"], p["muted"], ("Segoe UI", 9, "bold")),
            ("Card.TLabel", p["card"], p["text"], ("Segoe UI", 10)),
            ("CardTitle.TLabel", p["card"], p["text"], ("Segoe UI", 11, "bold")),
            ("CardCaption.TLabel", p["card"], p["muted"], ("Segoe UI", 9, "bold")),
            ("CardMuted.TLabel", p["card"], p["muted"], ("Segoe UI", 9)),
        ):
            s.configure(name, background=bg, foreground=fg, font=font)
        s.configure("TButton", padding=(12, 9), background=p["card"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"])
        s.map("TButton", background=[("active", p["selected"])], foreground=[("disabled", p["muted"])])
        s.configure("Accent.TButton", background=p["accent"], foreground="#ffffff", font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("disabled", p["border"]), ("active", p["hover"])],
              foreground=[("disabled", p["muted"]), ("!disabled", "#ffffff")])
        for name in ("TEntry", "TCombobox"):
            s.configure(name, padding=8, fieldbackground=p["input"], foreground=p["text"],
                        insertcolor=p["text"], bordercolor=p["border"], arrowcolor=p["text"])
            s.map(name, fieldbackground=[("disabled", p["bg"]), ("readonly", p["input"])],
                  foreground=[("disabled", p["muted"]), ("readonly", p["text"])],
                  selectbackground=[("!disabled", p["accent"])], selectforeground=[("!disabled", "#ffffff")])
        self.root.option_add("*TCombobox*Listbox.background", p["card"])
        self.root.option_add("*TCombobox*Listbox.foreground", p["text"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        s.configure("TRadiobutton", padding=(4, 4), background=p["card"], foreground=p["text"])
        s.map("TRadiobutton", background=[("active", p["stripe"])], indicatorbackground=[("selected", p["accent"]), ("!selected", p["input"])])
        s.configure("TSeparator", background=p["border"])
        s.configure("TScrollbar", background=p["border"], troughcolor=p["card"], arrowcolor=p["muted"], borderwidth=0)
        s.map("TScrollbar", background=[("active", p["muted"])])
        s.configure("Results.Treeview", rowheight=32, background=p["card"], fieldbackground=p["card"],
                    foreground=p["text"], borderwidth=0)
        s.map("Results.Treeview", background=[("selected", p["selected"])], foreground=[("selected", p["text"])])
        s.configure("Results.Treeview.Heading", padding=(10, 12), background=p["input"],
                    foreground=p["muted"], font=("Segoe UI", 9, "bold"), relief="flat")
        s.map("Results.Treeview.Heading", background=[("active", p["border"])])
        apply_window_theme(self.root, mode, p)
        self.on_change(p)

    def close(self):
        self._stop.set()
