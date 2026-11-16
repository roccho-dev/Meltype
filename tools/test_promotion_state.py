# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

import copy
from pathlib import Path
import tempfile
import unittest

from candidate_manifest import file_identity
from promotion_state import decide_release


class OccupiedReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.file = Path(self.directory.name) / "Meltype-1.6.0.1-windows.zip"
        self.file.write_bytes(b"tested bytes")
        identity = file_identity(self.file)
        self.release = {"tag_name": "v1.6.0.1", "draft": False, "prerelease": True,
                        "assets": [{"name": identity["file"], "size": identity["bytes"],
                                    "digest": "sha256:" + identity["sha256"], "state": "uploaded"}]}

    def test_confirmed_absence_creates_but_successful_retry_is_noop(self):
        self.assertEqual("create", decide_release(None, None, "v1.6.0.1", [self.file], True, False))
        self.assertEqual("accepted", decide_release(self.release, None, "v1.6.0.1", [self.file], True, False))

    def test_same_tested_bytes_can_promote_prerelease_without_rebuilding(self):
        self.assertEqual("edit", decide_release(self.release, None, "v1.6.0.1", [self.file], False, True))
        self.release["prerelease"] = False
        self.assertEqual("accepted", decide_release(self.release, "v1.6.0.1", "v1.6.0.1", [self.file], False, True))

    def test_partial_or_replaced_bytes_must_not_be_overwritten(self):
        partial = copy.deepcopy(self.release)
        partial["assets"] = []
        with self.assertRaises(ValueError):
            decide_release(partial, None, "v1.6.0.1", [self.file], False, True)
        self.file.write_bytes(b"rebuilt candidate")
        with self.assertRaises(ValueError):
            decide_release(self.release, None, "v1.6.0.1", [self.file], False, True)


if __name__ == "__main__":
    unittest.main()
