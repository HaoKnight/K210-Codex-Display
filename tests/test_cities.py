import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

from bridge.app import Bridge, handler_for, load_config
from bridge.cities import city_catalog, city_location, _lookup_location
from bridge.sources import chinese_city_name


class CitySelectionTests(unittest.TestCase):
    def test_catalog_covers_provinces_and_city_choices_are_valid(self):
        provinces = city_catalog()["provinces"]
        self.assertEqual(len(provinces), 34)
        cities = [city for province in provinces for city in province["cities"]]
        self.assertEqual(len({city["code"] for city in cities}), len(cities))
        self.assertGreater(len(cities), 700)
        for province in provinces:
            self.assertTrue(province["cities"])
        for city in cities:
            self.assertEqual(chinese_city_name(city["name"]), city["name"])
            self.assertNotIn("直辖县级行政区划", city["name"])
            self.assertNotEqual(city["name"], "市辖区")
            self.assertTrue(-90 <= city["latitude"] <= 90)
            self.assertTrue(-180 <= city["longitude"] <= 180)

    def test_selected_city_uses_its_coordinates_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config()
            config.update(source="demo", serial_port="")
            bridge = Bridge(config, Path(tmp) / "config.json")
            with patch("bridge.cities.fetch_json", side_effect=OSError("offline")), \
                    patch("bridge.app.geocode_city", side_effect=OSError("offline")):
                bridge.save_config({"weather": {"auto_location": False, "city_code": "513401"}})
            saved = load_config(bridge.config_path)["weather"]
            self.assertEqual(saved["city"], "西昌市")
            self.assertAlmostEqual(saved["latitude"], 27.89642)
            self.assertAlmostEqual(saved["longitude"], 102.26341)
            self.assertEqual(bridge.read()["packet"]["city"], "西昌")
            self.assertEqual(bridge.read()["weather_city"], "西昌市")
            self.assertNotIn("city_code", saved)

    def test_auto_mode_ignores_manual_code_and_invalid_manual_code_does_not_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config()
            config.update(source="demo", serial_port="")
            bridge = Bridge(config, Path(tmp) / "config.json")
            with patch("bridge.app.city_location") as lookup:
                bridge.save_config({"weather": {"auto_location": True, "city_code": "invalid"}})
                lookup.assert_not_called()
            before = bridge.config_path.read_bytes()
            for code in ("invalid", "", 1101):
                with self.subTest(code=code), self.assertRaises(ValueError):
                    bridge.save_config({"weather": {"auto_location": False, "city_code": code}})
                self.assertEqual(bridge.config_path.read_bytes(), before)

    def test_recent_city_lookup_qualifies_province_and_retains_chinese_name(self):
        province = {"name": "新疆维吾尔自治区", "country_code": "CN"}
        city = {"name": "新星市"}
        with patch("bridge.cities.fetch_json", return_value={"results": [{
                "name": "Xinxing", "country_code": "CN", "feature_code": "PPLA3",
                "latitude": 42.8, "longitude": 93.4}]}) as fetch:
            result = _lookup_location(province, city)
        query = parse_qs(urlsplit(fetch.call_args.args[0]).query)
        self.assertEqual(query["name"], ["新星市, 新疆"])
        self.assertEqual(query["countryCode"], ["CN"])
        self.assertEqual(result["city"], "新星市")

    def test_unknown_code_rejects_without_network(self):
        with patch("bridge.cities.fetch_json") as fetch, self.assertRaises(ValueError):
            city_location("../../config.local.json")
        fetch.assert_not_called()

    def test_http_serves_bundled_city_choices(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = load_config()
            config.update(source="demo", serial_port="")
            bridge = Bridge(config, Path(tmp) / "config.json")
            server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(bridge))
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            try:
                with urlopen("http://127.0.0.1:%d/api/cities" % server.server_port) as response:
                    self.assertEqual(response.headers.get_content_type(), "application/json")
                    self.assertEqual(len(json.load(response)["provinces"]), 34)
            finally:
                server.shutdown()
                server.server_close()
                worker.join()
