import unittest
from unittest.mock import MagicMock, patch

from bridge.sources import fetch_json, locate_city


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
        geocode = {"results": [{"name": "天津"}]}

        with patch("bridge.sources.fetch_json",
                   side_effect=(location, geocode)) as fetch:
            result = locate_city()

        self.assertEqual(result, {"city": "天津", "latitude": 39.1422,
                                  "longitude": 117.1767})
        self.assertEqual(fetch.call_args_list[0].args[0], "https://ipwho.is/")

    def test_rejects_failed_ipwho_is_response(self):
        with patch("bridge.sources.fetch_json",
                   return_value={"success": False, "message": "rate limited"}):
            with self.assertRaisesRegex(ValueError, "自动定位失败"):
                locate_city()


if __name__ == "__main__":
    unittest.main()
