#!/usr/bin/env python3
"""Unit tests for the SuperBrain upstream activity tracker."""

import importlib.util
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("track_upstream_activity.py")
SPEC = importlib.util.spec_from_file_location("track_upstream_activity", MODULE_PATH)
tracker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(tracker)


def pull(number, *, state="open", merged=False, events=None, updated_at=None):
    return {
        "repo": "sidinsearch/superbrain",
        "number": number,
        "label": f"PR {number}",
        "title": f"PR {number}",
        "url": f"https://github.com/sidinsearch/superbrain/pull/{number}",
        "state": state,
        "draft": False,
        "merged": merged,
        "updated_at": updated_at or "2026-07-31T12:00:00Z",
        "events": events or {},
    }


def issue(number, *, state="open", events=None, updated_at=None):
    return {
        "repo": "sidinsearch/superbrain",
        "number": number,
        "label": f"Issue {number}",
        "title": f"Issue {number}",
        "url": f"https://github.com/sidinsearch/superbrain/issues/{number}",
        "state": state,
        "updated_at": updated_at or "2026-07-31T12:00:00Z",
        "events": events or {},
    }


def snapshot(pulls, *, issues=None, meta=None, biome_ready=True, biome_open=False):
    return {
        "version": 1,
        "collected_at": "2026-07-31T13:00:00+00:00",
        "pulls": pulls,
        "issues": issues or [],
        "biome_branch_ready": biome_ready,
        "biome_pr_open": biome_open,
        "meta": meta or {},
    }


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 31, 13, tzinfo=timezone.utc)

    def test_first_scheduled_run_establishes_silent_baseline(self):
        current = snapshot([pull(4), pull(5)])
        notify, message = tracker.compare_snapshots(
            None, current, self.now, force=False, scheduled=True
        )
        self.assertFalse(notify)
        self.assertIn("Wait for upstream review", message)

    def test_external_comment_requests_attention(self):
        old = snapshot(
            [pull(4), pull(5)],
            meta={"last_action_key": "waiting", "last_stale_nudges": {}},
        )
        event = {
            "kind": "comment",
            "author": "sidinsearch",
            "author_type": "User",
            "url": "https://example.invalid/comment",
            "timestamp": "2026-07-31T12:30:00Z",
            "state": "",
            "fingerprint": "new",
        }
        current = snapshot([pull(4, events={"comment:1": event}), pull(5)])
        notify, message = tracker.compare_snapshots(
            old, current, self.now, force=False, scheduled=True
        )
        self.assertTrue(notify)
        self.assertIn("new comment by @sidinsearch", message)
        self.assertIn("reply if needed", message)

    def test_external_proposal_comment_requests_attention(self):
        old = snapshot(
            [pull(4), pull(5)],
            issues=[issue(6)],
            meta={"last_action_key": "waiting", "last_stale_nudges": {}},
        )
        event = {
            "kind": "comment",
            "author": "sidinsearch",
            "author_type": "User",
            "url": "https://example.invalid/comment",
            "timestamp": "2026-07-31T12:30:00Z",
            "state": "",
            "fingerprint": "new",
        }
        current = snapshot(
            [pull(4), pull(5)],
            issues=[issue(6, events={"comment:1": event})],
        )
        notify, message = tracker.compare_snapshots(
            old, current, self.now, force=False, scheduled=True
        )
        self.assertTrue(notify)
        self.assertIn("Issue #6: new comment by @sidinsearch", message)
        self.assertIn("reply if needed", message)

    def test_waiting_status_includes_proposal(self):
        current = snapshot([pull(4), pull(5)], issues=[issue(6)])
        notify, message = tracker.compare_snapshots(
            None, current, self.now, force=True, scheduled=False
        )
        self.assertTrue(notify)
        self.assertIn("PRs #4, #5 and proposal #6", message)
        self.assertIn("Issue #6", message)

    def test_merged_pr_opens_biome_submission_slot(self):
        old = snapshot(
            [pull(4), pull(5)],
            meta={"last_action_key": "waiting", "last_stale_nudges": {}},
        )
        current = snapshot([pull(4, state="closed", merged=True), pull(5)])
        notify, message = tracker.compare_snapshots(
            old, current, self.now, force=False, scheduled=True
        )
        self.assertTrue(notify)
        self.assertIn("open: rebase and submit prep/biome-tooling", message)

    def test_open_biome_pr_is_included_in_waiting_status(self):
        current = snapshot(
            [pull(4), pull(5), pull(6)],
            biome_open=True,
        )
        notify, message = tracker.compare_snapshots(
            None, current, self.now, force=True, scheduled=False
        )
        self.assertTrue(notify)
        self.assertIn("open PRs #4, #5, #6", message)

    def test_stale_nudge_repeats_only_after_three_days(self):
        stale_time = (self.now - timedelta(days=4)).isoformat()
        old = snapshot(
            [pull(4, updated_at=stale_time), pull(5)],
            meta={
                "last_action_key": "waiting",
                "last_stale_nudges": {"4": (self.now - timedelta(days=1)).isoformat()},
            },
        )
        current = snapshot([pull(4, updated_at=stale_time), pull(5)])
        notify, _ = tracker.compare_snapshots(
            old, current, self.now, force=False, scheduled=True
        )
        self.assertFalse(notify)


if __name__ == "__main__":
    unittest.main()
