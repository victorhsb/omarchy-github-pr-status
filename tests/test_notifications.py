import copy
import subprocess
import unittest
import test_helper
from unittest.mock import patch

from test_helper import ScriptedAPI, h, pr, search_page


class NotificationTests(unittest.TestCase):
    setUp = test_helper.CacheTests.setUp

    def refresh(self, review="APPROVED", **changes):
        source = pr()
        source.update(reviewDecision=review, mergeable="MERGEABLE", mergeStateStatus="CLEAN")
        source.update(changes)
        self.tick = getattr(self, "tick", 1000) + 70
        return h.run(ScriptedAPI({"viewer": {"login": "alice"}}, search_page([source]), {"nodes": [source]}),
                     h.Cache(self.path), now=lambda: self.tick, notify=True)

    @patch.object(h, "notify_ready")
    def test_review_then_ci_then_ready_once_and_new_cycle(self, send):
        self.refresh("REVIEW_REQUIRED")
        self.refresh(mergeStateStatus="BLOCKED")
        send.assert_not_called()
        self.refresh()
        send.assert_called_once()
        self.refresh()
        send.assert_called_once()
        self.refresh("CHANGES_REQUESTED")
        self.refresh()
        self.assertEqual(send.call_count, 2)

    @patch.object(h, "notify_ready")
    def test_first_seen_approved_is_silent(self, send):
        self.refresh()
        self.refresh()
        send.assert_not_called()

    @patch.object(h, "notify_ready")
    def test_failure_and_recovery_preserve_pending_review(self, send):
        self.refresh("REVIEW_REQUIRED")
        self.tick += 70
        h.run(ScriptedAPI(h.FetchError("offline")), h.Cache(self.path), now=lambda: self.tick, notify=True)
        send.assert_not_called()
        self.refresh()
        send.assert_called_once()

    @patch.object(h, "notify_ready")
    def test_account_change_clears_pending_review(self, send):
        self.refresh("REVIEW_REQUIRED")
        with patch.object(h, "auth_fingerprint", return_value="different"):
            self.refresh()
        send.assert_not_called()

    def test_blockers_and_partial_data_do_not_notify(self):
        row = h.normalize(ScriptedAPI(), dict(pr(), mergeable="MERGEABLE", mergeStateStatus="CLEAN"))
        previous = {"prs": [dict(row, awaitingReady=True)]}
        variants = [dict(row, draft=True), dict(row, error="offline"),
                    dict(row, mergeable="UNKNOWN"), dict(row, mergeable="CONFLICTING"),
                    dict(row, review="Unknown"), dict(row, mergeStateStatus="BEHIND"),
                    dict(row, mergeStateStatus="UNSTABLE"), dict(row, counts=None)]
        for bucket in ("running", "failed", "unknown"):
            variants.append(dict(row, counts=dict(row["counts"], **{bucket: 1})))
        for blocked in variants:
            with self.subTest(blocked=blocked):
                result = {"prs": [blocked]}
                self.assertEqual(h.ready_notifications(result, previous, True), [])
                self.assertTrue(blocked["awaitingReady"])
        for field in ("stale", "partial"):
            self.assertEqual(h.ready_notifications({"prs": [copy.deepcopy(row)], field: True}, previous, True), [])
        self.assertEqual(h.ready_notifications({"prs": [copy.deepcopy(row)]}, previous, False), [])

    def test_notify_escapes_markup_and_uses_bounded_argv(self):
        row = dict(repository="example/project", number=1, title='<b>$(touch /tmp/no)</b>')
        with patch.object(h.subprocess, "run") as run:
            h.notify_ready(row, h.Deadline())
            args, kwargs = run.call_args
            self.assertEqual(args[0][-1], 'example/project #1: &lt;b&gt;$(touch /tmp/no)&lt;/b&gt;')
            self.assertIn("--", args[0])
            self.assertNotIn("shell", kwargs)
            self.assertLessEqual(kwargs["timeout"], 2)
        for failure in (FileNotFoundError(), subprocess.TimeoutExpired("notify-send", 2)):
            with patch.object(h.subprocess, "run", side_effect=failure):
                h.notify_ready(row, h.Deadline())
