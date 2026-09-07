"""Resource-limit regressions use fictional data, fake clocks and local child processes."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from test_helper import h, ScriptedAPI, check, connection, pr, search_page


class Clock:
    def __init__(self):
        self.seconds = 0

    def __call__(self):
        return self.seconds

    def advance(self, seconds):
        self.seconds += seconds


class TimedAPI(ScriptedAPI):
    def __init__(self, clock, advances, *responses):
        super().__init__(*responses)
        self.clock, self.advances, self.timeouts = clock, advances, []

    def graphql(self, query, timeout=35, **variables):
        self.timeouts.append(timeout)
        self.clock.advance(self.advances[len(self.calls)])
        return super().graphql(query, timeout=timeout, **variables)


def check_connection(source, value):
    source["commits"]["nodes"][0]["commit"]["statusCheckRollup"]["contexts"] = value


def check_page(value):
    return {"repository": {"object": {"statusCheckRollup": {"contexts": value}}}}


def review_page(value):
    return {"node": {"reviews": value}}


def snapshot(rows):
    return {"schemaVersion": 1, "prs": rows, "stale": False, "partial": False,
            "error": "", "lastSuccessAt": 25}


class PaginationLimitTests(unittest.TestCase):
    def test_exact_pr_limit_is_complete_only_without_more_results(self):
        rows = [pr(str(i)) for i in range(h.MAX_PRS)]
        for cursor, total, incomplete in ((None, 100, False), ("more", 101, True), (None, 101, True)):
            with self.subTest(cursor=cursor, total=total):
                api = ScriptedAPI(search_page(rows, cursor, total))
                found, reason = h.discover(api, "alice")
                self.assertEqual(len(found), h.MAX_PRS)
                self.assertEqual(bool(reason), incomplete)
                self.assertEqual(len(api.calls), 1)

    def test_duplicate_search_pages_stop_after_ten(self):
        api = ScriptedAPI(*(search_page([pr()], str(i), 1) for i in range(10)))
        found, reason = h.discover(api, "alice")
        self.assertEqual(len(found), 1)
        self.assertTrue(reason)
        self.assertEqual(len(api.calls), 10)

    def test_exact_detail_page_and_entry_limits(self):
        for kind in ("check", "review"):
            for more in (False, True):
                with self.subTest(kind=kind, more=more):
                    source = pr()
                    pages = []
                    for page in range(5):
                        nodes = [check(str(page * 100 + i)) if kind == "check" else
                                 {"id": str(page * 100 + i), "state": "COMMENTED", "comments": {"totalCount": 1}}
                                 for i in range(100)]
                        pages.append(connection(nodes, str(page + 1) if page < 4 or more else None))
                    if kind == "check":
                        check_connection(source, pages[0])
                        api = ScriptedAPI(*(check_page(page) for page in pages[1:]))
                        call = h.check_pages
                    else:
                        source["reviews"] = pages[0]
                        api = ScriptedAPI(*(review_page(page) for page in pages[1:]))
                        call = h.review_pages
                    if more:
                        with self.assertRaises(h.LimitError):
                            call(api, source)
                    else:
                        result = call(api, source)
                        self.assertEqual(len(result) if kind == "check" else result, 500)
                    self.assertEqual(len(api.calls), 4)

    def test_duplicate_pending_and_empty_pages_consume_budget(self):
        for kind in ("check", "review", "empty"):
            with self.subTest(kind=kind):
                source = pr()
                nodes = [check()] * 100 if kind == "check" else ([{"id": "pending", "state": "PENDING", "comments": {"totalCount": 99}}] * 100 if kind == "review" else [])
                pages = [connection(nodes, str(i)) for i in range(5)]
                if kind == "check":
                    check_connection(source, pages[0])
                    api = ScriptedAPI(*(check_page(page) for page in pages[1:]))
                    call = h.check_pages
                else:
                    source["reviews"] = pages[0]
                    api = ScriptedAPI(*(review_page(page) for page in pages[1:]))
                    call = h.review_pages
                with self.assertRaises(h.LimitError):
                    call(api, source)
                self.assertEqual(len(api.calls), 4)

    def test_entry_cap_is_independent_of_page_cap(self):
        for kind in ("check", "review"):
            source = pr()
            if kind == "check":
                check_connection(source, connection([check(str(i)) for i in range(100)], "more"))
                call = h.check_pages
            else:
                source["reviews"] = connection([{"id": str(i), "state": "PENDING"} for i in range(100)], "more")
                call = h.review_pages
            api = ScriptedAPI()
            with patch.object(h, "MAX_ENTRIES", 100), self.assertRaises(h.LimitError):
                call(api, source)
            self.assertEqual(api.calls, [])

    def test_oversized_page_does_not_retain_extra_entries(self):
        source = pr()
        check_connection(source, connection([check(str(i)) for i in range(101)]))
        with self.assertRaises(h.LimitError):
            h.check_pages(ScriptedAPI(), source)
        source["reviews"] = connection([{"id": str(i), "state": "PENDING"} for i in range(101)])
        with self.assertRaises(h.LimitError):
            h.review_pages(ScriptedAPI(), source)

    def test_capped_details_keep_old_or_unknown_never_partial_success(self):
        old = h.normalize(ScriptedAPI(), pr())
        rows = [pr(), pr("new")]
        for source in rows:
            source["reviews"] = connection([], "more")
        with patch.object(h, "MAX_DETAIL_PAGES", 1):
            result = h.snapshot(ScriptedAPI(search_page(rows), {"nodes": rows}), "alice", snapshot([old]))
        self.assertTrue(result["partial"])
        self.assertTrue(result["stale"])
        self.assertEqual(result["lastSuccessAt"], 25)
        self.assertEqual(result["prs"][0]["checks"], old["checks"])
        self.assertEqual(result["prs"][0]["fetchedAt"], old["fetchedAt"])
        self.assertIsNone(result["prs"][1]["counts"])
        self.assertIsNone(result["prs"][1]["inline"])
        self.assertTrue(all(row["error"] for row in result["prs"]))


class DeadlineTests(unittest.TestCase):
    def test_auth_and_rate_limit_errors_keep_priority_at_deadline(self):
        for kind in ("auth", "rate_limit"):
            clock = Clock()
            api = TimedAPI(clock, [60], h.FetchError("failure", kind))
            with self.subTest(kind=kind), self.assertRaises(h.FetchError) as raised:
                h.BudgetAPI(api, h.Deadline(clock)).graphql("query")
            self.assertEqual(raised.exception.kind, kind)

    def test_request_timeout_uses_remaining_global_budget(self):
        clock = Clock()
        api = TimedAPI(clock, [40, 1], {}, {})
        limited = h.BudgetAPI(api, h.Deadline(clock))
        limited.graphql("first")
        limited.graphql("second")
        self.assertEqual(api.timeouts, [35, 20])
        clock.seconds = 60
        with self.assertRaises(h.FetchError) as raised:
            limited.graphql("never")
        self.assertEqual(raised.exception.kind, "deadline")
        self.assertEqual(len(api.calls), 2)

    def test_deadline_during_discovery_keeps_discovered_unknown_rows(self):
        clock = Clock()
        api = TimedAPI(clock, [0, 60], search_page([pr()], "next"), search_page([pr("late")]))
        result = h.snapshot(h.BudgetAPI(api, h.Deadline(clock)), "alice", {})
        self.assertEqual([row["id"] for row in result["prs"]], ["p1"])
        self.assertIsNone(result["prs"][0]["checks"])
        self.assertTrue(result["partial"])
        self.assertEqual(len(api.calls), 2)

    def test_deadline_during_later_batch_preserves_completed_rows(self):
        rows = [pr(str(i)) for i in range(21)]
        clock = Clock()
        api = TimedAPI(clock, [0, 0, 60], search_page(rows), {"nodes": rows[:20]}, {"nodes": rows[20:]})
        result = h.snapshot(h.BudgetAPI(api, h.Deadline(clock)), "alice", {})
        self.assertEqual(len(result["prs"]), 21)
        self.assertTrue(all(row["counts"] is not None for row in result["prs"][:20]))
        self.assertIsNone(result["prs"][20]["counts"])
        self.assertTrue(result["partial"])
        self.assertEqual(len(api.calls), 3)

    def test_deadline_in_nested_check_or_review_stops_remaining_requests(self):
        for kind in ("check", "review"):
            with self.subTest(kind=kind):
                rows = [pr(str(i)) for i in range(21)]
                if kind == "check":
                    check_connection(rows[1], connection([check()], "more"))
                    response = check_page(connection([check("late")]))
                else:
                    rows[1]["reviews"] = connection([], "more")
                    response = review_page(connection([]))
                old = h.normalize(ScriptedAPI(), pr("1"))
                clock = Clock()
                api = TimedAPI(clock, [0, 0, 60], search_page(rows), {"nodes": rows[:20]}, response)
                result = h.snapshot(h.BudgetAPI(api, h.Deadline(clock)), "alice", snapshot([old]))
                self.assertFalse(result["prs"][0]["error"])
                self.assertEqual(result["prs"][1]["fetchedAt"], old["fetchedAt"])
                self.assertTrue(all(row["error"] for row in result["prs"][1:]))
                self.assertEqual(result["lastSuccessAt"], 25)
                self.assertEqual(len(api.calls), 3)

    def test_lock_deadline_does_not_write_or_request(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = h.Cache(Path(directory))
            original = snapshot([h.normalize(ScriptedAPI(), pr())])
            original["authFingerprint"] = h.auth_fingerprint()
            cache.write(original)
            before = (Path(directory) / "snapshot.json").read_bytes()
            clock, api = Clock(), ScriptedAPI()
            with patch.object(h.fcntl, "flock", side_effect=BlockingIOError), patch.object(h.time, "sleep", side_effect=clock.advance):
                result = h.run(api, cache, monotonic=clock)
            self.assertTrue(result["partial"])
            self.assertEqual(result["prs"], original["prs"])
            self.assertEqual(api.calls, [])
            self.assertIsNone(cache.fd)
            self.assertEqual((Path(directory) / "snapshot.json").read_bytes(), before)

    def test_viewer_deadline_preserves_timestamp_and_backs_off(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = h.Cache(Path(directory))
            old = snapshot([h.normalize(ScriptedAPI(), pr())])
            old.update(authFingerprint=h.auth_fingerprint(), attemptedAt=1)
            cache.write(old)
            clock = Clock()
            api = TimedAPI(clock, [60], {"viewer": {"login": "alice"}})
            result = h.run(api, cache, now=lambda: 100, monotonic=clock)
            self.assertEqual(result["prs"], old["prs"])
            self.assertEqual(result["lastSuccessAt"], 25)
            self.assertTrue(result["partial"])
            self.assertGreater(result["retryAt"], 100)
            self.assertEqual(len(api.calls), 1)


class StringAndSnapshotTests(unittest.TestCase):
    def test_text_limits_preserve_boundary_and_flag_truncation(self):
        for extra in (0, 1):
            source = pr()
            source["title"] = "t" * (h.MAX_TEXT + extra)
            node = check("n" * (h.MAX_TEXT + extra))
            node["id"] = "check"
            node["conclusion"] = "s" * (h.MAX_STATUS + extra)
            check_connection(source, connection([node]))
            result = h.snapshot(ScriptedAPI(search_page([source]), {"nodes": [source]}), "alice", {"lastSuccessAt": 25})
            row = result["prs"][0]
            self.assertEqual(len(row["title"]), h.MAX_TEXT)
            self.assertEqual(len(row["checks"][0]["name"]), h.MAX_TEXT)
            self.assertEqual(len(row["checks"][0]["status"]), h.MAX_STATUS)
            self.assertEqual(result["partial"], bool(extra))
            if extra:
                self.assertTrue(row["title"].endswith("…"))
                self.assertEqual(result["lastSuccessAt"], 25)

    def test_oversized_identity_fields_are_rejected_not_truncated(self):
        for field in ("id", "url", "repository", "author"):
            source = pr()
            if field == "repository":
                source[field]["nameWithOwner"] = "r" * (h.MAX_NAME + 1)
            elif field == "author":
                source[field]["login"] = "a" * (h.MAX_NAME + 1)
            else:
                source[field] = "x" * (h.MAX_REFERENCE + 1)
            with self.subTest(field=field), self.assertRaises(h.LimitError):
                h.basic_row(source)
        with self.assertRaises(h.LimitError):
            h.next_cursor(connection([], "c" * (h.MAX_REFERENCE + 1)))
        self.assertEqual(h.reference("x" * h.MAX_REFERENCE), "x" * h.MAX_REFERENCE)
        source = pr()
        source["id"] = "x" * (h.MAX_REFERENCE + 1)
        found, reason = h.discover(ScriptedAPI(search_page([source])), "alice")
        self.assertEqual(found, [])
        self.assertTrue(reason)

    def test_actual_encoded_bytes_include_escaping_and_newline(self):
        rows = [h.normalize(ScriptedAPI(), pr(str(i))) for i in range(3)]
        for i, row in enumerate(rows):
            row["title"] = '🚀"\\' * 100
            row["updatedAt"] = str(i)
        value = snapshot(rows)
        required = len(h.encoded_snapshot(value))
        with patch.object(h, "MAX_SNAPSHOT_BYTES", required):
            self.assertFalse(h.finalize(value)["partial"])
        with patch.object(h, "MAX_SNAPSHOT_BYTES", required - 1):
            result = h.finalize(value, 12)
            encoded = h.encoded_snapshot(result)
            self.assertLessEqual(len(encoded), required - 1)
            self.assertTrue(encoded.endswith(b"\n"))
            self.assertTrue(result["partial"])
            self.assertTrue(result["stale"])
            self.assertEqual(result["lastSuccessAt"], 12)
            self.assertEqual([row["id"] for row in result["prs"]], ["2", "1"])
            self.assertEqual(json.loads(encoded), result)
            self.assertIn("size limit", result["error"])

    def test_production_byte_limit_bounds_cache_and_public_snapshot(self):
        rows = []
        for i in range(8):
            row = h.normalize(ScriptedAPI(), pr(str(i)))
            row["checks"] = [{"name": "🚀" * 512, "status": "SUCCESS", "bucket": "success"} for _ in range(500)]
            row["counts"]["success"] = 500
            rows.append(row)
        result = h.finalize(snapshot(rows), 12)
        self.assertTrue(result["partial"])
        self.assertLess(len(result["prs"]), 8)
        for public in (False, True):
            self.assertLessEqual(len(h.encoded_snapshot(result, public=public)), 2 * 1024 * 1024)

    def test_invalid_cached_structures_are_rejected(self):
        value = snapshot([h.normalize(ScriptedAPI(), pr())])
        mutations = [lambda v: v.update(prs=[v["prs"][0]] * 101),
                     lambda v: v["prs"][0].update(title="x" * 513),
                     lambda v: v["prs"][0].update(checks=v["prs"][0]["checks"] * 501),
                     lambda v: v["prs"][0]["checks"][0].update(bucket="unexpected"),
                     lambda v: v["prs"][0]["counts"].update(success=50),
                     lambda v: v.update(extra={"untrusted": [1, 2, 3]}),
                     lambda v: v.update(fetchedAt=float("nan")),
                     lambda v: v.update(retryAt=None),
                     lambda v: v.update(schemaVersion=True),
                     lambda v: v["prs"][0].update(draft="false")]
        with tempfile.TemporaryDirectory() as directory:
            cache = h.Cache(Path(directory))
            for mutate in mutations:
                bad = copy.deepcopy(value)
                mutate(bad)
                (Path(directory) / "snapshot.json").write_text(json.dumps(bad))
                self.assertEqual(cache.read(), {})
                clean = h.finalize(bad)
                self.assertEqual(clean["prs"], [])
                self.assertTrue(clean["partial"])

    def test_oversized_cache_is_rejected_before_json_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = h.Cache(Path(directory))
            path = Path(directory) / "snapshot.json"
            path.write_bytes(b" " * (h.MAX_SNAPSHOT_BYTES + 1))
            with patch.object(h.json, "loads", side_effect=AssertionError("must not parse")):
                self.assertEqual(cache.read(), {})
            path.unlink()
            os.mkfifo(path)
            self.assertEqual(cache.read(), {})

    def test_legacy_cache_cannot_bypass_limits_on_hit_or_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            cache = h.Cache(path)
            old = snapshot([h.normalize(ScriptedAPI(), pr())])
            old.update(authFingerprint=h.auth_fingerprint(), attemptedAt=time.time(), retryAt=time.time() + 900)
            old["prs"][0]["title"] = "x" * (h.MAX_SNAPSHOT_BYTES + 1)
            (path / "snapshot.json").write_text(json.dumps(old))
            api = ScriptedAPI(h.FetchError("offline", "network"))
            result = h.run(api, cache)
            self.assertEqual(len(api.calls), 1)
            self.assertEqual(result["prs"], [])
            self.assertLessEqual((path / "snapshot.json").stat().st_size, h.MAX_SNAPSHOT_BYTES)
            self.assertEqual(json.loads(h.encoded_snapshot(result, public=True))["prs"], [])
            cached = h.run(ScriptedAPI(), h.Cache(path), force=True)
            self.assertEqual(cached, result)


class ProcessLimitTests(unittest.TestCase):
    def test_cli_emits_bounded_json_on_fresh_fetch_and_cache_hit(self):
        rows = [pr(str(i)) for i in range(4)]
        for source in rows:
            checks = [dict(check(str(i)), name="🚀" * 512) for i in range(100)]
            check_connection(source, connection(checks))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            fixtures = path / "fixtures.json"
            fixtures.write_text(json.dumps({"viewer": {"viewer": {"login": "alice"}},
                                           "search": search_page(rows), "details": {"nodes": rows}}))
            fake_gh = path / "gh"
            fake_gh.write_text(f"#!{sys.executable}\n"
                               "import json, pathlib, sys\n"
                               "query = json.load(sys.stdin)['query']\n"
                               f"fixtures = json.loads(pathlib.Path({str(fixtures)!r}).read_text())\n"
                               "key = 'viewer' if 'viewer' in query else 'search' if 'search(' in query else 'details'\n"
                               "print(json.dumps({'data': fixtures[key]}))\n")
            fake_gh.chmod(0o700)
            env = dict(os.environ, PATH=str(path) + os.pathsep + os.environ.get("PATH", ""),
                       XDG_CACHE_HOME=str(path / "cache"), GH_CONFIG_DIR=str(path / "gh-config"))
            for key in ("GH_TOKEN", "GITHUB_TOKEN"):
                env.pop(key, None)
            command = [sys.executable, str(Path(h.__file__).resolve())]
            first = subprocess.run(command, env=env, capture_output=True, timeout=10, check=True)
            result = json.loads(first.stdout)
            self.assertEqual(first.stderr, b"")
            self.assertTrue(result["partial"])
            self.assertTrue(result["stale"])
            self.assertIsNone(result["lastSuccessAt"])
            self.assertEqual(len(result["prs"]), 3)
            self.assertLessEqual(len(first.stdout), h.MAX_SNAPSHOT_BYTES)
            self.assertNotIn("authFingerprint", result)
            cache_file = path / "cache/omarchy-github-pr-status/snapshot.json"
            self.assertLessEqual(cache_file.stat().st_size, h.MAX_SNAPSHOT_BYTES)
            self.assertEqual(cache_file.stat().st_mode & 0o777, 0o600)
            # A failing gh proves the second invocation really uses the safe cache.
            fake_gh.write_text(f"#!{sys.executable}\nraise SystemExit(99)\n")
            second = subprocess.run(command, env=env, capture_output=True, timeout=10, check=True)
            self.assertEqual(second.stdout, first.stdout)

    def execute(self, code, request="{}", timeout=3):
        return h.bounded_process([sys.executable, "-c", code], request, timeout, dict(os.environ))

    def test_exact_stream_limits_and_simultaneous_pipe_draining(self):
        with patch.object(h, "MAX_STDOUT_BYTES", 8192), patch.object(h, "MAX_STDERR_BYTES", 8192):
            result = self.execute("import os; os.write(2, b'e' * 8192); os.write(1, b'o' * 8192)")
        self.assertEqual(len(result.stdout), 8192)
        self.assertEqual(len(result.stderr), 8192)
        self.assertEqual(result.returncode, 0)

    def test_each_overflow_kills_and_reaps_child(self):
        original = subprocess.Popen
        for fd in (1, 2):
            children = []
            def launch(*args, **kwargs):
                child = original(*args, **kwargs)
                children.append(child)
                return child
            with self.subTest(fd=fd), patch.object(h, "MAX_STDOUT_BYTES", 1024), patch.object(h, "MAX_STDERR_BYTES", 1024), patch.object(h.subprocess, "Popen", side_effect=launch):
                with self.assertRaises(h.LimitError):
                    self.execute(f"import os,time; os.write({fd}, b'x' * 1025); time.sleep(30)")
                self.assertIsNotNone(children[0].poll())
                self.assertNotEqual(children[0].returncode, 0)

    def test_timeout_includes_blocked_stdin_and_reaps_child(self):
        original, children = subprocess.Popen, []
        def launch(*args, **kwargs):
            child = original(*args, **kwargs)
            children.append(child)
            return child
        started = time.monotonic()
        with patch.object(h.subprocess, "Popen", side_effect=launch):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.execute("import time; time.sleep(30)", request="x" * 100000, timeout=0.1)
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNotNone(children[0].poll())


if __name__ == "__main__":
    unittest.main()
