import unittest
from unittest.mock import MagicMock, patch

from bridge.sources import fetch_json, geocode_city, locate_city


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
