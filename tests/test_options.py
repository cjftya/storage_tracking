import json
from pathlib import Path
import tempfile
import unittest

from storage_scanner.options import argument_parser, load_settings


class OptionTests(unittest.TestCase):
    def test_default_and_custom_gui_period_keep_small_minimum(self):
        settings = load_settings(argument_parser("test").parse_args([]))
        self.assertEqual(settings["update_ms"], 1000)
        self.assertEqual(settings["scan_mode"], "folder")
        self.assertEqual(settings["min_size_bytes"], 100 * 1024 ** 2)
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"gui_update_ms": 700, "min_size_mib": "0.5"}))
            settings = load_settings(argument_parser("test").parse_args(["--config", str(config)]))
            self.assertEqual(settings["update_ms"], 700)
            self.assertEqual(settings["min_size_bytes"], 524288)

    def test_config_and_cli_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"min_size_mib": "50", "roots": [directory],
                                          "excluded_paths": [directory + "/cache"],
                                          "include_removable": True}), encoding="utf-8")
            args = argument_parser("test").parse_args(["--config", str(config), "--min-size-mib", "0.5",
                                                       "--exclude", directory + "/extra"])
            settings = load_settings(args)
            self.assertEqual(settings["min_size_bytes"], 524288)
            self.assertEqual(len(settings["extra_exclusions"]), 2)
            self.assertTrue(settings["include_removable"])

    def test_invalid_config_types_and_empty_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            for data in ({"roots": []}, {"roots": "path"}, {"excluded_paths": [2]},
                         {"include_removable": "false"}, {"scan_mode": "other"}, []):
                config.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_settings(argument_parser("test").parse_args(["--config", str(config)]))

    def test_mode_cli_overrides_config_and_legacy_defaults_to_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            for data, expected in (({}, "folder"), ({"scan_mode": "file"}, "file")):
                config.write_text(json.dumps(data), encoding="utf-8")
                args = ["--config", str(config)]
                self.assertEqual(load_settings(argument_parser("test").parse_args(args))["scan_mode"], expected)
                self.assertEqual(load_settings(argument_parser("test").parse_args(
                    [*args, "--mode", "folder"]))["scan_mode"], "folder")
