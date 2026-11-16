# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

"""Check the immutable Windows portable payload before a consumer extracts it."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile


def inspect_package(archive, expected_version):
    archive = Path(archive)
    entries = {}
    with zipfile.ZipFile(archive) as package:
        for entry in package.infolist():
            name = entry.filename.replace("\\", "/")
            parts = name.rstrip("/").split("/")
            if (not parts or name.startswith("/") or
                    any(part in ("", ".", "..") or ":" in part or part.endswith((" ", ".")) for part in parts)):
                raise ValueError(f"unsafe Windows path: {entry.filename}")
            if stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError(f"symbolic link is not a portable member: {name}")
            key = name.rstrip("/").casefold()
            if key in entries:
                raise ValueError(f"duplicate Windows path: {name}")
            entries[key] = entry
            if entry.is_dir():
                continue
            if (parts[0].casefold() in ("app", "data", "tip") or
                    parts[-1].casefold() in ("update.ps1", "install.ps1", "install.cmd", "uninstall.ps1", "uninstall.cmd", "meltype-ime.ps1")):
                raise ValueError(f"mutable installer/update payload in portable package: {name}")
        required = ("meltype.exe", "meltype.dll", "meltype.core.dll", "meltype.portable",
                    "meltype.deps.json", "meltype.runtimeconfig.json", "mozc/meltype_mozc_helper.exe")
        for name in required:
            if name not in entries or entries[name].is_dir():
                raise ValueError(f"required portable member is absent: {name}")
        if package.read(entries["meltype.portable"]).strip() != b"portable":
            raise ValueError("portable marker does not select portable execution")
        deps = json.loads(package.read(entries["meltype.deps.json"]))
        if f"Meltype/{expected_version}" not in deps.get("libraries", {}):
            raise ValueError(f"package version is not {expected_version}")
        if not any(re.fullmatch(r"dotnet/shared/microsoft\.netcore\.app/[^/]+/system\.private\.corelib\.dll", name) for name in entries):
            raise ValueError("private .NET runtime is absent")
        if not any(re.fullmatch(r"dotnet/shared/microsoft\.windowsdesktop\.app/[^/]+/system\.windows\.forms\.dll", name) for name in entries):
            raise ValueError("private Windows desktop runtime is absent")
    with archive.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "file": archive.name,
        "version": expected_version,
        "bytes": archive.stat().st_size,
        "sha256": digest,
        "executable": "Meltype.exe",
        "marker": "Meltype.portable",
        "inputMode": "Keyboard",
        "updateOwner": "distribution",
        "dataOverride": "MELTYPE_DATA_DIR",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    print(json.dumps(inspect_package(args.archive, args.version), indent=2))


if __name__ == "__main__":
    main()
