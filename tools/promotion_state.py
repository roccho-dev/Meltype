# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

"""Reconcile an occupied release identity before creating or promoting it."""

import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from candidate_manifest import file_identity


def decide_release(existing, latest_tag, expected_tag, paths, prerelease, make_latest):
    if prerelease and make_latest:
        raise ValueError("prerelease cannot be latest")
    if existing is None:
        return "create"
    if existing.get("tag_name") != expected_tag or existing.get("draft") is not False:
        raise ValueError("occupied release is not the expected published identity")
    expected = {path.name: file_identity(path) for path in paths}
    assets = existing.get("assets", [])
    if len(assets) != len(expected) or {asset.get("name") for asset in assets} != set(expected):
        raise ValueError("occupied release has partial, different or extra assets")
    for asset in assets:
        identity = expected[asset["name"]]
        if (asset.get("state") != "uploaded" or asset.get("size") != identity["bytes"] or
                asset.get("digest") != "sha256:" + identity["sha256"]):
            raise ValueError("occupied release bytes differ from the tested candidate")
    if not isinstance(existing.get("prerelease"), bool):
        raise ValueError("occupied release state is unknown")
    if not existing["prerelease"] and prerelease:
        raise ValueError("a stable release is not demoted to a candidate")
    if existing["prerelease"] == prerelease and (not make_latest or latest_tag == expected_tag):
        return "accepted"
    return "edit"


def fetch_optional(path):
    token = os.environ.get("GH_TOKEN")
    if not token:
        raise ValueError("GH_TOKEN is required for release identity readback")
    request = Request("https://api.github.com/" + path,
                      headers={"Accept": "application/vnd.github+json", "Authorization": "Bearer " + token,
                               "User-Agent": "Meltype-candidate-promotion"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        if error.code == 404:
            return None
        raise ValueError(f"release readback failed with HTTP {error.code}") from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--prerelease", choices=("true", "false"), required=True)
    parser.add_argument("--make-latest", choices=("true", "false"), required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repository):
        raise ValueError("invalid repository")
    base = f"repos/{args.repository}/releases"
    existing = fetch_optional(base + "/tags/" + quote(args.tag, safe=""))
    latest = fetch_optional(base + "/latest") if args.make_latest == "true" else None
    paths = sorted(args.directory.glob("Meltype-*.zip")) + [args.directory / "windows-candidate.json", args.directory / "linux-candidate.json"]
    print(decide_release(existing, latest["tag_name"] if latest else None, args.tag, paths,
                         args.prerelease == "true", args.make_latest == "true"))


if __name__ == "__main__":
    main()
