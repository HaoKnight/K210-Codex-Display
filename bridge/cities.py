"""Bundled Chinese city choices and coordinates for manual weather selection."""
from functools import lru_cache
import json
from pathlib import Path
from urllib.parse import urlencode

from .sources import fetch_json, finite


@lru_cache(maxsize=1)
def city_catalog():
    return json.loads((Path(__file__).parent / "web/china-cities.json").read_text())


def city_location(code):
    if not isinstance(code, str):
        raise ValueError("请选择列表中的城市")
    for province in city_catalog()["provinces"]:
        for city in province["cities"]:
            if city["code"] == code:
                if finite(city.get("latitude")) and finite(city.get("longitude")):
                    return {"city": city["name"], "latitude": city["latitude"],
                            "longitude": city["longitude"]}
                return _lookup_location(province, city)
    raise ValueError("请选择列表中的城市")


def _lookup_location(province, city):
    # Recently established cities may not yet be present in the bundled gazetteer.
    qualifiers = {"广西壮族自治区": "广西", "内蒙古自治区": "内蒙古",
                  "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
                  "西藏自治区": "西藏"}
    qualifier = qualifiers.get(province["name"], province["name"].removesuffix("省"))
    for name in dict.fromkeys((city["name"], city["name"].removesuffix("市"))):
        query = urlencode({"name": name + (", " + qualifier if province["country_code"] == "CN" else ""),
                           "countryCode": province["country_code"], "count": 10,
                           "language": "zh", "format": "json"})
        results = fetch_json("https://geocoding-api.open-meteo.com/v1/search?" + query,
                             "K210-Codex-Display-Hub/1.2.0").get("results") or []
        results = [r for r in results if finite(r.get("latitude")) and finite(r.get("longitude"))
                   and r.get("country_code") == province["country_code"]
                   and r.get("feature_code", "").startswith(("PPL", "ADM"))]
        if results:
            result = max(results, key=lambda r: (r.get("feature_code", "").startswith("PPLA"),
                                                r.get("population", 0)))
            return {"city": city["name"], "latitude": result["latitude"],
                    "longitude": result["longitude"]}
    raise ValueError("暂时无法查询所选城市的坐标，请稍后重试")
