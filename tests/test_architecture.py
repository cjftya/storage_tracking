import ast
from pathlib import Path
import unittest


class ArchitectureTests(unittest.TestCase):
    def test_engine_and_platform_have_no_gui_imports(self):
        base = Path(__file__).resolve().parents[1] / "storage_scanner"
        for folder in ("core", "folder", "platform_fs"):
            for path in (base / folder).glob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    modules = []
                    if isinstance(node, ast.Import):
                        modules = [alias.name for alias in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        modules = [node.module or ""]
                    for module in modules:
                        self.assertFalse(module.startswith(("tkinter", "storage_scanner.gui")), str(path))
