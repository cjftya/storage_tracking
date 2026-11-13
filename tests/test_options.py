import json
from pathlib import Path
import tempfile
import unittest

from storage_scanner.options import argument_parser, load_settings


class OptionTests(unittest.TestCase):
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
                         {"include_removable": "false"}, []):
                config.write_text(json.dumps(data), encoding="utf-8")
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_settings(argument_parser("test").parse_args(["--config", str(config)]))
