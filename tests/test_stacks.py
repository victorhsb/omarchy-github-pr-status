import unittest

from test_helper import h, pr, ScriptedAPI, search_page


class StackTests(unittest.TestCase):
    def row(self, identifier, position=None, number=7, repository="example/project", updated="2026-09-09T01:00:00Z"):
        source = pr(identifier)
        source["repository"]["nameWithOwner"] = repository
        source["updatedAt"] = updated
        source["stackEntry"] = None if position is None else {"position": position, "stack": {"number": number, "size": 4}}
        return h.normalize(ScriptedAPI(), source)

    def test_groups_by_repository_and_stack_then_actual_position(self):
        rows = [self.row("top", 4, updated="2026-09-09T03:00:00Z"),
                self.row("single", updated="2026-09-09T02:00:00Z"),
                self.row("base", 1), self.row("other-repo", 2, repository="other/project"),
                self.row("other-stack", 1, number=8)]
        result = h.finalize({"schemaVersion": 1, "prs": rows})
        self.assertEqual([row["id"] for row in result["prs"]], ["base", "top", "single", "other-repo", "other-stack"])
        self.assertEqual(result["prs"][1]["stack"], {"number": 7, "position": 4, "size": 4})
        self.assertEqual(h.finalize(result), result)

    def test_old_cache_and_standalone_remain_valid(self):
        row = self.row("old")
        del row["stack"]
        self.assertEqual(h.finalize({"schemaVersion": 1, "prs": [row]})["prs"], [row])
        self.assertIsNone(self.row("standalone")["stack"])

    def test_invalid_stack_metadata_is_rejected(self):
        for bad in ({"number": 0, "position": 1, "size": 2},
                    {"number": 1, "position": 0, "size": 2},
                    {"number": 1, "position": 3, "size": 2},
                    {"number": True, "position": 1, "size": 2},
                    {"number": 1, "position": 1, "size": "2"}):
            with self.subTest(bad=bad):
                row = self.row("bad")
                row["stack"] = bad
                self.assertEqual(h.finalize({"schemaVersion": 1, "prs": [row]})["prs"], [])

    def test_detail_failure_preserves_stack_and_marks_stale(self):
        old = self.row("p1", 3)
        result = h.snapshot(ScriptedAPI(search_page([pr()]), h.FetchError("offline")), "alice",
                            {"schemaVersion": 1, "prs": [old]})
        self.assertTrue(result["stale"])
        self.assertEqual(result["prs"][0]["stack"], old["stack"])

    def test_removing_from_stack_clears_previous_membership(self):
        result = h.snapshot(ScriptedAPI(search_page([pr()]), {"nodes": [dict(pr(), stackEntry=None)]}),
                            "alice", {"schemaVersion": 1, "prs": [self.row("p1", 3)]})
        self.assertIsNone(result["prs"][0]["stack"])
        self.assertFalse(result["stale"])
