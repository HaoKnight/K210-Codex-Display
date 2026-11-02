import datetime as dt
from io import BytesIO
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

from bridge.reset_feed import ResetFeed, confirmation_key, current_event, decode_snapshot, device_event

NOW = 1793277308


def instant(seconds):
    return dt.datetime.fromtimestamp(seconds, dt.timezone.utc).isoformat()


def event(**changes):
    raw = dict(id="forecast", type="reset_credit", status="announced",
               createdAt=instant(NOW - 60), updatedAt=instant(NOW - 30), confirmedAt=None,
               schedule=dict(precision="window", **{"from": instant(NOW + 3600),
                             "through": instant(NOW + 7200)}),
               posts=[dict(text="公开预告", url="https://x.com/example/status/1")],
               url="https://aihot.news/codex-reset")
    raw.update(changes)
    return raw


def response(*events):
    reply = MagicMock()
    reply.__enter__.return_value = reply
    reply.read.return_value = json.dumps({"events": events}).encode()
    reply.headers = {"ETag": '"version-one"'}
    return reply


class ResetFeedTests(unittest.TestCase):
    def test_confirmation_identity_survives_refresh_but_changes_for_a_new_confirmation(self):
        raw = event(status="confirmed", confirmedAt=instant(NOW), schedule=None)
        first = decode_snapshot(json.dumps({"events": [raw]}))[0]
        refreshed = decode_snapshot(json.dumps({"events": [dict(raw, updatedAt=instant(NOW + 60))]}))[0]
        self.assertEqual(confirmation_key(first), confirmation_key(refreshed))
        self.assertNotEqual(confirmation_key(first), confirmation_key(dict(first, id="new")))
        self.assertNotEqual(confirmation_key(first), confirmation_key(dict(first, confirmed_at=NOW + 60)))
        self.assertEqual(confirmation_key(dict(first, status="announced")), "")

    def test_decode_and_device_summary_keep_public_news_separate(self):
        parsed = decode_snapshot(json.dumps({"events": [event()]}))[0]
        self.assertEqual(parsed["from"], NOW + 3600)
        self.assertEqual(parsed["expires_at"], NOW + 7200)
        summary = device_event(dict(event=parsed, checked_at=NOW, stale=False))
        self.assertEqual(summary["type"], "reset_credit")
        self.assertNotIn("text", summary)
        self.assertNotIn("source_url", summary)
        self.assertLess(len(json.dumps(summary)), 400)

    def test_confirmation_active_and_nearest_future_priority(self):
        raw = [event(id="future"), event(id="later", schedule=dict(precision="window",
                     **{"from": instant(NOW + 4000), "through": instant(NOW + 8000)})),
               event(id="active", schedule=dict(precision="window",
                     **{"from": instant(NOW - 10), "through": instant(NOW + 1800)})),
               event(id="confirmed", status="confirmed", confirmedAt=instant(NOW - 1), schedule=None)]
        parsed = decode_snapshot(json.dumps({"events": raw}))
        self.assertEqual(current_event(parsed, NOW)["id"], "confirmed")
        self.assertEqual(current_event(parsed[:-1], NOW)["id"], "active")
        self.assertEqual(current_event(parsed[:2], NOW)["id"], "future")

    def test_expiry_uses_confirmation_or_update_and_scheduled_end(self):
        raw = [event(), event(id="unplanned", schedule=None),
               event(id="confirmed", status="confirmed", confirmedAt=instant(NOW), schedule=None)]
        planned, unplanned, confirmed = decode_snapshot(json.dumps({"events": raw}))
        self.assertIsNone(current_event([planned], NOW + 7201))
        self.assertIsNotNone(current_event([confirmed], NOW + 86400))
        self.assertIsNone(current_event([confirmed], NOW + 86401))
        self.assertIsNone(current_event([unplanned], NOW + 172800))

    def test_invalid_reply_does_not_manufacture_events(self):
        invalid = [[], {}, {"events": {}}, {"events": [event(type="unknown")]},
                   {"events": [event(updatedAt="bad")]},
                   {"events": [event(schedule=dict(precision="window", **{
                       "from": instant(NOW + 100), "through": instant(NOW)}))]}]
        for raw in invalid:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                decode_snapshot(json.dumps(raw))
        unsafe = event(url="javascript:alert(1)", posts=[dict(url="https://user:pass@example.com/")])
        parsed = decode_snapshot(json.dumps({"events": [unsafe]}))[0]
        self.assertIsNone(parsed["source_url"])
        self.assertIsNone(parsed["original_url"])

    def test_refresh_interval_etag_and_304_refresh_health(self):
        feed = ResetFeed()
        not_modified = HTTPError("https://example.com", 304, "unchanged", {}, BytesIO())
        with patch("bridge.reset_feed.urlopen", side_effect=[response(event()), not_modified]) as fetch:
            self.assertIsNone(feed.refresh(now=NOW))
            feed.refresh(now=NOW + 1)
            self.assertEqual(fetch.call_count, 1)
            feed.refresh(force=True, now=NOW + 60)
            self.assertEqual(fetch.call_args.args[0].get_header("If-none-match"), '"version-one"')
        self.assertEqual(feed.snapshot(NOW + 60)["checked_at"], NOW + 60)
        self.assertFalse(feed.snapshot(NOW + 60)["stale"])
        self.assertEqual(feed.snapshot(NOW + 60)["event"]["id"], "forecast")

    def test_recent_feed_and_manual_cooldown_avoid_full_history_rate_limit(self):
        feed = ResetFeed()
        with patch("bridge.reset_feed.urlopen", return_value=response(event())) as fetch:
            feed.refresh(now=NOW)
            self.assertEqual(fetch.call_args.args[0].full_url,
                             "https://aihot.news/api/v1/codex-resets/recent")
            feed.refresh(force=True, now=NOW + 59)
            self.assertEqual(fetch.call_count, 1)
            feed.refresh(force=True, now=NOW + 60)
            self.assertEqual(fetch.call_count, 2)
            feed.refresh(force=True, now=NOW + 61)
            feed.refresh(now=NOW + 959)
            self.assertEqual(fetch.call_count, 2)
            feed.refresh(now=NOW + 960)
            self.assertEqual(fetch.call_count, 3)

    def test_rate_limit_respects_retry_after_even_on_manual_refresh(self):
        feed = ResetFeed()
        limited = HTTPError("https://example.com", 429, "limited", {"Retry-After": "1200"}, BytesIO())
        with patch("bridge.reset_feed.urlopen", side_effect=[limited, response(event())]) as fetch:
            self.assertIn("受限", feed.refresh(now=NOW))
            self.assertEqual(feed.snapshot(NOW)["retry_at"], NOW + 1200)
            feed.refresh(force=True, now=NOW + 1199)
            self.assertEqual(fetch.call_count, 1)
            self.assertIsNone(feed.refresh(force=True, now=NOW + 1200))
            self.assertEqual(fetch.call_count, 2)
        self.assertIsNone(feed.snapshot(NOW + 1200)["retry_at"])

    def test_failed_and_malformed_snapshots_keep_last_good_event(self):
        feed = ResetFeed()
        broken = response()
        broken.read.return_value = b'{"events":"broken"}'
        with patch("bridge.reset_feed.urlopen", side_effect=[response(event()), URLError("SECRET"), broken]):
            feed.refresh(now=NOW)
            before = feed.snapshot(NOW)
            feed.refresh(force=True, now=NOW + 60)
            self.assertTrue(feed.snapshot(NOW + 60)["stale"])
            self.assertNotIn("SECRET", feed.snapshot(NOW + 60)["error"])
            feed.refresh(force=True, now=NOW + 120)
        self.assertEqual(feed.snapshot(NOW + 120)["event"], before["event"])

    def test_empty_success_clears_snapshot_and_readers_do_not_wait_for_network(self):
        feed = ResetFeed()
        def fetch(*args, **kwargs):
            self.assertTrue(feed.fetching)
            self.assertIsNotNone(feed.snapshot(NOW)["event"])
            return response()
        with patch("bridge.reset_feed.urlopen", return_value=response(event())):
            feed.refresh(now=NOW)
        with patch("bridge.reset_feed.urlopen", side_effect=fetch):
            feed.refresh(force=True, now=NOW + 60)
        self.assertIsNone(feed.snapshot(NOW + 60)["event"])


if __name__ == "__main__":
    unittest.main()
