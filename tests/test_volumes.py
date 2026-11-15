import unittest

from storage_scanner.platform_fs.linux import (Mount, parse_mountinfo, select_mounts, is_local,
                                              DEFAULT_EXCLUSIONS)
from storage_scanner.platform_fs.windows import select_volumes
from storage_scanner.platform_fs.volumes import Discovery, validate_roots


class VolumeTests(unittest.TestCase):
    def test_windows_fixed_removable_alias_network(self):
        candidates = (("C:\\", 3, "vol1"), ("D:\\", 2, "vol2"),
                      ("N:\\", 4, "network"), ("C:\\mounted\\", 3, "vol1"),
                      ("C:\\data\\", 3, "vol3"))
        self.assertEqual(select_volumes(candidates), ("c:\\", "c:\\data"))
        self.assertEqual(len(select_volumes(candidates, True)), 3)

    def test_mount_parser_unescapes_and_ignores_bad_lines(self):
        text = "35 25 8:1 / / rw - ext4 /dev/sda1 rw\n36 35 8:2 / /media/My\\040Disk rw shared:3 - exfat /dev/sdb1 rw\nbroken"
        mounts = parse_mountinfo(text)
        self.assertEqual(len(mounts), 2)
        self.assertEqual(mounts[1].path, "/media/My Disk")

    def test_mount_selection_and_bind_deduplication(self):
        mounts = (Mount("/", "ext4", "8:1", "/", "/dev/sda1"),
                  Mount("/bind", "ext4", "8:1", "/home", "/dev/sda1"),
                  Mount("/mnt/data", "xfs", "8:2", "/", "/dev/sdb1"),
                  Mount("/network", "nfs4", "0:3", "/", "server:/share"),
                  Mount("/proc", "proc", "0:1", "/", "proc"),
                  Mount("/media/usb", "vfat", "8:3", "/", "/dev/sdc1"))
        check = lambda m: m.device == "8:3"
        self.assertEqual({m.path for m in select_mounts(mounts, removable_check=check)}, {"/", "/mnt/data"})
        self.assertEqual(len(select_mounts(mounts, True, check)), 3)

    def test_bind_at_original_boundary_is_not_lost(self):
        mounts = (Mount("/", "ext4", "8:1", "/", "/dev/sda1"),
                  Mount("/home", "ext4", "8:1", "/home", "/dev/sda1"))
        self.assertEqual(len(select_mounts(mounts, removable_check=lambda m: False)), 2)

    def test_btrfs_disjoint_subvolumes_and_container_root(self):
        mounts = (Mount("/", "btrfs", "8:1", "/@", "/dev/sda1"),
                  Mount("/home", "btrfs", "8:1", "/@home", "/dev/sda1"))
        self.assertEqual(len(select_mounts(mounts, removable_check=lambda m: False)), 2)
        self.assertTrue(is_local(Mount("/", "overlay", "0:1", "/", "overlay")))
        self.assertFalse(is_local(Mount("/other", "overlay", "0:1", "/", "overlay")))

    def test_user_data_not_blanket_excluded(self):
        for path in ("/home", "/mnt", "/media", "/opt", "/var"):
            self.assertNotIn(path, DEFAULT_EXCLUSIONS)

    def test_reject_explicit_root_below_network_mount(self):
        # Host-native paths so this test also runs on Windows.
        import os
        from pathlib import Path
        local = str(Path.cwd())
        network = os.path.join(local, "network")
        discovery = Discovery((local,), (local, network), ())
        validate_roots(discovery, (os.path.join(local, "data"),))
        with self.assertRaises(ValueError):
            validate_roots(discovery, (os.path.join(network, "data"),))

    def test_runtime_media_only_confirmed_local_mounts_and_deduplication(self):
        local = Mount('/run/media/user/USB', 'exfat', '8:9', '/', '/dev/sdz1')
        mounts = (Mount('/', 'ext4', '8:1', '/', '/dev/sda1'), local,
                  Mount('/run/media/user/alias', 'exfat', '8:9', '/', '/dev/sdz1'),
                  Mount('/run/media/user/network', 'nfs4', '0:2', '/', 'host:/data'),
                  Mount('/run/media/user/virtual', 'tmpfs', '0:3', '/', 'tmpfs'),
                  Mount('/run/private', 'ext4', '8:8', '/', '/dev/sdy1'))
        check = lambda m: m.device == '8:9'
        self.assertEqual([m.path for m in select_mounts(mounts, removable_check=check)], ['/'])
        selected = select_mounts(mounts, True, check)
        self.assertEqual(len(selected), 2)
        self.assertEqual(sum(m.device == '8:9' for m in selected), 1)
