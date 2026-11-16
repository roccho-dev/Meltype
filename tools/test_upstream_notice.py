# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 roccho-dev

import copy
import unittest

from upstream_notice import observe


class UpstreamObservationTests(unittest.TestCase):
    def setUp(self):
        self.pin = {"repository": "yksr-melt/Meltype", "tag": "v1.6.0", "commit": "1" * 40}
        self.release = {"tag_name": "v1.6.0", "id": 10, "published_at": "2026-11-14T12:58:01Z", "assets": []}

    def fetch(self, path):
        if path.endswith("/releases/latest"):
            return self.release
        if "/git/ref/tags/" in path:
            return {"object": {"type": "commit", "sha": "1" * 40}}
        self.fail(path)

    def test_new_stable_is_reported_without_mutating_selected_scope(self):
        before = copy.deepcopy(self.pin)
        self.release["tag_name"] = "v1.6.1"
        result = observe(self.pin, self.fetch)
        self.assertEqual("different-stable-review-required", result["status"])
        self.assertEqual(before, self.pin)
        self.assertEqual(before, result["selected"])

    def test_moved_selected_tag_is_not_accepted_as_same_version(self):
        def changed(path):
            return self.release if path.endswith("/releases/latest") else {"object": {"type": "commit", "sha": "2" * 40}}
        with self.assertRaises(ValueError):
            observe(self.pin, changed)

    def test_annotated_release_tag_resolves_to_commit(self):
        def annotated(path):
            if path.endswith("/releases/latest"):
                return self.release
            if "/git/ref/" in path:
                return {"object": {"type": "tag", "sha": "2" * 40}}
            return {"object": {"type": "commit", "sha": "1" * 40}}
        result = observe(self.pin, annotated)
        self.assertEqual("selected-stable", result["status"])
        self.assertEqual(self.pin["commit"], result["observed"]["commit"])


if __name__ == "__main__":
    unittest.main()
