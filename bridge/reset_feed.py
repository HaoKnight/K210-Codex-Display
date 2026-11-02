"""Public reset announcements, kept separate from this account's quota."""
import copy
from email.utils import parsedate_to_datetime
import hashlib
import json
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from .sources import timestamp

SOURCE = "https://aihot.news/api/v1/codex-resets/recent"
INTERVAL = 900


def safe_url(value):
    if isinstance(value, str) and len(value) <= 2048:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and parsed.username is None:
            return value
    return None


def decode_snapshot(data):
    root = json.loads(data)
    if not isinstance(root, dict) or not isinstance(root.get("events"), list):
        raise ValueError("invalid reset snapshot")
    events = []
    for raw in root["events"]:
        if (not isinstance(raw, dict) or raw.get("type") not in ("direct_reset", "reset_credit")
                or raw.get("status") not in ("announced", "confirmed")
                or not isinstance(raw.get("id"), str)):
            raise ValueError("invalid reset event")
        event = {key: raw[key] for key in ("type", "status")}
        event["id"] = raw["id"][:128]
        for source, key in (("createdAt", "created_at"), ("updatedAt", "updated_at"),
                            ("confirmedAt", "confirmed_at")):
            value = timestamp(raw.get(source))
            if (not 0 < value <= 9999999999
                    and (source != "confirmedAt" or raw.get(source) is not None)):
                raise ValueError("invalid reset timestamp")
            event[key] = int(value) if value else None
        schedule = raw.get("schedule")
        event.update(precision=None, **{"from": None, "through": None})
        if schedule is not None:
            if not isinstance(schedule, dict) or schedule.get("precision") not in (
                    "exact", "approximate", "deadline", "date", "window"):
                raise ValueError("invalid reset schedule")
            start, end = timestamp(schedule.get("from")), timestamp(schedule.get("through"))
            if not 0 < start <= end <= 9999999999:
                raise ValueError("invalid reset time range")
            event.update(precision=schedule["precision"], **{"from": int(start), "through": int(end)})
        event["expires_at"] = ((event["confirmed_at"] or event["updated_at"]) + 86400
                               if event["status"] == "confirmed" else
                               event["through"] or event["updated_at"] + 172800)
        posts = raw.get("posts") or []
        if not isinstance(posts, list):
            raise ValueError("invalid reset posts")
        post = posts[0] if posts and isinstance(posts[0], dict) else {}
        text = post.get("text") or post.get("originalText") or post.get("fullOriginalText")
        event["text"] = text[:1000] if isinstance(text, str) else ""
        event["source_url"] = safe_url(raw.get("url"))
        event["original_url"] = safe_url(post.get("url"))
        events.append(event)
    return events


def current_event(events, now):
    def rank(event):
        if event["status"] == "confirmed":
            return 4, event["confirmed_at"] or event["updated_at"]
        if event["from"] is None:
            return 1, event["updated_at"]
        if now < event["from"]:
            return 2, -event["from"]
        return 3, event["through"]
    return max((event for event in events if now <= event["expires_at"]), key=rank, default=None)


class ResetFeed:
    def __init__(self):
        self.lock = threading.Lock()
        self.events = []
        self.etag = None
        self.last_attempt = None
        self.last_success = None
        self.retry_at = 0
        self.error = None
        self.fetching = False

    def refresh(self, force=False, now=None):
        now = time.time() if now is None else now
        with self.lock:
            # The recent endpoint caches for one minute, including manual checks.
            minimum = 60 if force else INTERVAL
            if (self.fetching or now < self.retry_at
                    or self.last_attempt is not None and now - self.last_attempt < minimum):
                return self.error
            self.fetching = True
            self.last_attempt = now
            etag = self.etag
        headers = {"Accept": "application/json", "User-Agent": "K210-Codex-Display-Hub"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            try:
                with urlopen(Request(SOURCE, headers=headers), timeout=20) as response:
                    data = response.read(512 * 1024 + 1)
                    if len(data) > 512 * 1024:
                        raise ValueError("reset snapshot too large")
                    events = decode_snapshot(data)
                    etag = response.headers.get("ETag")
            except HTTPError as exc:
                exc.close()
                if exc.code == 304:
                    with self.lock:
                        self.last_success, self.error = now, None
                    return None
                if exc.code == 429:
                    retry = (exc.headers or {}).get("Retry-After", "")
                    try:
                        delay = float(retry)
                        retry_at = now + delay if 0 <= delay < float("inf") else now + INTERVAL
                    except ValueError:
                        try:
                            retry_at = parsedate_to_datetime(retry).timestamp()
                        except (ValueError, TypeError, OverflowError):
                            retry_at = now + INTERVAL
                    with self.lock:
                        self.retry_at = max(now + 1, retry_at)
                    raise
                raise
            with self.lock:
                self.events, self.etag = events, etag
                self.last_success, self.error, self.retry_at = now, None, 0
        except Exception as exc:
            message = ("重置消息请求受限（HTTP 429），稍后重试" if isinstance(exc, HTTPError) and exc.code == 429
                       else "重置消息数据无效" if isinstance(exc, (ValueError, TypeError))
                       else "暂时无法获取重置消息")
            with self.lock:
                self.error = message
        finally:
            with self.lock:
                self.fetching = False
        return self.error

    def snapshot(self, now=None):
        now = time.time() if now is None else now
        with self.lock:
            event = current_event(self.events, now)
            stale = bool(self.error) or self.last_success is None or now - self.last_success > 1800
            return {"event": copy.deepcopy(event), "checked_at": self.last_success,
                    "stale": stale, "error": self.error,
                    "retry_at": self.retry_at if self.retry_at > now else None}


def confirmation_key(event):
    if not event or event["status"] != "confirmed":
        return ""
    identity = (event.get("id", ""), event["type"],
                event.get("confirmed_at") or event.get("updated_at"))
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]


def device_event(snapshot):
    event = snapshot["event"]
    if event is None:
        return None
    return {key: event[key] for key in (
        "type", "status", "precision", "from", "through", "confirmed_at", "expires_at")
    } | {"checked_at": int(snapshot["checked_at"]) if snapshot["checked_at"] else None,
         "stale": snapshot["stale"], "key": confirmation_key(event), "seen": False}
