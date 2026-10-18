# SPDX-License-Identifier: GPL-3.0-or-later
"""Producer identity and distributed-versus-tested package refusal boundaries.

These tests do not replace the CI's real Nix measurements or artifact readback.
"""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
import zipfile


spec = importlib.util.spec_from_file_location("meltype_supply", Path(__file__).parents[1] / "supply.py")
supply = importlib.util.module_from_spec(spec)
spec.loader.exec_module(supply)


class SupplyTests(unittest.TestCase):
    def env(self):
        return {"GITHUB_REPOSITORY": "roccho-dev/Meltype", "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "1",
            "MELTYPE_ARTIFACT_ID": "456", "MELTYPE_ARTIFACT_DIGEST": "b" * 64}

    def test_own_exact_checkout_identity(self):
        got = supply.identity(self.env(), "a" * 40, "c" * 40)
        self.assertEqual((got["rev"], got["tree"], got["run"], got["artifact"]),
            ("a" * 40, "c" * 40, 123, 456))

    def test_other_repository_is_not_a_calculation_target(self):
        env = self.env(); env["GITHUB_REPOSITORY"] = "roccho-dev/resources"
        with self.assertRaises(ValueError): supply.identity(env, "a" * 40, "c" * 40)

    def test_other_revision_or_merge_checkout_is_rejected(self):
        with self.assertRaises(ValueError): supply.identity(self.env(), "d" * 40, "c" * 40)

    def test_nonliteral_revision_is_rejected(self):
        env = self.env(); env["GITHUB_SHA"] = 'main"; ignored = true;'
        with self.assertRaises(ValueError): supply.identity(env, env["GITHUB_SHA"], "c" * 40)

    def test_missing_or_invalid_run_artifact_identity_is_rejected(self):
        for key in ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "MELTYPE_ARTIFACT_ID"):
            for invalid in ("", "0", "-1", "1.0", "01", " 1"):
                with self.subTest(key=key, invalid=invalid):
                    env = self.env(); env[key] = invalid
                    with self.assertRaises(ValueError): supply.identity(env, "a" * 40, "c" * 40)

    def test_missing_digest_or_invalid_tree_is_rejected(self):
        env = self.env(); env["MELTYPE_ARTIFACT_DIGEST"] = ""
        with self.assertRaises(ValueError): supply.identity(env, "a" * 40, "c" * 40)
        with self.assertRaises(ValueError): supply.identity(self.env(), "a" * 40, "main")

    def test_sri_requires_exact_canonical_sha256(self):
        good = "sha256-" + "A" * 43 + "="
        self.assertEqual(supply.sri(good), good)
        for bad in (None, "", "b" * 64, "sha256-aA==", "sha512-" + "A" * 43 + "=",
                "sha256-" + "A" * 42 + "B="):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): supply.sri(bad)

    def test_fetch_tree_identity_requires_explicit_record_not_store_path_coercion(self):
        good = "sha256-" + "A" * 43 + "="
        value = {"rev": "a" * 40, "narHash": good}
        self.assertEqual(supply.source_identity(json.dumps(value), "a" * 40), good)
        for invalid in ("/nix/store/source", None, [], {}, {**value, "outPath": "/nix/store/source"}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                supply.source_identity(json.dumps(invalid), "a" * 40)

    def test_fetch_tree_other_revision_and_invalid_nar_are_rejected(self):
        for value in ({"rev": "b" * 40, "narHash": "sha256-" + "A" * 43 + "="},
                {"rev": "a" * 40, "narHash": "not-a-NAR"}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                supply.source_identity(json.dumps(value), "a" * 40)

    def archive(self, directory, extra=None):
        target = Path(directory) / "own.zip"
        with zipfile.ZipFile(target, "w") as output:
            for name in supply.REQUIRED:
                output.writestr("Meltype-linux/" + name, b"same-build")
            if extra is not None:
                name, mode = extra
                entry = zipfile.ZipInfo(name); entry.create_system = 3
                # ZipInfo's Windows constructor normalizes backslashes. Keep
                # the malicious archive member itself, not a repaired fixture.
                entry.filename = entry.orig_filename = name
                entry.external_attr = mode << 16
                output.writestr(entry, b"other")
        return target

    def test_own_regular_package_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            supply.checked_archive(self.archive(directory))

    def test_path_escape_and_foreign_roots_are_rejected(self):
        for name in ("../escape", "/Meltype-linux/escape", "other/file",
                "Meltype-linux/../escape", "Meltype-linux/./file", "Meltype-linux//file",
                "Meltype-linux\\escape"):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    supply.checked_archive(self.archive(directory, (name, stat.S_IFREG | 0o644)))

    def test_duplicate_product_member_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertWarns(UserWarning):
                archive = self.archive(directory, ("Meltype-linux/" + supply.REQUIRED[0], stat.S_IFREG | 0o644))
            with self.assertRaises(ValueError): supply.checked_archive(archive)

    def test_zip_symlink_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = self.archive(directory, ("Meltype-linux/link", stat.S_IFLNK | 0o777))
            with self.assertRaises(ValueError): supply.checked_archive(archive)

    def test_missing_product_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "empty.zip"
            with zipfile.ZipFile(target, "w") as output: output.writestr("Meltype-linux/readme", "text")
            with self.assertRaises(ValueError): supply.checked_archive(target)

    def packages(self, directory):
        first, second = Path(directory) / "tested", Path(directory) / "unpacked"
        first.mkdir(); second.mkdir()
        for root in (first, second):
            (root / "empty-directory").mkdir()
            (root / "native").write_bytes(b"exact\x00binary")
        return first, second

    def test_same_bytes_and_directory_structure_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.packages(directory)
            supply.same_package(first, second)
            (second / "native").write_bytes(b"different")
            with self.assertRaises(ValueError): supply.same_package(first, second)

    def test_missing_empty_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.packages(directory)
            (second / "empty-directory").rmdir()
            with self.assertRaises(ValueError): supply.same_package(first, second)

    @unittest.skipUnless(os.name == "posix", "Unix executable mode is measured by the Linux producer")
    def test_changed_executable_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.packages(directory)
            (second / "native").chmod(0o755)
            with self.assertRaises(ValueError): supply.same_package(first, second)

    @unittest.skipUnless(os.name == "posix", "symlinks are measured by the Linux producer")
    def test_tested_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = self.packages(directory)
            (second / "link").symlink_to(first / "native")
            with self.assertRaises(ValueError): supply.same_package(first, second)


if __name__ == "__main__":
    unittest.main()
