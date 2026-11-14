"""Shared command-line configuration, using only the standard library."""
import argparse
import json
from pathlib import Path

from storage_scanner.core.filters import mib_to_bytes
from storage_scanner.platform_fs.paths import path_key
from storage_scanner.platform_fs.volumes import discover_volumes, make_config, validate_roots


def argument_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("roots", nargs="*", help="Explicit scan paths (default: discovered local volumes)")
    parser.add_argument("--mode", choices=("folder", "file"), default=None, help="GUI scan mode (default: folder)")
    parser.add_argument("--min-size-mib", default=None, help="Inclusive minimum, default 100 MiB")
    parser.add_argument("--exclude", action="append", default=[], help="Excluded file or directory; repeatable")
    parser.add_argument("--include-removable", action="store_true", help="Include removable local volumes")
    parser.add_argument("--no-default-excludes", action="store_true", help="Disable optional system path exclusions")
    parser.add_argument("--config", type=Path, help="JSON configuration file (see config.example.json)")
    return parser


def load_settings(args):
    data = {}
    if args.config:
        with args.config.open(encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Configuration must be a JSON object")
    for key in ("roots", "excluded_paths"):
        if key in data and (not isinstance(data[key], list) or
                            not all(isinstance(p, str) and p.strip() for p in data[key])):
            raise ValueError(f"{key} must be an array of nonempty paths")
    if "roots" in data and not data["roots"]:
        raise ValueError("roots must contain at least one path, or be omitted for discovery")
    for key in ("include_removable", "use_default_exclusions"):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(f"{key} must be boolean")
    mode = args.mode or data.get("scan_mode", "folder")
    if mode not in ("folder", "file"):
        raise ValueError("scan_mode must be folder or file")
    roots = args.roots or data.get("roots")
    return {
        "scan_mode": mode,
        "roots": tuple(path_key(p) for p in roots) if roots is not None else None,
        "min_size_bytes": mib_to_bytes(str(args.min_size_mib if args.min_size_mib is not None
                                            else data.get("min_size_mib", "100"))),
        "extra_exclusions": tuple(path_key(p) for p in (*data.get("excluded_paths", ()), *args.exclude)),
        "include_removable": args.include_removable or data.get("include_removable", False),
        "use_default_exclusions": not args.no_default_excludes and data.get("use_default_exclusions", True),
        "update_ms": data.get("gui_update_ms", 1000),
    }


def configured_scan(settings):
    discovery = discover_volumes(settings["include_removable"])
    if settings["roots"] is not None:
        validate_roots(discovery, settings["roots"])
    excludes = settings["extra_exclusions"]
    if settings["use_default_exclusions"]:
        excludes = (*discovery.default_exclusions, *excludes)
    return discovery, make_config(discovery, roots=settings["roots"],
                                  min_size_bytes=settings["min_size_bytes"], excluded_paths=excludes)
