import copy
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location("helper", Path(__file__).resolve().parents[1] / "bin/github_pr_status.py")
h = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(h)


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def check(name="unit", state="SUCCESS", kind="CheckRun"):
    if kind == "StatusContext":
        return {"id": name, "__typename": kind, "context": name, "state": state}
    return {"id": name, "__typename": kind, "name": name, "status": "COMPLETED", "conclusion": state}


def pr(identifier="p1", state="OPEN", author="alice", draft=False):
    return {
        "id": identifier, "number": 1, "title": "A <plain> title", "url": "https://github.com/example/project/pull/1",
        "repository": {"nameWithOwner": "example/project"}, "author": {"login": author},
        "state": state, "isDraft": draft, "updatedAt": "2026-09-06T00:00:00Z",
        "reviewDecision": "APPROVED", "comments": {"totalCount": 3},
        "reviews": connection([{"id": "review", "state": "APPROVED", "comments": {"totalCount": 4}}]),
        "commits": {"nodes": [{"commit": {"oid": "abc123", "statusCheckRollup": {"contexts": connection([check()])}}}]},
    }


class ScriptedAPI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def graphql(self, query, **variables):
        self.calls.append((query, variables))
        if not self.responses:
            raise AssertionError("Unexpected API request: " + query)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)


def search_page(items, cursor=None, total=None):
    return {"search": dict(connection(items, cursor), issueCount=len(items) if total is None else total)}


def concurrent_refresh(path, calls):
    class API:
        def graphql(self, query, **variables):
            with calls.get_lock():
                calls.value += 1
            time.sleep(0.05)
            return {"viewer": {"login": "alice"}} if "viewer" in query else search_page([])
    h.run(API(), h.Cache(Path(path)), force=True)


class HelperTests(unittest.TestCase):
    def test_discovery_pagination_dedup_scope_and_drafts(self):
        first, draft = pr(), pr("p2", draft=True)
        draft["updatedAt"] = "2026-09-06T02:00:00Z"
        api = ScriptedAPI(search_page([first, pr("closed", state="CLOSED"), pr("other", author="bob"), None], "next"), search_page([first, draft]))
        result, partial = h.discover(api, "alice")
        self.assertEqual([p["id"] for p in result], ["p2", "p1"])
        self.assertTrue(result[0]["isDraft"])
        self.assertFalse(partial)
        self.assertEqual(api.calls[1][1]["cursor"], "next")
        self.assertEqual(api.calls[0][1]["q"], "is:pr is:open author:alice sort:updated-desc")

    def test_search_cap_is_explicit(self):
        result, partial = h.discover(ScriptedAPI(search_page([pr()], total=1001)), "alice")
        self.assertTrue(partial)
        self.assertEqual(len(result), 1)

    def test_nonadvancing_cursor_is_error(self):
        api = ScriptedAPI(search_page([pr()], "same"), search_page([pr()], "same"))
        with self.assertRaises(h.FetchError):
            h.discover(api, "alice")

    def test_every_check_category_and_original_state(self):
        for status, expected in {"SUCCESS": "success", "SKIPPED": "skipped", "CANCELLED": "skipped", "NEUTRAL": "skipped", "FAILURE": "failed", "TIMED_OUT": "failed", "ACTION_REQUIRED": "failed", "STARTUP_FAILURE": "failed", "NEW_STATE": "unknown", None: "unknown"}.items():
            with self.subTest(status=status):
                self.assertEqual(h.bucket(check(state=status)), expected)
        for state in ("QUEUED", "WAITING", "PENDING", "IN_PROGRESS", "REQUESTED"):
            self.assertEqual(h.bucket(dict(check(), status=state, conclusion=None)), "running")
        for state, expected in {"ERROR": "failed", "FAILURE": "failed", "PENDING": "running", "SUCCESS": "success"}.items():
            self.assertEqual(h.bucket(check(state=state, kind="StatusContext")), expected)

    def test_checks_follow_pages_pinned_to_original_head(self):
        source = pr()
        source["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = connection([check()], "next")
        api = ScriptedAPI({"repository": {"object": {"statusCheckRollup": {"contexts": connection([check(), check("lint", "CANCELLED"), check("external", "ERROR", "StatusContext")])}}}})
        row = h.normalize(api, source)
        self.assertEqual(len(row["checks"]), 3)
        self.assertEqual(row["counts"], {"running": 0, "success": 1, "skipped": 1, "failed": 1, "unknown": 0})
        self.assertEqual(row["checks"][1]["status"], "CANCELLED")
        self.assertEqual(api.calls[0][1]["oid"], "abc123")

    def test_comment_totals_separate_submitted_from_pending(self):
        source = pr()
        # Review totals include replies and resolved/outdated comments; review bodies are not requested.
        source["reviews"] = connection([
            {"id": "resolved", "state": "DISMISSED", "comments": {"totalCount": 4}},
            {"id": "pending", "state": "PENDING", "comments": {"totalCount": 50}},
        ], "next")
        api = ScriptedAPI({"node": {"reviews": connection([
            {"id": "resolved", "state": "DISMISSED", "comments": {"totalCount": 4}},
            {"id": "reply", "state": "COMMENTED", "comments": {"totalCount": 2}},
            {"id": "approval-only", "state": "APPROVED", "comments": {"totalCount": 0}},
        ])}})
        row = h.normalize(api, source)
        self.assertEqual(row["discussion"], 3)
        self.assertEqual(row["inline"], 6)
        self.assertIn("after:$cursor", api.calls[0][0])

    def test_review_states(self):
        for decision, expected in {"APPROVED": "Approved", "CHANGES_REQUESTED": "Changes requested", "REVIEW_REQUIRED": "Review required", None: "No review decision", "NEW": "Unknown"}.items():
            source = pr()
            source["reviewDecision"] = decision
            self.assertEqual(h.normalize(ScriptedAPI(), source)["review"], expected)

    def test_no_checks_is_distinct_from_missing_head(self):
        source = pr()
        source["commits"]["nodes"][0]["commit"]["statusCheckRollup"] = None
        self.assertEqual(h.normalize(ScriptedAPI(), source)["checks"], [])
        source["commits"]["nodes"] = []
        with self.assertRaises(h.FetchError):
            h.normalize(ScriptedAPI(), source)

    def test_failed_detail_retains_old_counts_and_timestamp(self):
        old = h.normalize(ScriptedAPI(), pr())
        api = ScriptedAPI(search_page([pr(), pr("new")]), h.FetchError("Offline", "network"))
        result = h.snapshot(api, "alice", {"prs": [old], "lastSuccessAt": 12})
        self.assertTrue(result["partial"])
        self.assertEqual(result["prs"][0]["discussion"], 3)
        self.assertEqual(result["prs"][0]["fetchedAt"], old["fetchedAt"])
        self.assertIsNone(result["prs"][1]["counts"])
        self.assertIsNone(result["prs"][1]["inline"])
        self.assertEqual(result["lastSuccessAt"], 12)

    def test_nested_failure_does_not_turn_into_success(self):
        source = pr()
        source["reviews"]["pageInfo"] = {"hasNextPage": True, "endCursor": "r2"}
        result = h.snapshot(ScriptedAPI(search_page([source]), {"nodes": [source]}, h.FetchError("offline")), "alice", {})
        self.assertTrue(result["partial"])
        self.assertIsNone(result["prs"][0]["checks"])

    def test_closed_during_fetch_and_closed_since_last_refresh_are_removed(self):
        result = h.snapshot(ScriptedAPI(search_page([pr()]), {"nodes": [pr(state="MERGED")]}), "alice", {"prs": [h.basic_row(pr("old"))]})
        self.assertEqual(result["prs"], [])
        self.assertFalse(result["partial"])

    def test_details_are_batched(self):
        items = [pr(str(i)) for i in range(21)]
        api = ScriptedAPI(search_page(items), {"nodes": items[:20]}, {"nodes": items[20:]})
        result = h.snapshot(api, "alice", {})
        self.assertEqual(len(result["prs"]), 21)
        self.assertEqual([len(call[1]["ids"]) for call in api.calls[1:]], [20, 1])

    def test_rate_limit_stops_remaining_batches(self):
        items = [pr(str(i)) for i in range(21)]
        api = ScriptedAPI(search_page(items), h.FetchError("rate limited", "rate_limit", 900))
        result = h.snapshot(api, "alice", {})
        self.assertEqual(len(api.calls), 2)
        self.assertEqual(len(result["prs"]), 21)
        self.assertTrue(result["partial"])
        self.assertGreater(result["retryAt"], time.time() + 890)

    def test_gh_uses_stdin_and_never_a_shell(self):
        with patch.object(h.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"data":{"viewer":{"login":"alice"}}}', "")) as run:
            h.GitHub().graphql("query($q:String!) { search(query:$q) { issueCount } }", q='$(touch /tmp/unsafe)')
            args, kwargs = run.call_args
            self.assertNotIn("shell", kwargs)
            self.assertEqual(args[0][-2:], ["--input", "-"])
            self.assertNotIn("touch", " ".join(args[0]))
            self.assertEqual(json.loads(kwargs["input"])["variables"]["q"], '$(touch /tmp/unsafe)')

    def test_cli_failure_classification_and_graphql_partial_errors(self):
        for stdout, stderr, kind in [('', 'gh auth login', 'auth'), ('', 'rate limit exceeded', 'rate_limit'), ('', 'connection refused', 'network'), ('{"data":{"nodes":[]},"errors":[{"message":"forbidden"}]}', '', 'api')]:
            with patch.object(h.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout, stderr)):
                with self.assertRaises(h.FetchError) as raised:
                    h.GitHub().graphql("query {}")
                self.assertEqual(raised.exception.kind, kind)


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "cache"
        self.addCleanup(self.temp.cleanup)

    def initial(self):
        return h.run(ScriptedAPI({"viewer": {"login": "alice"}}, search_page([pr()]), {"nodes": [pr()]}), h.Cache(self.path))

    def test_cache_reuses_fetch_and_has_private_permissions(self):
        first = self.initial()
        second = h.run(ScriptedAPI(), h.Cache(self.path))
        self.assertEqual(first, second)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.path / "snapshot.json").stat().st_mode & 0o777, 0o600)

    def test_network_failure_retains_snapshot_and_backs_off_even_when_forced(self):
        original = self.initial()
        later = original["attemptedAt"] + 70
        result = h.run(ScriptedAPI(h.FetchError("offline", "network")), h.Cache(self.path), now=lambda: later)
        self.assertEqual(result["prs"], original["prs"])
        self.assertTrue(result["stale"])
        self.assertGreater(result["retryAt"], later)
        cached = h.run(ScriptedAPI(), h.Cache(self.path), force=True, now=lambda: later + 10)
        self.assertEqual(cached, result)

    def test_auth_failure_clears_private_data(self):
        original = self.initial()
        result = h.run(ScriptedAPI(h.FetchError("login", "auth")), h.Cache(self.path), now=lambda: original["attemptedAt"] + 70)
        self.assertEqual(result["prs"], [])
        self.assertNotIn("account", result)

    def test_account_switch_cannot_retain_other_accounts_prs(self):
        original = self.initial()
        result = h.run(ScriptedAPI({"viewer": {"login": "bob"}}, h.FetchError("offline")), h.Cache(self.path), now=lambda: original["attemptedAt"] + 70)
        self.assertEqual(result["prs"], [])

    def test_credential_change_invalidates_cached_response(self):
        self.initial()
        with patch.object(h, "auth_fingerprint", return_value="new-account"):
            result = h.run(ScriptedAPI(h.FetchError("offline")), h.Cache(self.path))
        self.assertEqual(result["prs"], [])

    def test_corrupt_cache_and_symlink_directory(self):
        cache = h.Cache(self.path)
        (self.path / "snapshot.json").write_text("bad-json")
        self.assertEqual(cache.read(), {})
        alias = self.path.parent / "alias"
        alias.symlink_to(self.path, target_is_directory=True)
        with self.assertRaises(h.FetchError):
            h.Cache(alias)

    def test_multiple_monitors_coalesce_manual_refresh(self):
        context = multiprocessing.get_context("fork")
        calls = context.Value("i", 0)
        processes = [context.Process(target=concurrent_refresh, args=(str(self.path), calls)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(calls.value, 2)
        self.assertEqual(h.Cache(self.path).read()["prs"], [])


if __name__ == "__main__":
    unittest.main()
