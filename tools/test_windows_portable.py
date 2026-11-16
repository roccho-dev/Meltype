# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from check_windows_portable import inspect_package


class PortableArchiveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Path(self.directory.name) / "Meltype-1.6.0.1-windows-portable.zip"
        self.members = {
            "Meltype.exe": b"candidate",
            "Meltype.dll": b"candidate",
            "Meltype.Core.dll": b"candidate",
            "Meltype.portable": b"portable",
            "Meltype.deps.json": json.dumps({"libraries": {"Meltype/1.6.0.1": {}}}).encode(),
            "Meltype.runtimeconfig.json": b"{}",
            "mozc/meltype_mozc_helper.exe": b"candidate",
            "dotnet/shared/Microsoft.NETCore.App/10.0.0/System.Private.CoreLib.dll": b"candidate",
            "dotnet/shared/Microsoft.WindowsDesktop.App/10.0.0/System.Windows.Forms.dll": b"candidate",
        }

    def write(self):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name, contents in self.members.items():
                archive.writestr(name, contents)

    def test_windows_separators_and_exact_artifact_digest(self):
        self.members = {name.replace("/", "\\"): value for name, value in self.members.items()}
        self.write()
        result = inspect_package(self.archive, "1.6.0.1")
        self.assertEqual("Meltype.exe", result["executable"])
        self.assertEqual("distribution", result["updateOwner"])
        self.assertEqual(self.archive.stat().st_size, result["bytes"])
        self.assertEqual(64, len(result["sha256"]))

    def test_rejects_installer_and_updater_escape(self):
        for forbidden in ("install.ps1", "nested/update.ps1", "tip/MeltypeTip.dll", "data/config.json"):
            with self.subTest(forbidden=forbidden):
                self.members[forbidden] = b"must not ship"
                self.write()
                with self.assertRaises(ValueError):
                    inspect_package(self.archive, "1.6.0.1")
                del self.members[forbidden]

    def test_rejects_windows_path_alias_and_traversal(self):
        for name in ("MELTYPE.EXE", "../Meltype.exe", "C:/Meltype.exe", "Meltype.exe."):
            with self.subTest(name=name):
                self.members[name] = b"alias"
                self.write()
                with self.assertRaises(ValueError):
                    inspect_package(self.archive, "1.6.0.1")
                del self.members[name]

    def test_rejects_wrong_version_marker_or_missing_engine(self):
        self.write()
        with self.assertRaises(ValueError):
            inspect_package(self.archive, "1.6.0.2")
        self.members["Meltype.portable"] = b"ordinary"
        self.write()
        with self.assertRaises(ValueError):
            inspect_package(self.archive, "1.6.0.1")
        self.members["Meltype.portable"] = b"portable"
        del self.members["mozc/meltype_mozc_helper.exe"]
        self.write()
        with self.assertRaises(ValueError):
            inspect_package(self.archive, "1.6.0.1")


if __name__ == "__main__":
    unittest.main()
