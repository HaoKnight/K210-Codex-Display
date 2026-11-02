import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from bridge.app import Bridge, RESET_HITBOX, handler_for, load_config
from bridge.device_support.protocol import decode, valid_reset
from bridge.reset_feed import decode_snapshot
from test_device_info import ns
from test_display_refresh import Canvas
from test_reset_feed import NOW, event, instant


def packet():
    return dict(v=1, type="state", seq=1, ts=NOW, offset=480, city="南京", temp=26,
                condition="多云", weather_stale=False, status="RUNNING", model="gpt-6-sol",
                q5=88, q7=97, r5=NOW + 3600, r7=NOW + 172800, stale=False, demo=True,
                show_codex=True, reset=dict(type="reset_credit", status="announced",
                precision="window", **{"from": NOW + 3600, "through": NOW + 7200},
                expires_at=NOW + 7200, confirmed_at=None, checked_at=NOW, stale=False))


class ResetDisplayTests(unittest.TestCase):
    def test_seen_confirmation_hides_only_home_badge_and_preserves_details(self):
        for kind in ("reset_credit", "direct_reset"):
            p = packet()
            p["reset"].update(type=kind, status="confirmed", confirmed_at=NOW, key="a" * 32, seen=False)
            unread = Canvas()
            ns["EmbeddedDisplay"](unread).render(p, NOW)
            p["reset"]["seen"] = True
            seen = Canvas()
            ns["EmbeddedDisplay"](seen).render(p, NOW)
            model = Canvas()
            ns["EmbeddedDisplay"](model).render(dict(p, reset=None), NOW)
            self.assertEqual(seen.image.tobytes(), model.image.tobytes())
            self.assertNotEqual(unread.image.tobytes(), seen.image.tobytes())
            detail = Canvas()
            ns["EmbeddedDisplay"](detail).render_reset(p, NOW)
            p["reset"]["seen"] = False
            original = Canvas()
            ns["EmbeddedDisplay"](original).render_reset(p, NOW)
            self.assertEqual(detail.image.tobytes(), original.image.tobytes())

    def test_device_read_survives_settings_changes_and_reports_to_host(self):
        p = packet()
        p["reset"].update(status="confirmed", confirmed_at=NOW, key="b" * 32, seen=False)
        with patch.dict(ns, make_boot_id=lambda: 1):
            receiver = ns["SerialStateReceiver"]()
        self.assertFalse(receiver.view_reset(p, "dashboard", NOW))
        self.assertTrue(receiver.view_reset(p, "reset", NOW))
        self.assertTrue(p["reset"]["seen"])
        self.assertFalse(receiver.view_reset(p, "reset", NOW))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'settings.json')
            self.assertTrue(ns["save_device_settings"](False, 100, path, reset_seen=receiver.reset_seen))
            ns["save_flip_setting"](True, path)
            ns["save_brightness_setting"](70, True, path)
            self.assertEqual(ns["load_device_settings"](path)["reset_seen"], receiver.reset_seen)
            with patch.dict(ns, make_boot_id=lambda: 2):
                restarted = ns["SerialStateReceiver"]()
            restarted.reset_seen = ns["load_device_settings"](path)["reset_seen"]
            p["reset"]["seen"] = False
            self.assertFalse(restarted.view_reset(p, "dashboard", NOW))
            self.assertTrue(p["reset"]["seen"])
        class UART:
            def write(self, data):
                self.message = json.loads(data)
        uart = UART()
        receiver.report_ui(uart, False, 'reset', 100)
        self.assertEqual(uart.message['reset_seen'], 'b' * 32)
        p["reset"].update(key="c" * 32, seen=False)
        self.assertFalse(receiver.view_reset(p, "dashboard", NOW))
        self.assertFalse(p["reset"]["seen"])
        p["reset"].update(status="announced")
        self.assertFalse(receiver.view_reset(p, "reset", NOW))
        p["reset"].update(status="confirmed", expires_at=NOW - 1)
        self.assertFalse(receiver.view_reset(p, "reset", NOW))

    def test_countdown_precision_arrival_and_expiry(self):
        p = packet()
        display = ns["EmbeddedDisplay"](Canvas())
        self.assertEqual(display.reset_message(p, NOW)["badge"], "发卡1h00m")
        self.assertIn("开始", display.reset_message(p, NOW)["relative"])
        self.assertEqual(display.reset_message(p, NOW + 3600)["badge"], "发卡时段")
        self.assertIsNone(display.reset_message(p, NOW + 7201))
        p["reset"]["precision"] = "deadline"
        self.assertEqual(display.reset_message(p, NOW)["badge"], "发卡2h00m")
        p["reset"]["precision"] = "date"
        self.assertIn("/", display.reset_message(p, NOW)["badge"])
        self.assertIn("仅公布日期", display.reset_message(p, NOW)["relative"])
        p["reset"].update(precision=None, **{"from": None, "through": None})
        self.assertEqual(display.reset_message(p, NOW)["badge"], "发卡待定")
        p["reset"].update(status="confirmed", confirmed_at=NOW)
        self.assertEqual(display.reset_message(p, NOW)["badge"], "发卡确认")

    def test_stale_news_warns_but_keeps_countdown(self):
        p = packet()
        display = ns["EmbeddedDisplay"](Canvas())
        p["reset"]["stale"] = True
        message = display.reset_message(p, NOW)
        self.assertEqual(message["badge"], "发卡1h00m")
        self.assertTrue(message["stale"])
        self.assertEqual(message["color"], ns["MUTED"])

    def test_details_share_settings_back_button_and_update_connection_dot(self):
        p = packet()
        canvas = Canvas()
        display = ns["EmbeddedDisplay"](canvas)
        display.render_reset(p, online=True, live=True)
        settings = Canvas()
        ns["EmbeddedDisplay"](settings).render_settings(False)
        self.assertEqual(canvas.image.crop((12, 8, 48, 44)).tobytes(),
                         settings.image.crop((12, 8, 48, 44)).tobytes())
        for online in (False, True):
            display.render_reset(p, online=online, live=True)
            self.assertEqual(canvas.image.getpixel((296, 26)),
                             ns["GREEN"] if online else ns["RED"])
            expected = Canvas()
            ns["EmbeddedDisplay"](expected).render_reset(p, online=online)
            self.assertEqual(canvas.image.tobytes(), expected.image.tobytes())

    def test_details_center_each_message_row_and_keep_only_source_footer(self):
        labels = []

        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, color=ns["WHITE"], scale=1, **kwargs):
                labels.append((x, y, str(value), scale))
                return super().text(x, y, value, color, scale=scale, **kwargs)

        display = Capture(Canvas())
        p = packet()
        for reset in (p["reset"], dict(p["reset"], status="confirmed", confirmed_at=NOW), None):
            p["reset"] = reset
            labels.clear()
            display.render_reset(p)
            centered = [row for row in labels if 64 <= row[1] < 208]
            self.assertEqual(len(centered), 3 if reset else 1)
            for x, y, value, scale in centered:
                width = display.text_width(value, scale)
                self.assertLessEqual(abs(x + width / 2 - 160), 0.5)
                self.assertGreaterEqual(x, 16)
                self.assertLessEqual(x + width, 304)
            footer = [value for x, y, value, scale in labels if y >= 208]
            self.assertEqual(len(footer), 2)
            self.assertEqual(footer[0], "来源 AIhot 公开消息")
            self.assertNotIn("AIhot", [value for x, y, value, scale in labels if y < 52])
            if reset is None:
                self.assertEqual(centered[0][1],
                                 display.centered_text_y(centered[0][2], 136, 21 / 12))

    def test_detail_fonts_are_larger_and_all_time_formats_fit(self):
        labels = []
        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, color=ns["WHITE"], scale=1, **kwargs):
                labels.append((x, y, str(value), color, scale))
                return super().text(x, y, value, color, scale=scale, **kwargs)
        display = Capture(Canvas())
        for kind in ("reset_credit", "direct_reset"):
            for precision in ("window", "exact", "approximate", "deadline", "date", None):
                for status in ("announced", "confirmed"):
                    p = packet()
                    p["reset"].update(type=kind, status=status, confirmed_at=NOW if status == "confirmed" else None,
                                      precision=precision, **{"from": NOW + 3600 if precision else None,
                                                             "through": NOW + 90000 if precision else None})
                    labels.clear()
                    display.render_reset(p, NOW)
                    previous_bottom = 64
                    for x, y, value, color, scale in [row for row in labels if 64 <= row[1] < 208]:
                        self.assertGreaterEqual(scale, 1.25)
                        bank = ns["SMOOTH_FONTS"][str(int(12 * scale))]
                        for char in value:
                            self.assertIn(char, bank, (value, char, scale))
                        self.assertGreaterEqual(y, previous_bottom)
                        previous_bottom = y + max(bank[char][1] for char in value)
                        self.assertLessEqual(previous_bottom, 208)
                        self.assertGreaterEqual(x, 16)
                        self.assertLessEqual(x + display.text_width(value, scale), 304)
                    body = [row for row in labels if 120 <= row[1] < 208]
                    self.assertEqual([row[4] for row in body], [21 / 12, 21 / 12])
                    self.assertTrue(all(row[3] == ns["GREEN" if status == "confirmed" else "AMBER"] for row in body))
                    footer = [row for row in labels if row[1] >= 208]
                    self.assertEqual([row[4] for row in footer], [1.25, 1.25])
                    self.assertLessEqual(footer[0][0] + display.text_width(footer[0][2], 1.25), footer[1][0])

    def test_status_is_the_largest_heading_and_repaints_when_message_changes(self):
        labels = []
        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, color=ns["WHITE"], scale=1, **kwargs):
                labels.append((x, y, value, color, scale))
                return super().text(x, y, value, color, scale=scale, **kwargs)
        p = packet(); canvas = Canvas(); display = Capture(canvas)
        for changes, heading, color in (({}, "发放预告", ns["AMBER"]),
                ({"type": "direct_reset"}, "重置预告", ns["AMBER"]),
                ({"status": "confirmed", "confirmed_at": NOW}, "公开确认", ns["GREEN"]),
                ({"stale": True}, "公开确认", ns["MUTED"]),
                ({"expires_at": NOW - 1}, "暂无消息", ns["MUTED"])):
            p["reset"].update(changes)
            labels.clear(); display.render_reset(p, NOW, live=True)
            header = next(row for row in labels if row[1] < 52)
            self.assertEqual(header[2:5], (heading, color, 25 / 12))
            self.assertGreater(header[4], max(row[4] for row in labels if row[1] >= 64))
            self.assertNotIn("重置信息", [row[2] for row in labels])
            self.assertEqual(sum(row[2] == heading for row in labels), 1)
            expected = Canvas(); ns["EmbeddedDisplay"](expected).render_reset(p, NOW)
            self.assertEqual(canvas.image.tobytes(), expected.image.tobytes())

    def test_detail_digits_align_and_fonts_append_without_adding_startup_strings(self):
        bank = ns["SMOOTH_FONTS"]["21"]
        bottoms = []
        for char in "确认录0123456789":
            width, height, pixels = bank[char]
            bottoms.append(max(row for row in range(height) if any(
                (pixels[(row * width + col) // 4] >> (((row * width + col) % 4) * 2)) & 3
                for col in range(width))))
        self.assertLessEqual(max(bottoms) - min(bottoms), 1)
        for size in ("21", "25"):
            self.assertFalse(ns["SMOOTH_FONTS"][size].extra)
            self.assertTrue(ns["SMOOTH_FONTS"][size].characters)
        banks = ns["SMOOTH_FONTS"]
        old_end = max(font._offset(len(font.characters)) for size, font in banks.items()
                      if size not in ("21", "25"))
        self.assertEqual(banks["21"]._offset(0), old_end)
        self.assertEqual(banks["25"]._offset(0), banks["21"]._offset(len(banks["21"].characters)))

    def test_new_labels_have_glyphs_and_badges_fit_the_model_slot(self):
        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, color=ns["WHITE"], scale=1, max_width=320, **kwargs):
                bank = ns["SMOOTH_FONTS"][str(int(12 * scale))]
                for char in str(value):
                    self_test.assertIn(char, bank, (str(value), scale, char))
                if y >= 214 and str(value).startswith(("发卡", "重置")):
                    self_test.assertEqual(scale, 1.25)
                    self_test.assertGreaterEqual(x, 214)
                    self_test.assertLessEqual(x + self.text_width(value, scale), 312)
                return super().text(x, y, value, color, scale, max_width, **kwargs)
        self_test = self
        p = packet()
        display = Capture(Canvas())
        for precision in ("window", "exact", "approximate", "date", "deadline", None):
            for status in ("announced", "confirmed"):
                p["reset"].update(precision=precision, status=status,
                                  **{"from": NOW + 3600 if precision else None,
                                     "through": NOW + 7200 if precision else None})
                display.render(p)
                display.render_reset(p)
        for minutes in range(1440):
            p["reset"].update(precision="window", status="announced",
                              **{"from": NOW + minutes * 60, "through": NOW + 86400})
            message = display.reset_message(p, NOW)
            self.assertLessEqual(display.text_width(message["badge"], 1.25), 98)
        p["reset"] = None
        display.render_reset(p)

    def test_larger_model_text_and_long_statuses_stay_in_separate_slots(self):
        labels = []

        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, color=ns["WHITE"], scale=1, **kwargs):
                if y >= 214:
                    labels.append((x, y, value, color, scale, kwargs))
                return super().text(x, y, value, color, scale=scale, **kwargs)

        display = Capture(Canvas())
        p = dict(packet(), reset=None)
        for status in ("RUNNING", "UNKNOWN", "OFFLINE", "DONE"):
            for model in ("gpt-6.1-sol", "gpt-6-astra", "codex-auto-review"):
                labels.clear()
                display.render(dict(p, status=status, model=model))
                prefix, caption, activity = labels
                self.assertEqual(caption[3:5], (ns["WHITE"], 1.25))
                left, right = caption[5]['clip']
                visible_left = max(left, caption[0])
                visible_right = min(right, caption[0] + display.text_width(caption[2], caption[4]))
                self.assertLessEqual(prefix[0] + display.text_width(prefix[2], prefix[4]), activity[0])
                self.assertLessEqual(activity[0] + display.text_width(activity[2], activity[4]), visible_left)
                self.assertGreaterEqual(visible_left, 214)
                self.assertLessEqual(visible_right, 312)
                self.assertEqual(caption[2], model)
                for char in caption[2]:
                    self.assertIn(char, ns["SMOOTH_FONTS"]["15"])

    def test_incremental_home_and_details_match_full_after_changes(self):
        p = packet()
        canvas = Canvas()
        display = ns["EmbeddedDisplay"](canvas)
        for page in ("home", "details"):
            for seconds, changes in ((0, {}), (60, {}), (120, {"stale": True}),
                                     (121, {"status": "confirmed", "confirmed_at": NOW + 121}),
                                     (7201, {})):
                p["reset"].update(changes)
                render = display.render_live if page == "home" else display.render_reset
                if page == "home":
                    render(p, NOW + seconds)
                else:
                    render(p, NOW + seconds, live=True)
                expected = Canvas()
                reference = ns["EmbeddedDisplay"](expected)
                (reference.render if page == "home" else reference.render_reset)(p, NOW + seconds)
                self.assertEqual(canvas.image.tobytes(), expected.image.tobytes(), (page, seconds))

    def test_both_receivers_validate_optional_reset_data(self):
        p = packet()
        self.assertIsNotNone(decode(json.dumps(p).encode()))
        self.assertTrue(ns["SerialStateReceiver"].valid(p))
        for key, value in (("type", "unknown"), ("stale", "false"), ("expires_at", True),
                           ("through", NOW - 1), ("checked_at", float("inf")),
                           ("key", "invalid"), ("key", "z" * 32), ("seen", "false")):
            bad = copy.deepcopy(p)
            bad["reset"][key] = value
            self.assertFalse(valid_reset(bad["reset"]))
            self.assertFalse(ns["SerialStateReceiver"].valid(bad))
            self.assertIsNone(decode(json.dumps(bad).encode()))
        del p["reset"]
        self.assertTrue(ns["SerialStateReceiver"].valid(p))
        self.assertIsNotNone(decode(json.dumps(p).encode()))


class ResetBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = load_config()
        config.update(source="demo", serial_port="")
        self.bridge = Bridge(config, Path(self.tmp.name) / "config.json")

    def test_confirmed_message_is_seen_after_preview_touch_and_survives_restart(self):
        self.bridge.config["source"] = "logs"
        for kind in ("reset_credit", "direct_reset"):
            with self.subTest(kind=kind), patch("bridge.app.time.time", return_value=NOW):
                raw = event(id=kind, type=kind, status="confirmed", confirmedAt=instant(NOW))
                self.bridge.reset_feed.events = decode_snapshot(json.dumps({"events": [raw]}))
                self.bridge.reset_feed.last_success = NOW
                self.bridge.tick(send=False, poll=False)
                self.assertFalse(self.bridge.read()["packet"]["reset"]["seen"])
                details = self.bridge.device_touch(280, 227)
                self.assertTrue(details["packet"]["reset"]["seen"])
                returned = self.bridge.device_touch(20, 20)
                self.assertEqual(returned["device_page"], "dashboard")
                self.assertTrue(returned["packet"]["reset"]["seen"])
                saved = load_config(self.bridge.config_path)
                self.assertEqual(saved["reset_seen"], returned["packet"]["reset"]["key"])
                restarted = Bridge(saved, self.bridge.config_path)
                restarted.reset_feed.events = self.bridge.reset_feed.events
                restarted.reset_feed.last_success = NOW
                restarted.tick(send=False, poll=False)
                self.assertTrue(restarted.read()["packet"]["reset"]["seen"])

    def test_device_read_report_only_acknowledges_the_matching_confirmation(self):
        self.bridge.config["source"] = "logs"
        with patch("bridge.app.time.time", return_value=NOW):
            self.bridge.reset_feed.events = decode_snapshot(json.dumps({"events": [
                event(status="confirmed", confirmedAt=instant(NOW))]}))
            self.bridge.tick(send=False, poll=False)
            key = self.bridge.read()["packet"]["reset"]["key"]
            self.bridge.link._accept_device_ui({"reset_seen": "a" * 32, "show_codex": True})
            self.assertFalse(self.bridge.apply_device_settings())
            self.assertTrue(self.bridge.link._accept_device_ui({"reset_seen": key, "show_codex": True}))
            self.assertTrue(self.bridge.apply_device_settings())
            self.bridge.tick(send=False, poll=False)
            self.assertTrue(self.bridge.read()["packet"]["reset"]["seen"])
            self.assertEqual(load_config(self.bridge.config_path)["reset_seen"], key)

    def test_simulated_read_is_isolated_and_new_confirmation_alerts_again(self):
        before = copy.deepcopy(self.bridge.config)
        with patch("bridge.app.time.time", return_value=NOW), patch.object(self.bridge.link, "exchange") as send:
            request = dict(mode="confirmed", type="reset_credit", confirmed_at=NOW)
            self.bridge.set_reset_preview(request)
            self.bridge.device_touch(280, 227)
            state = self.bridge.device_touch(20, 20)
            self.assertTrue(state["reset_preview"]["event"]["seen"])
            state = self.bridge.set_reset_preview(request)
            self.assertTrue(state["reset_preview"]["event"]["seen"])
            state = self.bridge.set_reset_preview(dict(request, confirmed_at=NOW + 60))
            self.assertFalse(state["reset_preview"]["event"]["seen"])
            self.bridge.set_reset_preview(dict(mode="announced", type="reset_credit", precision="exact",
                                               **{"from": NOW + 3600, "through": NOW + 3600}))
            self.bridge.device_touch(280, 227)
            state = self.bridge.device_touch(20, 20)
            self.assertFalse(state["reset_preview"]["event"]["seen"])
            send.assert_not_called()
        self.assertEqual(self.bridge.config, before)
        self.assertFalse(self.bridge.config_path.exists())

    def test_preview_touch_opens_details_and_returns_without_changing_quota(self):
        before = self.bridge.read()
        detail = self.bridge.device_touch(280, 227)
        self.assertEqual(detail["device_page"], "reset")
        self.assertNotEqual(detail["frame_version"], before["frame_version"])
        self.assertEqual(detail["packet"]["q5"], before["packet"]["q5"])
        self.assertEqual(self.bridge.device_touch(20, 20)["device_page"], "dashboard")

    def test_larger_reset_entry_routes_the_same_in_live_and_simulated_preview(self):
        self.assertEqual(RESET_HITBOX, ns["RESET_HITBOX"])
        for simulated in (False, True):
            if simulated:
                self.bridge.set_reset_preview({"mode": "empty"})
            for x,y in ((215,180),(240,155),(316,200)):
                with self.subTest(simulated=simulated, outside=(x,y)):
                    state = self.bridge.device_touch(x,y)
                    page = state["reset_preview"].get("page") if simulated else state["device_page"]
                    self.assertEqual(page,"dashboard")
            for x,y in ((216,156),(239,158),(315,239),(280,227)):
                with self.subTest(simulated=simulated, inside=(x,y)):
                    state = self.bridge.device_touch(x,y)
                    page = state["reset_preview"].get("page") if simulated else state["device_page"]
                    self.assertEqual(page,"reset")
                    self.bridge.device_touch(20,20)

    def test_turning_news_off_persists_and_clears_badge_without_network(self):
        with patch("bridge.reset_feed.urlopen") as fetch:
            self.bridge.save_config({"reset_news_enabled": False})
            self.bridge.refresh_reset(force=True)
            fetch.assert_not_called()
        self.assertFalse(load_config(self.bridge.config_path)["reset_news_enabled"])
        self.assertIsNone(self.bridge.read()["packet"]["reset"])
        self.bridge.device_touch(280, 227)
        self.assertEqual(self.bridge.link.device_page, "reset")
        self.assertEqual(self.bridge.device_touch(20, 20)["device_page"], "dashboard")
        with self.assertRaises(ValueError):
            self.bridge.save_config({"reset_news_enabled": "false"})
        self.assertFalse(self.bridge.quota_wake.is_set())
        self.assertFalse(self.bridge.weather_wake.is_set())

    def test_model_slot_opens_empty_reset_details_without_fetching(self):
        self.bridge.config["source"] = "logs"
        with patch.object(self.bridge.observer, "current_activity", return_value={
                "status": "IDLE", "model": "gpt-6-sol"}), \
             patch("bridge.reset_feed.urlopen") as fetch:
            self.bridge.tick(send=False, poll=False)
            before = self.bridge.read()
            self.assertIsNone(before["packet"]["reset"])
            self.assertEqual(before["packet"]["model"], "gpt-6-sol")
            detail = self.bridge.device_touch(280, 227)
            self.assertEqual(detail["device_page"], "reset")
            self.assertIsNone(detail["packet"]["reset"])
            self.assertNotEqual(detail["frame_version"], before["frame_version"])
            self.assertEqual(self.bridge.device_touch(20, 20)["device_page"], "dashboard")
            fetch.assert_not_called()

    def test_news_is_independent_of_quota_route_and_packet_stays_small(self):
        self.bridge.config["source"] = "logs"
        with self.bridge.reset_feed.lock:
            self.bridge.reset_feed.events = decode_snapshot(json.dumps({"events": [event()]}))
            self.bridge.reset_feed.last_success = NOW
        with patch("bridge.app.time.time", return_value=NOW):
            self.bridge.tick(send=False, poll=False)
            state = self.bridge.read(include_frame=False)
        self.assertIsNotNone(state["packet"]["reset"])
        self.assertIn("source_url", state["reset_news"]["event"])
        self.assertNotIn("source_url", state["packet"]["reset"])
        self.assertLessEqual(len(json.dumps(state["packet"], ensure_ascii=True).encode()) + 1, 2048)

    def test_countdown_uses_local_clock_without_per_second_packet_changes(self):
        packets = []
        for offset in (0, 1):
            with patch("bridge.app.time.time", return_value=self.bridge.demo_started_at + offset):
                self.bridge.tick(send=False, poll=False)
                packets.append(self.bridge.read(include_frame=False)["packet"])
        for p in packets:
            for field in ("seq", "ts", "time_sync"):
                p.pop(field)
        self.assertEqual(packets[0], packets[1])

    def test_content_preview_never_replaces_live_data_or_sends_to_device(self):
        self.bridge.link.set_device_ui(page="settings")
        self.bridge.tick(send=False, poll=False)
        before = self.bridge.read()
        config = copy.deepcopy(self.bridge.config)
        preview = dict(mode="announced", type="direct_reset", precision="window",
                       **{"from": NOW + 3600, "through": NOW + 7200})
        with patch("bridge.app.time.time", return_value=NOW), \
             patch("bridge.reset_feed.urlopen") as fetch, \
             patch.object(self.bridge.link, "exchange", side_effect=lambda p, *args: p) as send:
            state = self.bridge.set_reset_preview(preview)
            self.assertEqual(state["packet"], before["packet"])
            self.assertEqual(state["reset_news"], before["reset_news"])
            self.assertEqual(state["serial"], before["serial"])
            self.assertEqual(state["reset_preview"]["event"]["type"], "direct_reset")
            self.assertNotEqual(state["frame_version"], before["frame_version"])
            send.assert_not_called()
            self.bridge.tick(poll=False)
            self.assertEqual(send.call_args[0][0]["reset"]["type"], "reset_credit")
            self.assertEqual(send.call_args[0][0]["page"], "settings")
            fetch.assert_not_called()
        self.assertEqual(self.bridge.config, config)
        self.assertFalse(self.bridge.config_path.exists())
        state["reset_preview"]["event"]["type"] = "bad"
        self.assertEqual(self.bridge.read()["reset_preview"]["event"]["type"], "direct_reset")

    def test_content_preview_switches_states_and_countdown_keeps_running(self):
        with patch("bridge.app.time.time", return_value=NOW):
            state = self.bridge.set_reset_preview(dict(mode="announced", type="reset_credit",
                precision="window", **{"from": NOW + 3600, "through": NOW + 7200}))
        first_frame = state["frame_version"]
        with patch("bridge.app.time.time", return_value=NOW + 60):
            self.bridge.tick(send=False, poll=False)
            state = self.bridge.read()
        self.assertNotEqual(state["frame_version"], first_frame)
        message = ns["EmbeddedDisplay"](Canvas()).reset_message(
            dict(state["packet"], reset=state["reset_preview"]["event"]), NOW + 60)
        self.assertEqual(message["badge"], "发卡59m")
        confirmed = self.bridge.set_reset_preview(dict(mode="confirmed", type="direct_reset",
                                                      confirmed_at=NOW))
        self.assertNotEqual(confirmed["frame_version"], state["frame_version"])
        self.assertTrue(valid_reset(confirmed["reset_preview"]["event"]))
        empty = self.bridge.set_reset_preview({"mode": "empty"})
        self.assertNotEqual(empty["frame_version"], confirmed["frame_version"])
        self.assertIsNone(empty["reset_preview"]["event"])
        restored = self.bridge.set_reset_preview({"mode": "live"})
        self.assertEqual(restored["reset_preview"]["mode"], "live")

    def test_content_preview_back_keeps_simulation_on_home_without_serial_write(self):
        self.bridge.link.set_device_ui(page="settings")
        self.bridge.tick(send=False, poll=False)
        before = self.bridge.read()
        self.bridge.set_reset_preview({"mode": "empty"})
        with patch.object(self.bridge.link, "exchange") as send:
            state = self.bridge.device_touch(280, 227)
            self.assertEqual(state["reset_preview"]["mode"], "empty")
            state = self.bridge.device_touch(20, 20)
            self.assertEqual(state["reset_preview"]["mode"], "empty")
            self.assertEqual(state["reset_preview"]["page"], "dashboard")
            state = self.bridge.device_touch(280, 227)
            self.assertEqual(state["reset_preview"]["page"], "reset")
            send.assert_not_called()
        self.assertEqual(state["serial"]["device_page"], "settings")
        self.assertEqual(state["packet"], before["packet"])

    def test_preview_switch_starts_on_home_and_content_updates_keep_current_page(self):
        self.bridge.link.set_device_ui(page="settings")
        self.bridge.tick(send=False, poll=False)
        before = self.bridge.read()
        state = self.bridge.set_reset_preview(dict(mode="confirmed", type="direct_reset",
                                                  confirmed_at=NOW))
        self.assertEqual(state["reset_preview"]["page"], "dashboard")
        self.bridge.device_touch(280, 227)
        state = self.bridge.set_reset_preview(dict(mode="confirmed", type="reset_credit",
                                                  confirmed_at=NOW))
        self.assertEqual(state["reset_preview"]["page"], "reset")
        self.assertEqual(self.bridge.read()["reset_preview"], state["reset_preview"])
        restored = self.bridge.set_reset_preview({"mode": "live"})
        self.assertEqual(restored["reset_preview"], {"mode": "live", "event": None})
        self.assertEqual(restored["frame_version"], before["frame_version"])
        self.assertEqual(restored["packet"], before["packet"])
        self.assertEqual(self.bridge.set_reset_preview({"mode": "empty"})["reset_preview"]["page"],
                         "dashboard")

    def test_simulated_message_stays_fresh_until_preview_is_switched_off(self):
        messages = []

        class Capture(ns["EmbeddedDisplay"]):
            def render(self, p, **kwargs):
                messages.append(self.reset_message(p, p["ts"]))
                return super().render(p, **kwargs)

        with patch("bridge.app.time.time", return_value=NOW):
            self.bridge.set_reset_preview(dict(mode="confirmed", type="direct_reset",
                                               confirmed_at=NOW))
        with patch("bridge.app.time.time", return_value=NOW + 3 * 86400), \
             patch.object(self.bridge.renderer, "display_type", return_value=Capture):
            self.bridge.tick(send=False, poll=False)
            live_event = copy.deepcopy(self.bridge.snapshot["packet"]["reset"])
            state = self.bridge.read()
        self.assertEqual(messages[-1]["badge"], "重置确认")
        self.assertFalse(messages[-1]["stale"])
        self.assertEqual(state["reset_preview"]["event"]["checked_at"], NOW)
        self.assertEqual(state["packet"]["reset"], live_event)

    def test_content_preview_home_badges_match_message_even_when_device_hides_codex(self):
        labels = []

        class Capture(ns["EmbeddedDisplay"]):
            def text(self, x, y, value, *args, **kwargs):
                if x >= 212 and y >= 214:
                    labels.append(value)
                return super().text(x, y, value, *args, **kwargs)

        self.bridge.link.set_device_ui(page="settings", show_codex=False)
        with patch("bridge.app.time.time", return_value=NOW), \
             patch.object(self.bridge.renderer, "display_type", return_value=Capture), \
             patch.object(self.bridge.link, "exchange") as send:
            self.bridge.tick(send=False, poll=False)
            before = self.bridge.read()
            for payload, badge in ((dict(mode="announced", type="reset_credit", precision="window",
                                        **{"from": NOW + 3600, "through": NOW + 7200}), "发卡1h00m"),
                                   (dict(mode="confirmed", type="direct_reset", confirmed_at=NOW), "重置确认")):
                labels.clear()
                state = self.bridge.set_reset_preview(dict(payload, page="dashboard"))
                self.assertIn(badge, labels)
                self.assertEqual(state["packet"], before["packet"])
                self.assertFalse(state["packet"]["show_codex"])
                state = self.bridge.device_touch(280, 227)
                self.assertEqual(state["reset_preview"]["page"], "reset")
                state = self.bridge.device_touch(20, 20)
                self.assertEqual(state["reset_preview"]["page"], "dashboard")
                self.assertEqual(state["reset_preview"]["event"]["type"], payload["type"])
            send.assert_not_called()

    def test_invalid_content_preview_keeps_previous_state(self):
        before = self.bridge.set_reset_preview({"mode": "empty"})
        scheduled = dict(mode="announced", type="reset_credit", precision="window",
                         **{"from": NOW + 3600, "through": NOW + 7200})
        bad = [None, [], {}, {"mode": []}, {"mode": "bad"}, {"mode": "empty", "text": "x"},
               {"mode": "empty", "page": "settings"}, {"mode": "empty", "page": []},
               {"mode": "live", "page": "dashboard"},
               dict(scheduled, type="bad"), dict(scheduled, precision="bad"),
               dict(scheduled, **{"from": True}), dict(scheduled, through=NOW),
               dict(scheduled, through=float("inf")),
               dict(mode="confirmed", type="reset_credit", confirmed_at=None)]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                self.bridge.set_reset_preview(request)
            self.assertEqual(self.bridge.read()["reset_preview"], before["reset_preview"])

    def test_content_preview_http_validation_and_hidden_preview_state(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(self.bridge))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = "http://127.0.0.1:%d" % server.server_port
        try:
            with urlopen(Request(base + "/api/reset-preview", data=b'{"mode":"empty"}',
                                 headers={"Content-Type": "application/json"})) as response:
                state = json.load(response)["state"]
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(state["reset_preview"]["mode"], "empty")
            with urlopen(base + "/api/state?preview=0") as response:
                hidden = json.load(response)
                self.assertEqual(hidden["reset_preview"]["mode"], "empty")
                self.assertNotIn("frame", hidden)
                self.assertEqual(hidden["packet"], state["packet"])
            for data, mime, status in ((b"{}", "text/plain", 415),
                                       (b"bad", "application/json", 400),
                                       (b"[]", "application/json", 400),
                                       (b"x" * 1025, "application/json", 400)):
                with self.subTest(data=data[:20]), self.assertRaises(HTTPError) as caught:
                    urlopen(Request(base + "/api/reset-preview", data=data,
                                    headers={"Content-Type": mime}))
                self.assertEqual(caught.exception.code, status)
            self.assertEqual(self.bridge.read()["reset_preview"]["mode"], "empty")
        finally:
            server.shutdown()
            server.server_close()
            worker.join()


if __name__ == "__main__":
    unittest.main()
