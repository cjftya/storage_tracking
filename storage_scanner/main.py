"""Launch with python -m storage_scanner (or python -m storage_scanner.main)."""
from storage_scanner.options import argument_parser, load_settings


def main(argv=None):
    parser = argument_parser("Metadata-only local storage scanner")
    args = parser.parse_args(argv)
    try:
        settings = load_settings(args)
        import tkinter as tk
        from storage_scanner.gui.app import ScannerApp
        root = tk.Tk()
        try:
            ScannerApp(root, settings)
        except Exception:
            root.destroy()
            raise
        root.mainloop()
    except (ValueError, OSError, RuntimeError) as exc:
        parser.exit(2, f"Storage Scanner: {exc}\n")


if __name__ == "__main__":
    main()
