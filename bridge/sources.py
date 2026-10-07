"""Read only the metadata needed for the display; never expose conversation text."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request


def timestamp(value):
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def normalize_limits(raw, limit_id="codex"):
    """Select a bucket explicitly. Unknown durations must not masquerade as 5h/7d."""
    if not isinstance(raw, dict):
        return None
    buckets = raw.get("rateLimitsByLimitId")
    if isinstance(buckets, dict):
        raw = buckets.get(limit_id)
    else:
        raw = raw.get("rateLimits", raw)
    if not isinstance(raw, dict):
        return None
    if raw.get("limitId", raw.get("limit_id", limit_id)) not in (None, limit_id):
        return None
    result = {"q5": None, "q7": None, "r5": None, "r7": None}
    found = False
    for name in ("primary", "secondary"):
        w = raw.get(name)
        if not isinstance(w, dict):
            continue
        minutes = w.get("windowDurationMins", w.get("window_minutes"))
        used = w.get("usedPercent", w.get("used_percent"))
        reset = w.get("resetsAt", w.get("resets_at"))
        suffix = "5" if minutes == 300 else "7" if minutes == 10080 else None
        if suffix and finite(used):
            result["q" + suffix] = round(max(0, min(100, 100 - used)), 1)
            result["r" + suffix] = int(reset) if finite(reset) and reset > 0 else None
            found = True
    return result if found else None


def quota_error_message(exc):
    """Expose useful diagnostics without leaking raw service output or credentials."""
    if isinstance(exc, FileNotFoundError):
        detail = "找不到 Codex 可执行文件，请检查 codex_bin"
    elif isinstance(exc, (TimeoutError, queue.Empty)):
        detail = "Codex 服务响应超时，请检查网络后重试"
    elif isinstance(exc, PermissionError):
        detail = "无权启动或访问 Codex 服务，请检查本机权限"
    elif isinstance(exc, ValueError):
        detail = "未找到匹配的 5 小时/7 天额度，请检查 limit_id"
    elif str(exc).startswith("app-server RPC error "):
        detail = "Codex 服务返回错误（%s），请检查登录状态和网络" % str(exc).rsplit(" ", 1)[-1]
    elif str(exc).startswith("app-server exited"):
        detail = "Codex 服务提前退出，请检查启动权限和 Codex 安装"
    else:
        detail = "Codex 服务不可用，请检查登录状态和网络"
    return "额度查询失败：" + detail


class LogObserver:
    """Discover periodically; tail bounded bytes and preserve incomplete lines.

    First scan takes the last 512 KiB of each of 64 most recently modified files.
    Status is a heuristic for the most recently active observed session only.
    """
    def __init__(self, home, limit_id="codex"):
        self.home = Path(home).expanduser()
        self.limit_id = limit_id
        self.files = {}
        self.paths = []
        self.next_scan = 0
        self.quota = None
        self.quota_at = 0
        self.activity = {}
        self.error = None

    def consume(self, e, key):
        if not isinstance(e, dict):
            return
        at = timestamp(e.get("timestamp"))
        p = e.get("payload")
        if not isinstance(p, dict) or not at:
            return
        if e.get("type") == "event_msg":
            q = normalize_limits(p.get("rate_limits"), self.limit_id)
            if q is not None and at > self.quota_at:
                self.quota, self.quota_at = q, at
        a = self.activity.setdefault(key, {"at": 0, "status": "UNKNOWN", "model": "--"})
        if at < a["at"]:
            return
        if e.get("type") == "turn_context":
            model = p.get("model")
            if isinstance(model, str):
                a["model"] = model[:48]
        if e.get("type") == "event_msg":
            typ = p.get("type")
            statuses = {"task_started": "RUNNING", "task_complete": "DONE",
                        "turn_aborted": "IDLE"}
            if typ in statuses:
                a.update(at=at, status=statuses[typ])
            elif typ in ("token_count", "item_completed") and a["status"] == "RUNNING":
                a["at"] = at

    def poll(self):
        self.error = None
        now = time.monotonic()
        if now >= self.next_scan:
            candidates = []
            for directory in ("sessions", "archived_sessions"):
                for path in (self.home / directory).rglob("*.jsonl"):
                    try:
                        candidates.append((path.stat().st_mtime, path))
                    except OSError:
                        pass
            self.paths = [p for _, p in sorted(candidates, reverse=True)[:64]]
            keep = set(self.paths)
            self.files = {k: v for k, v in self.files.items() if k in keep}
            self.activity = {k: v for k, v in self.activity.items() if k in keep}
            self.next_scan = now + 30
        for path in self.paths:
            try:
                stat = path.stat()
                old = self.files.get(path)
                fresh = old is None or old[0] != stat.st_ino or stat.st_size < old[1]
                if fresh:
                    pos, pending, dropping = max(0, stat.st_size - 524288), b"", False
                    self.activity.pop(path, None)
                else:
                    _, pos, pending, dropping = old
                with path.open("rb") as f:
                    f.seek(pos)
                    if fresh and pos:
                        f.readline()  # initial seek can be in the middle of a record
                    data = f.read(524288)
                    pos = f.tell()
                chunks = (pending + data).split(b"\n")
                pending = chunks.pop()
                for line in chunks:
                    if dropping:
                        dropping = False
                        continue
                    if len(line) > 524288:
                        continue
                    try:
                        self.consume(json.loads(line), path)
                    except (ValueError, UnicodeError):
                        pass
                if len(pending) > 524288:
                    pending, dropping = b"", True
                self.files[path] = (stat.st_ino, pos, pending, dropping)
            except OSError:
                self.error = "部分日志无法读取"
        if not self.paths:
            self.error = "未发现本地 Codex 会话日志"

    def current_activity(self, stale_seconds):
        if not self.activity:
            return {"status": "UNKNOWN", "model": "--", "at": 0}
        now = time.time()
        running = [v for v in self.activity.values()
                   if v["status"] == "RUNNING" and now - v["at"] <= stale_seconds]
        a = dict(max(running or list(self.activity.values()), key=lambda v: v["at"]))
        a["running_count"] = len(running)
        if time.time() - a["at"] > stale_seconds:
            # Observed work has gone quiet, so Codex is simply idle. UNKNOWN is
            # reserved for the case where no session log could be read at all.
            a["status"] = "IDLE"
        return a


def resolve_codex_binary(binary="codex"):
    """Find Codex even when a macOS GUI app receives a minimal PATH."""
    requested = Path(str(binary)).expanduser()
    if requested.parent != Path("."):
        if requested.is_file() and os.access(requested, os.X_OK):
            return str(requested.resolve())
        raise FileNotFoundError("Codex executable not found: " + str(requested))
    discovered = shutil.which(str(binary))
    if discovered:
        return discovered
    home = Path.home()
    candidates = (
        home / ".local/bin" / str(binary),
        home / ".codex/packages/standalone/current/bin" / str(binary),
        Path("/opt/homebrew/bin") / str(binary),
        Path("/usr/local/bin") / str(binary),
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    raise FileNotFoundError("Codex executable not found in standard locations")


def read_app_server(binary, home, timeout=20):
    """One short-lived stdio session; no thread/turn creation, no inference."""
    executable = resolve_codex_binary(binary)
    search_paths = [str(Path(executable).parent), str(Path.home() / ".local/bin"),
                    "/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    inherited_path = os.environ.get("PATH", "")
    env = dict(os.environ, CODEX_HOME=str(Path(home).expanduser()),
               PATH=os.pathsep.join(search_paths + ([inherited_path] if inherited_path else [])))
    proc = subprocess.Popen([executable, "app-server"], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            text=True, env=env, bufsize=1)
    responses = queue.Queue()
    def reader():
        for line in proc.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                pass
        responses.put(None)
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout
    def send(message):
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()
    def receive(ident):
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("app-server timeout")
            try:
                msg = responses.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError("app-server timeout")
            if msg is None:
                raise RuntimeError("app-server exited")
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError("app-server RPC error " + str(msg["error"].get("code")))
                return msg.get("result", {})
            if "method" in msg and "id" in msg:
                send({"id": msg["id"], "error": {"code": -32601, "message": "Unsupported"}})
    try:
        send({"id": 1, "method": "initialize", "params": {
            "clientInfo": {"name": "k210_codex_bridge", "version": "1.2.0"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        send({"id": 2, "method": "account/rateLimits/read", "params": {}})
        return receive(2)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        thread.join(timeout=1)
        proc.stdin.close()
        proc.stdout.close()


def fetch_json(url, user_agent, timeout=10):
    """Fetch weather-related JSON directly, ignoring system proxy settings."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


def weather_fetch(config):
    query = urllib.parse.urlencode({"latitude": config["latitude"],
        "longitude": config["longitude"], "current": "temperature_2m,weather_code",
        "timezone": "GMT", "timeformat": "unixtime"})
    data = fetch_json("https://api.open-meteo.com/v1/forecast?" + query,
                      "K210-Codex-Bridge/1.2.0")["current"]
    code = data["weather_code"]
    condition = ("晴" if code == 0 else "多云" if code in (1, 2, 3)
                 else "雾" if code in (45, 48) else "雪" if code in (71, 73, 75, 77, 85, 86)
                 else "雷雨" if code >= 95 else "雨")
    if not finite(data.get("temperature_2m")) or not finite(data.get("time")):
        raise ValueError("invalid weather data")
    return {"temp": data["temperature_2m"], "condition": condition, "at": data["time"]}


def chinese_city_name(city):
    """Accept a Chinese place name; never display a Latin-name fallback."""
    if not isinstance(city, str):
        return None
    city = city.strip()
    def is_han(char):
        return 0x3400 <= ord(char) <= 0x9FFF or 0x20000 <= ord(char) <= 0x323AF
    if not any(is_han(char) for char in city):
        return None
    if any(not is_han(char) and char not in " ·・-—（）()" for char in city):
        return None
    return city[:16]


def geocode_city(city):
    query = urllib.parse.urlencode({"name": city, "count": 1, "language": "zh", "format": "json"})
    results = fetch_json("https://geocoding-api.open-meteo.com/v1/search?" + query,
                         "K210-Codex-Display-Hub/1.2.0").get("results") or []
    if not results or not finite(results[0].get("latitude")) or not finite(results[0].get("longitude")):
        raise ValueError("找不到这个城市，请检查名称")
    result = results[0]
    name = chinese_city_name(result.get("name")) or chinese_city_name(city)
    if not name:
        raise ValueError("未找到城市的中文名称，请使用中文城市名")
    return {"city": name, "latitude": result["latitude"], "longitude": result["longitude"]}


def locate_city():
    """Use the network's public IP to estimate the current city."""
    data = fetch_json("https://ipwho.is/?lang=zh-CN", "K210-Codex-Display-Hub/1.2.0")
    city = data.get("city")
    lat, lon = data.get("latitude"), data.get("longitude")
    if (data.get("success") is False or not isinstance(city, str) or not city.strip()
            or not finite(lat) or not finite(lon) or not -90 <= lat <= 90
            or not -180 <= lon <= 180):
        raise ValueError("自动定位失败，请稍后重试或关闭自动定位")
    name = chinese_city_name(city)
    if not name:
        try:
            name = geocode_city(city)["city"]
        except Exception:
            name = "未知城市"
    return {"city": name, "latitude": lat, "longitude": lon}
