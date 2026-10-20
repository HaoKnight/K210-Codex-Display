import json
from pathlib import Path
import socket
import ssl
import tempfile
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import MagicMock, patch

from bridge.sources import (LogObserver, fetch_json, geocode_city, locate_city,
                            resolve_codex_binary, weather_error_message)


class CodexBinaryTests(unittest.TestCase):
    def test_desktop_app_is_found_without_a_terminal_path(self):
        for relative in (
            'Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex',
            'Applications/Codex.app/Contents/Resources/codex',
        ):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory() as tmp:
                binary = Path(tmp) / relative
                binary.parent.mkdir(parents=True)
                binary.write_text('#!/bin/sh\n')
                binary.chmod(0o755)
                with patch('bridge.sources.shutil.which', return_value=None), \
                        patch('bridge.sources.Path.home', return_value=Path(tmp)), \
                        patch.object(Path, 'is_file', autospec=True,
                                     side_effect=lambda p: p == binary):
                    self.assertEqual(resolve_codex_binary(), str(binary.resolve()))

    def test_path_binary_wins_and_explicit_missing_path_does_not_fall_back(self):
        with patch('bridge.sources.shutil.which', return_value='/custom/bin/codex'):
            self.assertEqual(resolve_codex_binary(), '/custom/bin/codex')
            with patch.object(Path, 'is_file', return_value=False), self.assertRaises(FileNotFoundError):
                resolve_codex_binary('/missing/codex')

    def test_discovery_rejects_non_executable_files(self):
        with patch('bridge.sources.shutil.which', return_value=None), \
                patch.object(Path, 'is_file', return_value=True), \
                patch('bridge.sources.os.access', return_value=False), \
                self.assertRaises(FileNotFoundError):
            resolve_codex_binary()


class WeatherErrorTests(unittest.TestCase):
    def test_errors_explain_network_failure_without_exposing_raw_details(self):
        cases = (
            (URLError(TimeoutError('SECRET')), '超时'),
            (URLError(socket.gaierror(-2, 'SECRET')), '域名解析'),
            (URLError(ssl.SSLCertVerificationError(1, 'SECRET')), '证书验证'),
            (URLError(ssl.SSLError(1, 'SECRET')), 'HTTPS 连接'),
            (URLError(ConnectionRefusedError('SECRET')), '连接被拒绝'),
            (URLError('SECRET'), '无法连接'),
            (HTTPError('https://SECRET', 503, 'SECRET', {}, None), 'HTTP 503'),
            (ValueError('SECRET'), '服务数据无效'),
        )
        for error, expected in cases:
            with self.subTest(error=error):
                message = weather_error_message(error, '自动定位')
                self.assertTrue(message.startswith('自动定位失败：'))
                self.assertIn(expected, message)
                self.assertNotIn('SECRET', message)


class LogObserverTests(unittest.TestCase):
    def test_unchanged_files_stay_closed_and_append_truncation_rotation_are_read(self):
        def event(status):
            return json.dumps({'timestamp': '2026-10-08T01:00:00Z',
                              'type': 'event_msg', 'payload': {'type': status}}) + '\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sessions' / 'test.jsonl'
            path.parent.mkdir()
            observer = LogObserver(tmp)
            record = event('task_started') + '\n'
            path.write_text(record[:-5])
            observer.poll()
            with patch.object(Path, 'open', side_effect=AssertionError('unchanged file opened')):
                observer.poll()
            with path.open('a') as f:
                f.write(record[-5:])
            observer.poll()
            self.assertEqual(observer.activity[path]['status'], 'RUNNING')
            path.write_text(event('turn_aborted'))
            observer.poll()
            self.assertEqual(observer.activity[path]['status'], 'IDLE')
            replacement = path.with_suffix('.tmp')
            replacement.write_text(event('task_complete'))
            replacement.replace(path)
            observer.poll()
            self.assertEqual(observer.activity[path]['status'], 'DONE')


class FetchJsonTests(unittest.TestCase):
    def test_ignores_system_proxy_settings(self):
        direct_handler = object()
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value

        with patch("bridge.sources.urllib.request.ProxyHandler",
                   return_value=direct_handler) as proxy_handler, \
                patch("bridge.sources.urllib.request.build_opener",
                      return_value=opener) as build_opener, \
                patch("bridge.sources.json.load", return_value={"ok": True}) as load:
            result = fetch_json("https://weather.example/api", "test-agent", timeout=3)

        self.assertEqual(result, {"ok": True})
        proxy_handler.assert_called_once_with({})
        build_opener.assert_called_once_with(direct_handler)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://weather.example/api")
        opener.open.assert_called_once_with(request, timeout=3)
        load.assert_called_once_with(response)


class LocateCityTests(unittest.TestCase):
    def test_uses_ipwho_is_and_localizes_city_name(self):
        location = {"success": True, "city": "Tianjin",
                    "latitude": 39.1422, "longitude": 117.1767}
        geocode = {"results": [{"name": "天津", "latitude": 39, "longitude": 117}]}

        with patch("bridge.sources.fetch_json",
                   side_effect=(location, geocode)) as fetch:
            result = locate_city()

        self.assertEqual(result, {"city": "天津", "latitude": 39.1422,
                                  "longitude": 117.1767})
        self.assertEqual(fetch.call_args_list[0].args[0], "https://ipwho.is/?lang=zh-CN")

    def test_chinese_ip_name_needs_no_geocoding(self):
        location = {"success": True, "city": "济宁", "latitude": 35.4, "longitude": 116.6}
        with patch("bridge.sources.fetch_json", return_value=location) as fetch:
            self.assertEqual(locate_city()["city"], "济宁")
        self.assertEqual(fetch.call_count, 1)

    def test_untranslated_ip_name_never_reaches_display(self):
        location = {"success": True, "city": "Jining", "latitude": 35.4, "longitude": 116.6}
        for geocode in ({"results": []}, {"results": [{"name": "Jining", "latitude": 35, "longitude": 116}]}):
            with self.subTest(geocode=geocode), patch("bridge.sources.fetch_json", side_effect=(location, geocode)):
                self.assertEqual(locate_city(), {"city": "未知城市", "latitude": 35.4, "longitude": 116.6})
        with patch("bridge.sources.fetch_json", side_effect=(location, OSError("offline"))):
            self.assertEqual(locate_city()["city"], "未知城市")

    def test_rejects_failed_ipwho_is_response(self):
        with patch("bridge.sources.fetch_json",
                   return_value={"success": False, "message": "rate limited"}):
            with self.assertRaisesRegex(ValueError, "自动定位失败"):
                locate_city()


class GeocodeCityTests(unittest.TestCase):
    def test_english_search_returns_chinese_name_and_coordinates(self):
        with patch("bridge.sources.fetch_json", return_value={"results": [{
                "name": "伦敦", "latitude": 51.5, "longitude": -0.12}]}):
            self.assertEqual(geocode_city("London"), {"city": "伦敦", "latitude": 51.5, "longitude": -0.12})

    def test_chinese_query_survives_missing_translation(self):
        with patch("bridge.sources.fetch_json", return_value={"results": [{
                "name": "Jining", "latitude": 35.4, "longitude": 116.6}]}):
            self.assertEqual(geocode_city("济宁")["city"], "济宁")

    def test_english_or_mixed_name_is_not_saved_as_chinese(self):
        for name in ("Jining", "济宁 Jining"):
            with self.subTest(name=name), patch("bridge.sources.fetch_json", return_value={"results": [{
                    "name": name, "latitude": 35.4, "longitude": 116.6}]}):
                with self.assertRaisesRegex(ValueError, "中文"):
                    geocode_city("Jining")


if __name__ == "__main__":
    unittest.main()
