"""Bounded newline JSON framing. Compatible with CPython and MicroPython."""
try:
    import ujson as json
except ImportError:
    import json

MAX_LINE = 2048


class LineBuffer:
    def __init__(self):
        self.buf = b""
        self.discard = False

    def feed(self, data):
        result = []
        for part in data.split(b"\n")[:-1]:
            if not self.discard and len(self.buf) + len(part) <= MAX_LINE:
                result.append(self.buf + part)
            self.buf = b""
            self.discard = False
        tail = data.rsplit(b"\n", 1)[-1]
        if self.discard or len(self.buf) + len(tail) > MAX_LINE:
            self.buf = b""
            self.discard = True
        else:
            self.buf += tail
        return result


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def decode(line):
    try:
        p = json.loads(line.decode("utf-8"))
        if not isinstance(p, dict) or p.get("v") != 1 or p.get("type") != "state":
            return None
        if not isinstance(p.get("seq"), int) or p["seq"] < 0:
            return None
        for key in ("q5", "q7"):
            v = p.get(key)
            if v is not None and (not number(v) or not 0 <= v <= 100):
                return None
        for key in ("r5", "r7"):
            v = p.get(key)
            if v is not None and (not number(v) or not 0 <= v <= 9999999999):
                return None
        if not number(p.get("ts")) or not 0 <= p["ts"] <= 9999999999:
            return None
        if not isinstance(p.get("offset"), int) or not -720 <= p["offset"] <= 840:
            return None
        if not isinstance(p.get("time_sync", False), bool):
            return None
        if p.get("status") not in ("RUNNING", "WAITING", "DONE", "UNKNOWN", "IDLE"):
            return None
        for key, limit in (("city", 16), ("model", 48), ("condition", 16)):
            if not isinstance(p.get(key), str) or len(p[key]) > limit:
                return None
        if p.get("temp") is not None and (not number(p["temp"]) or not -100 <= p["temp"] <= 100):
            return None
        for key in ("stale", "weather_stale", "demo"):
            if not isinstance(p.get(key), bool):
                return None
        return p
    except (ValueError, TypeError, UnicodeError, KeyError):
        return None
