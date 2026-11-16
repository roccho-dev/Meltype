# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

"""Bind candidate artifacts to one source commit; promotion verifies these bytes without rebuilding."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

from check_windows_portable import inspect_package


def file_identity(path):
    path = Path(path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"file": path.name, "bytes": path.stat().st_size, "sha256": digest}


def verify_provider(run, artifact, repository, commit, workflow_path, artifact_name):
    if (not isinstance(run.get("id"), int) or not isinstance(artifact.get("id"), int) or
            not isinstance(run.get("repository", {}).get("id"), int) or
            run.get("status") != "completed" or run.get("conclusion") != "success" or run.get("event") not in ("push", "workflow_dispatch") or
            run.get("head_sha") != commit or run.get("repository", {}).get("full_name") != repository or
            run.get("head_repository", {}).get("full_name") != repository or run.get("path") != workflow_path):
        raise ValueError("candidate run is not a successful same-repository source execution")
    identity = artifact.get("workflow_run", {})
    if (artifact.get("expired") is not False or artifact.get("name") != artifact_name or
            identity.get("id") != run.get("id") or identity.get("head_sha") != commit or
            identity.get("repository_id") != run.get("repository", {}).get("id") or
            identity.get("head_repository_id") != run.get("repository", {}).get("id")):
        raise ValueError("artifact identity is not from the selected candidate run")


def verify(manifest, directory, repository, commit, version, kind):
    if (manifest.get("schema") != "meltype.candidate.v1" or
            manifest.get("sourceRepository") != repository or
            manifest.get("sourceCommit") != commit or
            manifest.get("version") != version or manifest.get("kind") != kind):
        raise ValueError("candidate source, version or platform differs")
    artifacts = manifest.get("artifacts", [])
    expected = ({f"Meltype-{version}-windows.zip", f"Meltype-{version}-windows-portable.zip"}
                if kind == "windows" else {f"Meltype-{version}-linux.zip"})
    if len(artifacts) != len(expected) or {item.get("file") for item in artifacts} != expected:
        raise ValueError("candidate artifacts are not the exact declared product set")
    for item in artifacts:
        if file_identity(Path(directory) / item["file"]) != item:
            raise ValueError(f"candidate bytes differ: {item['file']}")
    if kind == "windows":
        portable = inspect_package(Path(directory) / f"Meltype-{version}-windows-portable.zip", version)
        if manifest.get("managedWindows") != portable:
            raise ValueError("portable consumer contract differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("create", "verify", "provider"))
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--directory", type=Path, default=Path("dist"))
    parser.add_argument("--repository", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--kind", choices=("windows", "linux"), required=True)
    parser.add_argument("--provider-run", type=Path)
    parser.add_argument("--provider-artifact", type=Path)
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.commit):
        raise ValueError("source commit must be immutable")
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:\.\d+)?(?:-[A-Za-z0-9.]+)?", args.version):
        raise ValueError("candidate version is invalid")
    if args.command == "provider":
        run = json.loads(args.provider_run.read_text(encoding="utf-8"))
        artifact = json.loads(args.provider_artifact.read_text(encoding="utf-8"))
        workflow = ".github/workflows/fork-build.yml" if args.kind == "windows" else ".github/workflows/fork-linux.yml"
        verify_provider(run, artifact, args.repository, args.commit, workflow, f"meltype-{args.kind}-candidate")
    elif args.command == "create":
        if args.manifest is None:
            parser.error("--manifest is required when creating a candidate")
        actual_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
        if actual_commit != args.commit:
            raise ValueError("candidate source is not the checked-out commit")
        subprocess.run(["git", "diff", "--quiet", "HEAD", "--"], check=True)
        names = ([f"Meltype-{args.version}-windows.zip", f"Meltype-{args.version}-windows-portable.zip"]
                 if args.kind == "windows" else [f"Meltype-{args.version}-linux.zip"])
        manifest = {
            "schema": "meltype.candidate.v1", "sourceRepository": args.repository,
            "sourceCommit": args.commit, "version": args.version, "kind": args.kind,
            "artifacts": [file_identity(args.directory / name) for name in names],
        }
        if args.kind == "windows":
            manifest["managedWindows"] = inspect_package(args.directory / names[1], args.version)
        verify(manifest, args.directory, args.repository, args.commit, args.version, args.kind)
        args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    else:
        if args.manifest is None:
            parser.error("--manifest is required when verifying a candidate")
        verify(json.loads(args.manifest.read_text(encoding="utf-8")), args.directory,
               args.repository, args.commit, args.version, args.kind)
    print(f"PASS: {args.kind} candidate source and artifact bytes")


if __name__ == "__main__":
    main()
