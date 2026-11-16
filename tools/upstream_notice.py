# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

"""Observe the newest stable release without changing the selected upstream snapshot."""

import argparse
import json
from pathlib import Path
import re
from urllib.parse import quote
from urllib.request import Request, urlopen


def fetch_json(path):
    request = Request("https://api.github.com/" + path,
                      headers={"Accept": "application/vnd.github+json", "User-Agent": "Meltype-upstream-notice"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def observe(pin, fetch=fetch_json):
    if pin.get("repository") != "yksr-melt/Meltype" or not re.fullmatch(r"[0-9a-f]{40}", pin.get("commit", "")):
        raise ValueError("invalid selected upstream identity")
    base = f"repos/{pin['repository']}"
    release = fetch(f"{base}/releases/latest")
    if release.get("draft") or release.get("prerelease"):
        raise ValueError("release observation is not stable")
    tag = release["tag_name"]
    obj = fetch(f"{base}/git/ref/tags/{quote(tag, safe='')}")["object"]
    seen = set()
    while obj["type"] == "tag":
        if obj["sha"] in seen or len(seen) >= 8:
            raise ValueError("cyclic or excessive annotated tag chain")
        seen.add(obj["sha"])
        obj = fetch(f"{base}/git/tags/{obj['sha']}")["object"]
    if obj["type"] != "commit" or not re.fullmatch(r"[0-9a-f]{40}", obj["sha"]):
        raise ValueError("stable release does not resolve to one commit")
    if tag == pin["tag"] and obj["sha"] != pin["commit"]:
        raise ValueError("selected upstream tag moved; do not silently redefine its source")
    return {
        "schema": "meltype.upstream-observation.v1", "selected": pin,
        "observed": {"tag": tag, "commit": obj["sha"], "releaseId": release["id"],
                     "publishedAt": release["published_at"],
                     "assets": [{"name": asset["name"], "bytes": asset["size"], "digest": asset.get("digest")}
                                for asset in release.get("assets", [])]},
        "status": "selected-stable" if tag == pin["tag"] else "different-stable-review-required",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pin", type=Path, default=Path(".github/upstream-base.json"))
    args = parser.parse_args()
    print(json.dumps(observe(json.loads(args.pin.read_text(encoding="utf-8"))), indent=2))


if __name__ == "__main__":
    main()
