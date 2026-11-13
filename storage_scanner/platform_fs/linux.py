"""Linux mount discovery. Parsing is separately testable on any host."""
from dataclasses import dataclass
import os
import re
import posixpath

from storage_scanner.platform_fs.paths import contains_path

DEFAULT_EXCLUSIONS = ("/proc", "/sys", "/dev", "/run", "/usr", "/bin", "/sbin",
                      "/lib", "/lib64", "/boot", "/etc")
MANDATORY_EXCLUSIONS = ("/proc", "/sys", "/dev", "/run")
LOCAL_TYPES = frozenset("ext2 ext3 ext4 xfs btrfs f2fs vfat exfat ntfs ntfs3 fuseblk zfs reiserfs jfs hfs hfsplus udf iso9660 bcachefs".split())
NETWORK_TYPES = frozenset("nfs nfs4 cifs smb3 smbfs ceph glusterfs fuse.sshfs fuse.rclone 9p afs coda davfs davfs2".split())
VIRTUAL_TYPES = frozenset("proc sysfs devtmpfs devpts tmpfs ramfs cgroup cgroup2 pstore securityfs debugfs tracefs configfs fusectl mqueue hugetlbfs autofs binfmt_misc rpc_pipefs nsfs squashfs efivarfs selinuxfs bpf overlay aufs".split())


@dataclass(frozen=True, slots=True)
class Mount:
    path: str
    filesystem: str
    device: str
    source_root: str
    source: str


def unescape_mount(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def parse_mountinfo(text: str) -> tuple[Mount, ...]:
    mounts = []
    for line in text.splitlines():
        try:
            left, right = line.split(" - ", 1)
            fields, tail = left.split(), right.split()
            mounts.append(Mount(unescape_mount(fields[4]), tail[0], fields[2],
                                unescape_mount(fields[3]), unescape_mount(tail[1])))
        except (ValueError, IndexError):
            continue
    return tuple(mounts)


def is_local(mount: Mount) -> bool:
    if mount.filesystem in NETWORK_TYPES or mount.filesystem.startswith(("nfs", "fuse.")):
        return False
    # Container root is useful; other overlay mounts are not additional storage.
    if mount.path == "/" and mount.filesystem in {"overlay", "aufs"}:
        return True
    if mount.filesystem in VIRTUAL_TYPES:
        return False
    return mount.filesystem in LOCAL_TYPES or mount.source.startswith("/dev/")


def is_removable(mount: Mount) -> bool:
    location = os.path.realpath("/sys/dev/block/" + mount.device)
    while location.startswith("/sys/"):
        try:
            with open(os.path.join(location, "removable"), encoding="ascii") as handle:
                return handle.read().strip() == "1"
        except OSError:
            parent = os.path.dirname(location)
            if parent == location:
                break
            location = parent
    return False


def select_mounts(mounts, include_removable=False, removable_check=is_removable):
    candidates = [m for m in mounts if is_local(m) and
                  not any(contains_path(p, m.path, windows=False) for p in MANDATORY_EXCLUSIONS) and
                  (include_removable or not removable_check(m))]
    # A bind alias of an already selected device subtree adds no new data.
    selected = []
    for mount in sorted(candidates, key=lambda m: (len(m.source_root), len(m.path), m.path)):
        duplicate = False
        for existing in selected:
            if existing.device != mount.device or existing.filesystem != mount.filesystem:
                continue
            if existing.source_root == mount.source_root:
                duplicate = True
                break
            if contains_path(existing.source_root, mount.source_root, windows=False):
                original = posixpath.normpath(posixpath.join(existing.path,
                    posixpath.relpath(mount.source_root, existing.source_root)))
                # Only discard an alias if its original path is traversable from
                # the selected root, rather than hidden behind a mount boundary.
                if not any(other.path != existing.path and
                           contains_path(other.path, original, windows=False) for other in mounts):
                    duplicate = True
                    break
        if duplicate:
            continue
        selected.append(mount)
    return tuple(selected)
