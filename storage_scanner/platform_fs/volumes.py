from dataclasses import dataclass
import os
import sys

from storage_scanner.core.models import ScanConfig
from storage_scanner.platform_fs.paths import contains_path, path_key


@dataclass(frozen=True, slots=True)
class Discovery:
    roots: tuple[str, ...]
    boundary_paths: tuple[str, ...]
    default_exclusions: tuple[str, ...]
    mandatory_exclusions: tuple[str, ...] = ()


def validate_roots(discovery: Discovery, roots):
    allowed = set(path_key(p) for p in discovery.roots)
    boundaries = set(path_key(p) for p in discovery.boundary_paths) | allowed
    for root in roots:
        candidate = path_key(root)
        owning_mount = max((p for p in boundaries if contains_path(p, candidate)), key=len, default=None)
        if owning_mount not in allowed:
            raise ValueError(f"Root is outside selected local volumes or inside an excluded mount: {root}")


def discover_volumes(include_removable: bool = False) -> Discovery:
    if os.name == "nt":
        from storage_scanner.platform_fs.windows import enumerate_candidates, select_volumes, default_exclusions
        candidates = enumerate_candidates()
        roots = select_volumes(candidates, include_removable)
        return Discovery(roots, tuple(c[0] for c in candidates), default_exclusions(roots))
    if sys.platform.startswith("linux"):
        from storage_scanner.platform_fs.linux import parse_mountinfo, select_mounts, DEFAULT_EXCLUSIONS, MANDATORY_EXCLUSIONS
        with open("/proc/self/mountinfo", encoding="utf-8", errors="surrogateescape") as handle:
            mounts = parse_mountinfo(handle.read())
        selected = select_mounts(mounts, include_removable)
        return Discovery(tuple(m.path for m in selected), tuple(m.path for m in mounts),
                         DEFAULT_EXCLUSIONS, MANDATORY_EXCLUSIONS)
    raise RuntimeError("Only Windows and Linux are supported")


def make_config(discovery: Discovery, *, roots=None, min_size_bytes=100 * 1024 * 1024,
                excluded_paths=None) -> ScanConfig:
    exclusions = discovery.default_exclusions if excluded_paths is None else tuple(excluded_paths)
    return ScanConfig(tuple(discovery.roots if roots is None else roots), min_size_bytes,
                      tuple(dict.fromkeys((*discovery.mandatory_exclusions, *exclusions))),
                      discovery.boundary_paths)
