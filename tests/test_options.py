import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from storage_scanner.options import argument_parser, load_settings, configured_scope, configured_scan
from storage_scanner.platform_fs.volumes import Discovery, ScanScope, make_config


class OptionTests(unittest.TestCase):
    def test_folder_platform_defaults_and_config_overrides(self):
        from types import SimpleNamespace
        parser = argument_parser("test")
        args = parser.parse_args([])
        for name, workers, large in (("nt", 8, True), ("posix", 1, False)):
            with patch("storage_scanner.options.os", SimpleNamespace(name=name)):
                settings = load_settings(args)
            self.assertEqual((settings["folder_workers"], settings["large_fetch"]), (workers, large))
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps({"folder_workers": 8, "large_fetch": True}))
            with patch("storage_scanner.options.os", SimpleNamespace(name="posix")):
                settings = load_settings(parser.parse_args(["--config", str(config)]))
            self.assertEqual(settings["folder_workers"], 8)
            settings = load_settings(parser.parse_args(["--config", str(config),
                                                       "--folder-workers", "2", "--no-large-fetch"]))
            self.assertEqual((settings["folder_workers"], settings["large_fetch"]), (2, False))
            for data in ({"folder_workers": True}, {"folder_workers": 3},
                         {"folder_workers": 8.0}, {"large_fetch": "false"}):
                config.write_text(json.dumps(data))
                with self.subTest(data=data), self.assertRaises(ValueError):
                    load_settings(parser.parse_args(["--config", str(config)]))

    def test_scope_has_no_file_model_and_preserves_policy(self):
        root = str(Path.cwd())
        mandatory = str(Path(root) / "mandatory")
        default = str(Path(root) / "default")
        extra = str(Path(root) / "extra")
        discovery = Discovery((root,), (root,), (default,), (mandatory,))
        settings = load_settings(argument_parser("test").parse_args([]))
        settings.update(roots=(root,), extra_exclusions=(mandatory, extra), min_size_bytes=7)
        with patch("storage_scanner.options.discover_volumes", return_value=discovery):
            with patch("storage_scanner.core.models.ScanConfig", side_effect=AssertionError("File model")):
                _, scope = configured_scope(settings)
            self.assertIsInstance(scope, ScanScope)
            self.assertEqual(scope.excluded_paths, (mandatory, default, extra))
            _, config = configured_scan(settings)
        self.assertEqual(config, make_config(discovery, roots=(root,), min_size_bytes=7,
                                             excluded_paths=(default, mandatory, extra)))
        self.assertEqual(config.min_size_bytes, 7)

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

    def test_no_default_excludes_keeps_mandatory_policy_and_auto_scope(self):
        from storage_scanner.platform_fs.volumes import resolve_scope
        root = str(Path.cwd())
        system = str(Path(root) / 'system')
        mandatory = str(Path(root) / 'mandatory')
        discovery = Discovery((root, system, mandatory), (root, system, mandatory),
                              (system,), (mandatory,))
        self.assertEqual(resolve_scope(discovery).roots, (root,))
        settings = load_settings(argument_parser('test').parse_args(['--no-default-excludes']))
        with patch('storage_scanner.options.discover_volumes', return_value=discovery):
            _, scope = configured_scope(settings)
        self.assertEqual(scope.roots, (root, system))
        self.assertEqual(scope.excluded_paths, (mandatory,))
