# SPDX-License-Identifier: GPL-3.0-or-later
"""The Linux producer's own immutable source and same-build package receipt.

No consumer pins, remote artifact selector, credentials or acceptance decision.
Importing this module does not fetch, measure, build or publish anything.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import tempfile
import zipfile


REPOSITORY = "roccho-dev/Meltype"
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
REQUIRED = ("libMeltypeNative.so", "mozc/meltype_mozc_helper",
    "tty/meltype_tty.py", "tty/kitty_display.py")


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sri(value):
    require(isinstance(value, str) and value.startswith("sha256-"), "invalid NAR hash")
    try:
        decoded = base64.b64decode(value[7:], validate=True)
    except (ValueError, TypeError):
        raise ValueError("invalid NAR hash") from None
    require(len(decoded) == 32 and base64.b64encode(decoded).decode() == value[7:],
        "invalid NAR hash")
    return value


def identity(env, checkout_rev, checkout_tree):
    require(env.get("GITHUB_REPOSITORY") == REPOSITORY, "not this producer")
    rev = env.get("GITHUB_SHA", "")
    require(HEX40.fullmatch(rev) is not None and rev == checkout_rev,
        "checkout does not match producer revision")
    require(HEX40.fullmatch(checkout_tree) is not None, "invalid checkout tree")
    numeric = {}
    for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "MELTYPE_ARTIFACT_ID"):
        value = env.get(key, "")
        require(re.fullmatch(r"[1-9][0-9]*", value) is not None, "invalid producer identity")
        numeric[key] = int(value)
    digest = env.get("MELTYPE_ARTIFACT_DIGEST", "")
    require(HEX64.fullmatch(digest) is not None, "invalid producer artifact digest")
    return {"rev": rev, "tree": checkout_tree, "run": numeric["GITHUB_RUN_ID"],
        "attempt": numeric["GITHUB_RUN_ATTEMPT"],
        "artifact": numeric["MELTYPE_ARTIFACT_ID"], "digest": digest}


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_identity(raw, rev):
    value = json.loads(raw)
    require(type(value) is dict and set(value) == {"rev", "narHash"},
        "fetched source identity is not a record")
    require(value["rev"] == rev, "fetched source revision mismatch")
    return sri(value["narHash"])


def checked_archive(archive):
    """Validate this producer's one package before using the regular unzip path."""
    seen = set()
    total = 0
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        require(0 < len(entries) <= 4096, "invalid package entry count")
        for entry in entries:
            name = entry.filename
            parts = name.rstrip("/").split("/")
            require(name == entry.orig_filename and "\\" not in name
                and not name.startswith("/") and parts[0] == "Meltype-linux"
                and all(part not in ("", ".", "..") for part in parts)
                and str(PurePosixPath(name)).rstrip("/") == name.rstrip("/"),
                "unsafe package member")
            require(name.rstrip("/") not in seen, "duplicate package member")
            seen.add(name.rstrip("/"))
            kind = stat.S_IFMT(entry.external_attr >> 16)
            require(kind in ((0, stat.S_IFDIR) if entry.is_dir() else (0, stat.S_IFREG)),
                "non-regular package member")
            require(not entry.flag_bits & 1, "encrypted package member")
            total += entry.file_size
            require(total <= 256 * 1024 * 1024, "package too large")
        for name in REQUIRED:
            require("Meltype-linux/" + name in seen, "missing package product")


def fingerprint(directory):
    require(directory.is_dir() and not directory.is_symlink(), "invalid package directory")
    result = {}
    for path in sorted(directory.rglob("*")):
        require(not path.is_symlink(), "package symlink")
        name = path.relative_to(directory).as_posix()
        mode = path.stat().st_mode
        if stat.S_ISDIR(mode):
            result[name] = {"kind": "directory"}
        else:
            require(stat.S_ISREG(mode), "non-regular package file")
            result[name] = {"kind": "file", "size": path.stat().st_size,
                "executable": bool(mode & 0o111), "sha256": file_hash(path)}
    return result


def same_package(tested, unpacked):
    require(fingerprint(tested) == fingerprint(unpacked),
        "distributed package differs from tested package")


def command(args, root):
    return subprocess.run(args, cwd=root, check=True, capture_output=True,
        text=True, timeout=180).stdout.strip()


def main():
    # Both locations are this producer's existing build products, not caller input.
    root = Path(__file__).resolve().parents[2]
    build = root / "linux/build"
    tested = build / "Meltype-linux"
    rev = command(["git", "rev-parse", "HEAD"], root)
    tree = command(["git", "rev-parse", "HEAD^{tree}"], root)
    owner = identity(os.environ, rev, tree)
    archives = sorted(build.glob("Meltype-*.zip"))
    require(len(archives) == 1 and archives[0].is_file()
        and not archives[0].is_symlink(), "expected this build's one ZIP")
    archive = archives[0]
    checked_archive(archive)
    nix = ["nix", "--extra-experimental-features", "nix-command flakes"]
    with tempfile.TemporaryDirectory(prefix="meltype-supply-", dir=build) as temporary:
        command(["unzip", "-q", str(archive), "-d", temporary], root)
        unpacked = Path(temporary) / "Meltype-linux"
        same_package(tested, unpacked)
        package_nar = sri(command(nix + ["hash", "path", "--type", "sha256", str(unpacked)], root))
        require(package_nar == sri(command(nix + ["hash", "path", "--type", "sha256", str(tested)], root)),
            "package NAR differs from tested package")
        products = {name: file_hash(unpacked / name) for name in REQUIRED}
    # Fetch the public immutable source exactly as a normal fetchTree consumer
    # would. Do not hash a checkout containing .git, build output or audit files.
    # A fetchTree result has outPath, so Nix's JSON conversion treats the whole
    # result as a store-path string. Explicitly project the two identity fields.
    expression = ('let source = builtins.fetchTree { type = "github"; owner = "roccho-dev"; '
        'repo = "Meltype"; rev = "' + rev + '"; }; in { inherit (source) rev narHash; }')
    source_nar = source_identity(command(nix + ["eval", "--json", "--expr", expression], root), rev)
    result = {"schema": "meltype-linux-supply/1", "repository": REPOSITORY,
        "source": {"rev": rev, "tree": tree, "narHash": source_nar},
        "producer": {"workflow": ".github/workflows/linux.yml",
            "run": owner["run"], "attempt": owner["attempt"]},
        "artifact": {"id": owner["artifact"], "sha256": owner["digest"],
            "member": archive.name, "memberSize": archive.stat().st_size,
            "memberSha256": file_hash(archive), "packageNarHash": package_nar,
            "products": products}}
    target = build / "meltype-supply.json"
    target.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
