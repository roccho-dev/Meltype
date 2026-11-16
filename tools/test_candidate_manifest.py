# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

import copy
from pathlib import Path
import tempfile
import unittest

from candidate_manifest import file_identity, verify, verify_provider


class CandidatePromotionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.file = self.root / "Meltype-1.6.0.1-linux.zip"
        self.file.write_bytes(b"candidate archive bytes")
        self.commit = "1" * 40
        self.manifest = {
            "schema": "meltype.candidate.v1", "sourceRepository": "roccho-dev/Meltype",
            "sourceCommit": self.commit, "version": "1.6.0.1", "kind": "linux",
            "artifacts": [file_identity(self.file)],
        }

    def check(self, manifest):
        return verify(manifest, self.root, "roccho-dev/Meltype", self.commit, "1.6.0.1", "linux")

    def test_verifies_original_bytes_and_refuses_rebuilt_or_replaced_bytes(self):
        self.check(self.manifest)
        self.file.write_bytes(b"different candidate bytes")
        with self.assertRaises(ValueError):
            self.check(self.manifest)

    def test_refuses_mixed_source_platform_and_version(self):
        for key, value in (("sourceCommit", "2" * 40), ("sourceRepository", "yksr-melt/Meltype"),
                           ("version", "1.6.0.2"), ("kind", "windows")):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.manifest)
                changed[key] = value
                with self.assertRaises(ValueError):
                    self.check(changed)

    def test_refuses_extra_or_aliased_artifacts(self):
        for name in ("../outside.zip", "Meltype-1.6.0.2-linux.zip"):
            changed = copy.deepcopy(self.manifest)
            changed["artifacts"][0]["file"] = name
            with self.assertRaises(ValueError):
                self.check(changed)
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"] *= 2
        with self.assertRaises(ValueError):
            self.check(changed)

    def test_provider_run_and_artifact_must_be_successful_same_source(self):
        repository = {"id": 123, "full_name": "roccho-dev/Meltype"}
        run = {"id": 45, "status": "completed", "conclusion": "success", "event": "workflow_dispatch", "head_sha": self.commit,
               "repository": repository, "head_repository": repository, "path": ".github/workflows/fork-linux.yml"}
        artifact = {"id": 67, "name": "meltype-linux-candidate", "expired": False,
                    "workflow_run": {"id": 45, "head_sha": self.commit, "repository_id": 123, "head_repository_id": 123}}
        def check(r, a):
            verify_provider(r, a, "roccho-dev/Meltype", self.commit, ".github/workflows/fork-linux.yml", "meltype-linux-candidate")
        check(run, artifact)
        for key, value in (("conclusion", "failure"), ("head_sha", "2" * 40), ("event", "pull_request")):
            changed = copy.deepcopy(run)
            changed[key] = value
            with self.assertRaises(ValueError):
                check(changed, artifact)
        for key, value in (("id", 46), ("repository_id", 999), ("head_sha", "2" * 40)):
            changed = copy.deepcopy(artifact)
            changed["workflow_run"][key] = value
            with self.assertRaises(ValueError):
                check(run, changed)


if __name__ == "__main__":
    unittest.main()
