import argparse
import base64
import binascii
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sys
import threading
import time
import webbrowser
from urllib.parse import parse_qs, urlsplit

from bridge.device_support.display import Display
from .live_renderer import LiveRenderer
from .serial_link import SerialLink, list_ports
from .sources import (LogObserver, geocode_city, locate_city, normalize_limits, read_app_server,
                      weather_fetch, quota_error_message)

if getattr(sys, "frozen", False):
    ROOT = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    CANMV_PREVIEW = ROOT / "CanMV_Preview.py"
    K210_PROGRAM = CANMV_PREVIEW
else:
    ROOT = Path(__file__).resolve().parents[1]
    CANMV_PREVIEW = ROOT / "K210" / "CanMV_Preview.py"
    K210_PROGRAM = ROOT / "K210" / "main.py"

WEATHER_HITBOX = (226, 0, 94, 82)
BACK_HITBOX = (0, 0, 72, 56)
INFO_HITBOX = (266, 0, 54, 52)
CODEX_HITBOX = (12, 57, 296, 38)
CLOCK_SETTINGS_HITBOX = (268, 0, 52, 48)
FLIP_HITBOX = (12, 99, 296, 38)
BLUETOOTH_HITBOX = (12, 141, 296, 38)
BRIGHTNESS_DOWN_HITBOX = (214, 184, 44, 46)
BRIGHTNESS_UP_HITBOX = (262, 184, 46, 46)


def point_in_rect(x, y, rect):
    left, top, width, height = rect
    return left <= x < left + width and top <= y < top + height


class Canvas:
    def __init__(self):
        self.ops = []

    def draw_rectangle(self, x, y, w, h, color, fill=False):
        self.ops.append(["rect", x, y, w, h, color, fill])

    def draw_line(self, x, y, x2, y2, color, thickness=1):
        self.ops.append(["line", x, y, x2, y2, color, thickness])

    def draw_circle(self, x, y, r, color, fill=False):
        self.ops.append(["circle", x, y, r, color, fill])


class Bridge:
    def __init__(self, config, config_path=None):
        self.config = config
        self.config_path = Path(config_path) if config_path else ROOT / "config.local.json"
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.tick_lock = threading.Lock()
        self.reconfigure_lock = threading.Lock()
        self.serial_connection_lock = threading.Lock()
        self.program_update_lock = threading.Lock()
        self.sync_lock = threading.Lock()
        self.quota_refresh_lock = threading.Lock()
        self.weather_refresh_lock = threading.Lock()
        self.quota_wake = threading.Event()
        self.touch_sync_busy = False
        self.weather_wake = threading.Event()
        self.observer = LogObserver(config["codex_home"], config["limit_id"])
        self.link = SerialLink(config["serial_port"], config["baudrate"])
        self.renderer = LiveRenderer(CANMV_PREVIEW)
        self.remote_quota = None
        self.remote_at = 0
        self.remote_error = None
        self.weather = {"temp": None, "condition": "未知", "at": 0}
        self.weather_error = None
        self.device_settings_error = None
        self.program_update = {"status": "idle", "progress": 0,
                               "message": "尚未更新"}
        self.demo_started_at = int(time.time())
        self.seq = 0
        self.snapshot = {}
        self.last_sync = None
        self.threads = []
        self.tick(send=False, poll=False)

    def background(self, fn):
        t = threading.Thread(target=fn, daemon=True)
        self.threads.append(t)
        t.start()

    def start(self):
        self.background(self.loop)
        self.background(self.quota_loop)
        self.background(self.weather_loop)

    def loop(self):
        next_refresh = 0
        while not self.stop.is_set():
            with self.tick_lock:
                changed = self.link.poll()
                requested = self.link.sync_requested
                self.link.sync_requested = False
            if requested and not self.touch_sync_busy:
                self.touch_sync_busy = True
                self.background(self.sync_from_touch)
            self.apply_device_settings()
            now = time.monotonic()
            if changed or now >= next_refresh:
                # Polling local data/refreshing the preview does not imply a write.
                self.tick()
                self.apply_device_settings()
                next_refresh = now + 0.5
            self.stop.wait(0.1)

    def sync_from_touch(self):
        try:
            self.sync_now()
        except Exception as exc:
            with self.lock:
                self.last_sync = {"at": time.time(), "ok": False,
                                  "results": {"device": {"status": "failed", "message": str(exc)}}}
        finally:
            self.touch_sync_busy = False

    def quota_loop(self):
        while not self.stop.is_set():
            with self.lock:
                c = copy.deepcopy(self.config)
            active = c["source"] in ("app-server", "hybrid")
            if active:
                self.refresh_quota(c)
            self.quota_wake.wait(c["quota_poll_seconds"] if active else 3600)
            self.quota_wake.clear()

    def refresh_quota(self, config=None):
        with self.quota_refresh_lock:
            return self._refresh_quota(config)

    def _refresh_quota(self, config=None):
        """Query the Codex service once; keep the last good value on failure."""
        with self.lock:
            c = copy.deepcopy(self.config) if config is None else config
        if c["source"] not in ("app-server", "hybrid"):
            return None
        key = (c["source"], c["codex_bin"], c["codex_home"], c["limit_id"])
        try:
            raw = read_app_server(c["codex_bin"], c["codex_home"])
            quota = normalize_limits(raw, c["limit_id"])
            if quota is None:
                raise ValueError("No matching 5h/7d bucket")
        except Exception as exc:
            message = quota_error_message(exc)
            with self.lock:
                current = self.config
                if (current["source"], current["codex_bin"], current["codex_home"],
                        current["limit_id"]) == key:
                    self.remote_error = message
            return message
        with self.lock:
            current = self.config
            if (current["source"], current["codex_bin"], current["codex_home"],
                    current["limit_id"]) == key:
                self.remote_quota, self.remote_at = quota, time.time()
                self.remote_error = None
        return None

    def weather_loop(self):
        while not self.stop.is_set():
            with self.lock:
                c = copy.deepcopy(self.config)
            active = c["source"] != "demo"
            error = None
            if active:
                error = self.refresh_weather(c)
            delay = min(60, c["weather"]["poll_seconds"]) if error else (
                c["weather"]["poll_seconds"] if active else 3600)
            self.weather_wake.wait(delay)
            self.weather_wake.clear()

    def refresh_weather(self, config=None):
        with self.weather_refresh_lock:
            return self._refresh_weather(config)

    def _refresh_weather(self, config=None):
        """Fetch the current weather once; keep the last good value on failure."""
        with self.lock:
            c = copy.deepcopy(self.config) if config is None else config
        if c["source"] == "demo":
            return None
        try:
            effective = dict(c["weather"])
            if effective.get("auto_location", False):
                effective.update(locate_city())
            weather = weather_fetch(effective)
            weather["city"] = effective["city"]
        except Exception as exc:
            message = type(exc).__name__ + ": 天气暂不可用"
            with self.lock:
                if self.config["weather"] == c["weather"] and self.config["source"] == c["source"]:
                    self.weather_error = message
            return message
        with self.lock:
            if self.config["weather"] == c["weather"] and self.config["source"] == c["source"]:
                self.weather = weather
                self.weather_error = None
        return None

    def sync_now(self):
        """Force one full refresh: logs, quota, weather, device time, then redraw."""
        if not self.sync_lock.acquire(blocking=False):
            raise RuntimeError("正在同步，请等待本次同步完成")
        try:
            with self.reconfigure_lock:
                with self.lock:
                    c = copy.deepcopy(self.config)
                demo = c["source"] == "demo"
                results = {}
                quota_error = None if demo else self.refresh_quota(c)
                weather_error = None if demo else self.refresh_weather(c)
                results["quota"] = {"status": "skipped" if c["source"] not in ("hybrid", "app-server")
                                    else "failed" if quota_error else "ok",
                                    "message": quota_error or ("演示数据" if demo else
                                        "使用本地日志" if c["source"] == "logs" else "额度已更新")}
                results["weather"] = {"status": "skipped" if demo
                                      else "failed" if weather_error else "ok",
                                      "message": weather_error or ("未启用实时天气" if demo
                                                                  else "天气已更新")}
                with self.tick_lock:
                    self.observer.next_scan = 0
                    self.link.request_time_sync()
                    sync_started = time.time()
                    self._tick(send=True, poll=not demo)
                results["logs"] = {"status": "skipped" if demo else "failed" if self.observer.error else "ok",
                                   "message": "演示数据" if demo else self.observer.error or "日志已刷新"}
                deadline = time.monotonic() + 2
                while c["serial_port"] and time.monotonic() < deadline:
                    with self.tick_lock:
                        self.link.poll()
                        info = self.link.info()
                    if (info.get("time_sync_at") or 0) >= sync_started or info.get("error"):
                        break
                    self.stop.wait(0.05)
                with self.tick_lock:
                    info = self.link.info()
                    confirmed = (info.get("time_sync_at") or 0) >= sync_started
                    results["clock"] = {"status": "skipped" if not c["serial_port"] else "ok" if confirmed else "failed",
                                        "message": "仅网页预览" if not c["serial_port"] else
                                            "设备已确认校时" if confirmed else "未收到设备校时确认，请检查连接"}
                    self._tick(send=False, poll=False)
                with self.lock:
                    self.last_sync = {"at": time.time(), "results": results,
                                      "ok": all(r["status"] != "failed" for r in results.values())}
        finally:
            self.sync_lock.release()
        self.apply_device_settings()
        return self.read()

    def apply_device_settings(self):
        """Persist settings reported by the K210 touch UI."""
        with self.tick_lock:
            device_flip = self.link.take_device_flip()
        if device_flip is None:
            return False
        with self.reconfigure_lock:
            with self.lock:
                if bool(self.config.get("flip", False)) == device_flip:
                    self.device_settings_error = None
                    return False
                new = copy.deepcopy(self.config)
                new["flip"] = device_flip
            try:
                temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
                temporary.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
                temporary.replace(self.config_path)
            except OSError as exc:
                with self.lock:
                    self.device_settings_error = "设备设置保存失败：" + str(exc)
                return False
            with self.lock:
                self.config = new
                self.device_settings_error = None
                if self.snapshot.get("packet"):
                    self.snapshot["packet"]["flip"] = device_flip
            return True

    def tick(self, send=True, poll=True):
        with self.tick_lock:
            return self._tick(send, poll)

    def _tick(self, send=True, poll=True):
        c = self.config
        demo = c["source"] == "demo"
        if not demo and poll:
            try:
                self.observer.poll()
            except Exception as exc:
                self.observer.error = type(exc).__name__ + ": 日志读取失败"
        now = time.time()
        with self.lock:
            remote, remote_at = self.remote_quota, self.remote_at
            weather = dict(self.weather)
        activity = self.observer.current_activity(c["activity_stale_seconds"])
        q, at, origin = None, 0, "unavailable"
        if c["source"] in ("logs", "hybrid") and self.observer.quota:
            q, at, origin = self.observer.quota, self.observer.quota_at, "logs"
        if c["source"] in ("app-server", "hybrid") and remote and remote_at >= at:
            q, at, origin = remote, remote_at, "app-server"
        if demo:
            q = {"q5": 88, "q7": 97, "r5": self.demo_started_at + 4320, "r7": self.demo_started_at + 604800}
            at, origin = now, "demo"
            activity = {"status": "RUNNING", "model": "demo-model"}
            weather = {"temp": 26, "condition": "多云", "at": now}
        self.seq += 1
        device_ui = self.link.ui_state()
        packet = {"v": 1, "type": "state", "seq": self.seq, "ts": int(now),
                  "offset": c["utc_offset_minutes"], "city": weather.get("city", c["weather"]["city"]),
                  "temp": weather["temp"], "condition": weather["condition"],
                  "status": activity["status"], "model": activity["model"],
                  "flip": bool(c.get("flip", False)),
                  "page": device_ui["page"],
                  "show_codex": device_ui["show_codex"],
                  "brightness": device_ui["brightness"],
                  "stale": not at or now - at > c["quota_stale_seconds"],
                  "weather_stale": now - weather["at"] > 3600, "demo": demo}
        packet.update(q or {"q5": None, "q7": None, "r5": None, "r7": None})
        # Send the actual completed upload time back to that same device boot.
        with self.lock:
            update = dict(self.program_update)
        reported = self.link.info().get("device_info", {})
        if (update.get("status") == "success" and reported.get("version")
                and update.get("device_boot_id") == self.link.device_boot_id):
            packet["program_updated_at"] = int(update["updated_at"])
            packet["program_version"] = reported["version"]
        packet["time_sync"] = False
        if send:
            packet = self.link.exchange(packet, c["clock_sync_seconds"])
        link_info = self.link.info()
        online = link_info["connected"]
        self._confirm_program_update(link_info)
        device_page = link_info.get("device_page", "dashboard")
        device_flip = link_info.get("device_flip")
        device_brightness = link_info.get("device_brightness", 100)
        render_flip = device_flip if isinstance(device_flip, bool) else packet["flip"]
        canvas = Canvas()
        display_type = self.renderer.display_type()
        if display_type is not None:
            try:
                device_display = display_type(canvas)
                if device_page == "info" and hasattr(device_display, "render_info"):
                    device_display.render_info(link_info.get("device_info", {}),
                                               c["utc_offset_minutes"], online=online)
                elif device_page == "settings" and hasattr(device_display, "render_settings"):
                    device_display.render_settings(render_flip, device_brightness,
                                                   touch_ready=True,
                                                   show_codex=link_info.get("device_show_codex", True),
                                                   bluetooth_enabled=link_info.get("device_bluetooth_enabled", False))
                else:
                    device_display.render(packet, online=online)
            except Exception as exc:
                self.renderer.report_render_error(exc)
                canvas = Canvas()
                Display(canvas, str(ROOT / "bridge/device_support/font.json")).render(
                    packet, online=online)
        else:
            Display(canvas, str(ROOT / "bridge/device_support/font.json")).render(
                packet, online=online)
        with self.lock:
            self.snapshot = {"packet": packet, "frame": canvas.ops, "serial": link_info,
                "frame_version": hashlib.sha256(json.dumps(canvas.ops, separators=(",", ":")).encode()).hexdigest()[:20],
                "activity": activity,
                "quota_source": origin, "quota_updated_at": at or None,
                "weather_updated_at": weather["at"] or None,
                "device_page": device_page,
                "errors": [e for e in (self.observer.error if not demo else None,
                    self.remote_error, self.weather_error, self.link.error,
                    self.device_settings_error, self.renderer.error) if e],
                "source_mode": c["source"]}

    def read(self, frame_version=None, include_frame=True):
        with self.lock:
            include_frame = include_frame and frame_version != self.snapshot.get("frame_version")
            state = copy.deepcopy({key: value for key, value in self.snapshot.items()
                                   if include_frame or key != "frame"})
            state["program_update"] = copy.deepcopy(self.program_update)
            state["sync"] = copy.deepcopy(self.last_sync)
            return state

    def _set_program_update(self, status, progress, message, **details):
        update = {"status": status, "progress": max(0, min(100, int(progress))),
                  "message": message, "updated_at": time.time()}
        update.update(details)
        with self.lock:
            self.program_update = update

    def _confirm_program_update(self, link_info):
        """Mark an update successful only after a post-reset device reply."""
        with self.lock:
            pending = copy.deepcopy(self.program_update)
        if pending.get("status") != "restarting":
            return False
        if time.monotonic() >= pending.get("confirm_deadline", float("inf")):
            self._set_program_update("failed", 100,
                "文件已写入，但 45 秒内未确认新程序启动。请检查设备供电及程序；必要时用 CanMV IDE 恢复 main.py.bak。",
                target=pending.get("target"), backup=pending.get("backup"))
            return False
        confirm_after = pending.get("confirm_after", 0)
        ack_at = link_info.get("ack_at") or 0
        boot_id = link_info.get("device_boot_id")
        if (not link_info.get("connected") or ack_at < confirm_after
                or boot_id is None or boot_id == pending.get("previous_boot_id")):
            return False
        details = {key: value for key, value in pending.items()
                   if key not in ("status", "progress", "message",
                                  "updated_at", "confirm_after", "confirm_deadline")}
        details["device_boot_id"] = link_info.get("device_boot_id")
        self._set_program_update(
            "success", 100, "更新成功，K210 已重启并重新连接", **details)
        return True

    def update_device_program(self, target="auto", source_path=None,
                              source_bytes=None, source_name=None):
        """Upload a selected Python program over the active realtime link."""
        if not self.program_update_lock.acquire(blocking=False):
            raise RuntimeError("K210 程序正在更新，请等待当前更新完成")
        try:
            with self.lock:
                restarting = self.program_update.get("status") == "restarting"
            if restarting:
                raise RuntimeError("正在等待 K210 重启，请等待确认结果")
            return self._update_device_program(
                target, source_path=source_path,
                source_bytes=source_bytes, source_name=source_name)
        except Exception as exc:
            if not restarting:
                self._set_program_update("failed", 0,
                                         type(exc).__name__ + ": " + str(exc))
            raise
        finally:
            self.program_update_lock.release()

    def _update_device_program(self, target="auto", source_path=None,
                               source_bytes=None, source_name=None):
        if target not in ("auto", "flash", "sd"):
            raise ValueError("无效的程序存储位置")
        if source_path is not None and source_bytes is not None:
            raise ValueError("文件路径和上传文件不能同时使用")
        if source_bytes is None:
            selected = Path(source_path).expanduser() if source_path else K210_PROGRAM
            if not selected.is_absolute():
                selected = ROOT / selected
            selected = selected.resolve()
            if selected.suffix.lower() != ".py":
                raise ValueError("主程序必须是 .py 文件")
            try:
                source = selected.read_bytes()
            except OSError as exc:
                raise OSError("无法读取主程序文件：" + str(exc))
            source_label = str(selected)
        else:
            if not isinstance(source_bytes, bytes):
                raise ValueError("上传的主程序内容无效")
            source_label = str(source_name or "已选择的 main.py")
            if not source_label.lower().endswith(".py"):
                raise ValueError("主程序必须是 .py 文件")
            source = source_bytes
        if not source or len(source) > 512 * 1024:
            raise ValueError("主程序大小必须在 1 B 到 512 KB 之间")
        try:
            source.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError("主程序必须使用 UTF-8 编码")
        online_update_preserved = (b"class ProgramUpdater" in source or
                                   b"from program_update import ProgramUpdater" in source)

        self._set_program_update("preparing", 0, "正在准备 main.py",
                                 source=source_label)

        def report(done, total):
            percent = int(done * 100 / total) if total else 0
            self._set_program_update("uploading", percent,
                                     "正在上传 main.py… %d%%" % percent)

        try:
            with self.tick_lock:
                if not self.link.info()["connected"]:
                    raise ConnectionError("请先连接 K210，并等待设备确认")
                previous_boot_id = self.link.device_boot_id
                result = self.link.update_program(source, target=target,
                                                  progress=report)
            self._set_program_update(
                "restarting", 100,
                "文件已写入，等待 K210 重启后确认",
                target=result["target"], size=result["size"],
                sha256=result["sha256"], backup=result["backup"],
                source=source_label,
                online_update_preserved=online_update_preserved,
                previous_boot_id=previous_boot_id,
                confirm_deadline=time.monotonic() + 45,
                confirm_after=(time.time()
                               + result.get("restart_ms", 1200) / 1000.0))
            result["source"] = source_label
            result["online_update_preserved"] = online_update_preserved
            return result
        except Exception as exc:
            self._set_program_update("failed", 0,
                                     type(exc).__name__ + ": " + str(exc))
            raise

    def device_touch(self, x, y):
        """Apply a browser-preview tap using the K210 screen's hit boxes."""
        if (not isinstance(x, (int, float)) or isinstance(x, bool)
                or not isinstance(y, (int, float)) or isinstance(y, bool)):
            raise ValueError("触摸坐标无效")
        x, y = int(x), int(y)
        if not 0 <= x < 320 or not 0 <= y < 240:
            raise ValueError("触摸坐标超出屏幕")

        flip_requested = False
        with self.tick_lock:
            ui = self.link.ui_state()
            page, brightness = ui["page"], ui["brightness"]
            if page == "dashboard" and point_in_rect(x, y,
                    WEATHER_HITBOX if ui["show_codex"] else CLOCK_SETTINGS_HITBOX):
                self.link.set_device_ui(page="settings")
            elif page == "settings" and point_in_rect(x, y, INFO_HITBOX):
                self.link.set_device_ui(page="info")
            elif page == "info" and point_in_rect(x, y, BACK_HITBOX):
                self.link.set_device_ui(page="settings")
            elif page == "settings" and point_in_rect(x, y, BACK_HITBOX):
                self.link.set_device_ui(page="dashboard")
            elif page == "settings" and point_in_rect(x, y, CODEX_HITBOX):
                self.link.set_device_ui(show_codex=not ui["show_codex"])
            elif page == "settings" and point_in_rect(x, y, FLIP_HITBOX):
                flip_requested = True
            elif page == "settings" and point_in_rect(x, y, BRIGHTNESS_DOWN_HITBOX):
                self.link.set_device_ui(brightness=max(20, brightness - 10))
            elif page == "settings" and point_in_rect(x, y, BRIGHTNESS_UP_HITBOX):
                self.link.set_device_ui(brightness=min(100, brightness + 10))
            if not flip_requested:
                self._tick(send=True, poll=False)
        if flip_requested:
            with self.lock:
                next_flip = not bool(self.config.get("flip", False))
            self.save_config({"flip": next_flip})
        self.apply_device_settings()
        return self.read()

    def config_view(self):
        with self.lock:
            c = copy.deepcopy(self.config)
        return {"source": c["source"], "serial_port": c["serial_port"],
                "baudrate": c["baudrate"], "utc_offset_minutes": c["utc_offset_minutes"],
                "flip": bool(c.get("flip", False)),
                "weather": {key: c["weather"][key] for key in
                            ("enabled", "city", "auto_location")}}

    def save_config(self, update):
        if not isinstance(update, dict):
            raise ValueError("配置格式无效")
        allowed = {"source", "serial_port", "baudrate", "utc_offset_minutes", "flip",
                   "weather"}
        if set(update) - allowed:
            raise ValueError("包含不支持的配置项")
        with self.reconfigure_lock:
            with self.lock:
                new = copy.deepcopy(self.config)
            for key in ("source", "serial_port", "baudrate", "utc_offset_minutes", "flip"):
                if key in update:
                    new[key] = update[key]
            if "weather" in update:
                weather = update["weather"]
                weather_allowed = {"enabled", "city", "auto_location"}
                if not isinstance(weather, dict) or set(weather) - weather_allowed:
                    raise ValueError("天气配置格式无效")
                previous_city = new["weather"]["city"]
                weather = dict(weather)
                if weather.get("auto_location", new["weather"].get("auto_location", False)):
                    weather.pop("city", None)
                new["weather"].update(weather)
                new["weather"]["enabled"] = True
                validate(new)
                if "city" in weather and weather["city"] != previous_city:
                    latitude, longitude = geocode_city(weather["city"])
                    new["weather"].update(latitude=latitude, longitude=longitude)
            validate(new)
            temporary = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
            temporary.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n")
            temporary.replace(self.config_path)
            self.reconfigure(new)
        return self.config_view()

    def reconfigure(self, new):
        with self.tick_lock:
            old = self.config
            observer_changed = (old["codex_home"], old["limit_id"]) != (
                new["codex_home"], new["limit_id"])
            link_changed = (old["serial_port"], old["baudrate"]) != (
                new["serial_port"], new["baudrate"])
            remote_changed = (old["source"], old["codex_bin"], old["codex_home"], old["limit_id"]) != (
                new["source"], new["codex_bin"], new["codex_home"], new["limit_id"])
            weather_changed = old["weather"] != new["weather"] or old["source"] != new["source"]
            flip_changed = bool(old.get("flip")) != bool(new.get("flip"))
            if observer_changed:
                self.observer = LogObserver(new["codex_home"], new["limit_id"])
            if link_changed:
                self.link.close()
                self.link = SerialLink(new["serial_port"], new["baudrate"])
            with self.lock:
                self.config = copy.deepcopy(new)
                if remote_changed:
                    self.remote_quota, self.remote_at, self.remote_error = None, 0, None
                if weather_changed:
                    self.weather = {"temp": None, "condition": "未知", "at": 0}
                    self.weather_error = None
        self.quota_wake.set()
        self.weather_wake.set()
        # A flip is a panel-level change: push it to the device right away.
        self.tick(send=flip_changed, poll=False)

    def close(self):
        self.stop.set()
        self.quota_wake.set()
        self.weather_wake.set()
        for t in self.threads:
            t.join(timeout=25)
        self.link.close()

    def reconnect_serial(self):
        """Close and reopen the configured serial link on the next exchange."""
        with self.tick_lock:
            with self.lock:
                c = copy.deepcopy(self.config)
            self.link.close()
            self.link = SerialLink(c["serial_port"], c["baudrate"])
            self._tick(send=True, poll=False)
        self.apply_device_settings()
        return self.read()

    def connect_bluetooth(self, address):
        from .bluetooth_devices import ADDRESS, serial_port_for_device
        if not isinstance(address, str) or not ADDRESS.fullmatch(address):
            raise ValueError("蓝牙设备地址无效")
        with self.serial_connection_lock:
            # Release the stream before reopening the selected device. Applying
            # the same port must also create a new channel and a fresh handshake.
            self.save_config({"serial_port": ""})
            port = serial_port_for_device(address)
            self.save_config({"serial_port": port, "baudrate": 9600})
            state = self.reconnect_serial()
            if state["serial"].get("error"):
                raise ConnectionError(state["serial"]["error"])
            return port


def handler_for(bridge):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def allowed_host(self):
            host = self.headers.get("Host", "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                self.send_error(403)
                return False
            return True

        def send_body(self, status, body, mime="application/json; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; frame-ancestors 'none'")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if not self.allowed_host():
                return
            parsed = urlsplit(self.path)
            path = parsed.path
            if path == "/api/state":
                version = parse_qs(parsed.query).get("frame_version", [None])[0]
                body = json.dumps(bridge.read(frame_version=version), ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif path == "/api/config":
                body = json.dumps(bridge.config_view(), ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif path == "/api/ports":
                body = json.dumps(list_ports(), ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif path == "/api/program-info":
                try:
                    size = K210_PROGRAM.stat().st_size
                except OSError:
                    size = None
                body = json.dumps({
                    "default_path": str(K210_PROGRAM.resolve()),
                    "exists": K210_PROGRAM.is_file(),
                    "size": size,
                }, ensure_ascii=False).encode()
                mime = "application/json; charset=utf-8"
            elif path == "/":
                body = (ROOT / "bridge/web/index.html").read_bytes()
                mime = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_body(200, body, mime)

        def do_POST(self):
            if not self.allowed_host():
                return
            path = self.path.split("?", 1)[0]
            if path == "/api/sync":
                try:
                    state = bridge.sync_now()
                except Exception as exc:
                    body = json.dumps({"ok": False, "error": type(exc).__name__ + ": 同步过程中出现异常"},
                                      ensure_ascii=False).encode()
                    self.send_body(500, body)
                    return
                body = json.dumps({"ok": True, "state": state}, ensure_ascii=False).encode()
                self.send_body(200, body)
                return
            if path in ("/api/bluetooth/pair", "/api/bluetooth/select"):
                from .bluetooth_devices import pick_device
                try:
                    result = pick_device(path.rsplit("/", 1)[-1])
                    self.send_body(200, json.dumps(result, ensure_ascii=False).encode())
                except (OSError, RuntimeError, TimeoutError) as exc:
                    self.send_body(500, json.dumps({"ok": False, "error": str(exc)},
                                                   ensure_ascii=False).encode())
                return
            if path == "/api/bluetooth/connect":
                if self.headers.get_content_type() != "application/json":
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024:
                        raise ValueError("蓝牙连接请求大小无效")
                    request = json.loads(self.rfile.read(length))
                    if not isinstance(request, dict) or set(request) != {"address"}:
                        raise ValueError("蓝牙连接请求格式无效")
                    port = bridge.connect_bluetooth(request["address"])
                    self.send_body(200, json.dumps({"ok": True, "port": port},
                                                   ensure_ascii=False).encode())
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    self.send_body(400, json.dumps({"ok": False, "error": str(exc)},
                                                   ensure_ascii=False).encode())
                except ConnectionError as exc:
                    self.send_body(409, json.dumps({"ok": False, "error": str(exc)},
                                                   ensure_ascii=False).encode())
                except (OSError, RuntimeError) as exc:
                    self.send_body(500, json.dumps({"ok": False, "error": str(exc)},
                                                   ensure_ascii=False).encode())
                return
            if path == "/api/device-touch":
                if self.headers.get_content_type() != "application/json":
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 1024:
                        raise ValueError("触摸数据大小无效")
                    touch = json.loads(self.rfile.read(length))
                    if not isinstance(touch, dict) or set(touch) != {"x", "y"}:
                        raise ValueError("触摸数据格式无效")
                    state = bridge.device_touch(touch["x"], touch["y"])
                    body = json.dumps({"ok": True, "state": state},
                                      ensure_ascii=False).encode()
                    self.send_body(200, body)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    body = json.dumps({"ok": False, "error": str(exc)},
                                      ensure_ascii=False).encode()
                    self.send_body(400, body)
                return
            if path == "/api/program-update":
                if self.headers.get_content_type() != "application/json":
                    self.send_error(415)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 750000:
                        raise ValueError("更新请求大小无效")
                    request = json.loads(self.rfile.read(length))
                    allowed = {"target", "path", "content", "filename"}
                    if not isinstance(request, dict) or set(request) - allowed:
                        raise ValueError("更新请求格式无效")
                    encoded = request.get("content")
                    source = None
                    if encoded is not None:
                        if not isinstance(encoded, str):
                            raise ValueError("上传的主程序内容无效")
                        try:
                            source = base64.b64decode(encoded, validate=True)
                        except (binascii.Error, ValueError) as exc:
                            raise ValueError("上传的主程序内容不是有效的 Base64") from exc
                    result = bridge.update_device_program(
                        request.get("target", "auto"),
                        source_path=request.get("path"),
                        source_bytes=source,
                        source_name=request.get("filename"))
                    body = json.dumps({"ok": True, "result": result},
                                      ensure_ascii=False).encode()
                    self.send_body(200, body)
                except (ValueError, TypeError, json.JSONDecodeError) as exc:
                    body = json.dumps({"ok": False, "error": str(exc)},
                                      ensure_ascii=False).encode()
                    self.send_body(400, body)
                except ConnectionError as exc:
                    body = json.dumps({"ok": False, "error": str(exc)},
                                      ensure_ascii=False).encode()
                    self.send_body(409, body)
                except (OSError, RuntimeError, TimeoutError) as exc:
                    body = json.dumps({"ok": False, "error": str(exc)},
                                      ensure_ascii=False).encode()
                    self.send_body(500, body)
                return
            if path != "/api/config":
                self.send_error(404)
                return
            if self.headers.get_content_type() != "application/json":
                self.send_error(415)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    raise ValueError("配置内容大小无效")
                update = json.loads(self.rfile.read(length))
                config = bridge.save_config(update)
                body = json.dumps({"ok": True, "config": config}, ensure_ascii=False).encode()
                self.send_body(200, body)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                body = json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False).encode()
                self.send_body(400, body)
            except OSError as exc:
                body = json.dumps({"ok": False, "error": "配置保存失败：" + str(exc)}, ensure_ascii=False).encode()
                self.send_body(500, body)
    return Handler


def load_config(path=None):
    c = json.loads((ROOT / "config.example.json").read_text())
    if path:
        custom = json.loads(Path(path).read_text())
        weather = dict(c["weather"], **custom.pop("weather", {}))
        c.update(custom)
        c["weather"] = weather
    c["weather"]["enabled"] = True
    return c


def validate(c):
    if c["source"] not in ("demo", "logs", "app-server", "hybrid"):
        raise ValueError("source must be demo/logs/app-server/hybrid")
    for name, lower, upper in (("quota_poll_seconds", 60, 86400), ("quota_stale_seconds", 60, 604800),
                               ("activity_stale_seconds", 30, 86400), ("http_port", 1024, 65535),
                               ("clock_sync_seconds", 60, 86400), ("baudrate", 9600, 1500000),
                               ("utc_offset_minutes", -720, 840)):
        if not isinstance(c[name], int) or isinstance(c[name], bool) or not lower <= c[name] <= upper:
            raise ValueError("Invalid " + name)
    if not isinstance(c.get("flip"), bool):
        raise ValueError("Invalid flip")
    w = c["weather"]
    if not isinstance(w.get("auto_location", False), bool):
        raise ValueError("Invalid auto_location")
    for key, lo, hi in (("latitude", -90, 90), ("longitude", -180, 180), ("poll_seconds", 600, 86400)):
        if not isinstance(w[key], (int, float)) or not math.isfinite(w[key]) or not lo <= w[key] <= hi:
            raise ValueError("Invalid weather " + key)
    if not isinstance(w["city"], str) or not 1 <= len(w["city"]) <= 16:
        raise ValueError("Invalid city")
    if not isinstance(w["enabled"], bool):
        raise ValueError("Invalid weather enabled")
    if not isinstance(c["serial_port"], str) or len(c["serial_port"]) > 256:
        raise ValueError("Invalid serial_port")


def runtime_config(config_path=None, overrides=None):
    path = Path(config_path) if config_path else ROOT / "config.local.json"
    c = load_config(path if path.exists() else None)
    for key, value in (overrides or {}).items():
        if value is not None:
            c[key] = value
    validate(c)
    return c, path


def create_runtime(config_path=None, overrides=None):
    """Create a bridge and HTTP server for CLI or menu-bar ownership."""
    c, path = runtime_config(config_path, overrides)
    bridge = Bridge(c, path)
    server = ThreadingHTTPServer(("127.0.0.1", c["http_port"]), handler_for(bridge))
    url = "http://127.0.0.1:%d" % c["http_port"]
    return bridge, server, url


def main():
    parser = argparse.ArgumentParser(description="K210 Codex Display Hub")
    parser.add_argument("--config")
    parser.add_argument("--source", choices=["demo", "logs", "app-server", "hybrid"])
    parser.add_argument("--serial", help="Explicit serial path; omitted means preview only")
    parser.add_argument("--port", type=int)
    parser.add_argument("--open", action="store_true", help="Open browser")
    parser.add_argument("--list-ports", action="store_true")
    parser.add_argument("--once", action="store_true", help="Read local state once; no server or serial write")
    args = parser.parse_args()
    if args.list_ports:
        print(json.dumps(list_ports(), ensure_ascii=False, indent=2))
        return
    config_path = Path(args.config) if args.config else ROOT / "config.local.json"
    overrides = {"source": args.source, "serial_port": args.serial,
                 "http_port": args.port}
    if args.once:
        c, config_path = runtime_config(config_path, overrides)
        bridge = Bridge(c, config_path)
        bridge.tick(send=False)
        state = bridge.read()
        state.pop("frame")
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    bridge, server, url = create_runtime(config_path, overrides)
    c = bridge.config
    bridge.start()
    print("K210 Codex Display Hub:", url, "| source:", c["source"], "| Ctrl+C to stop", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        bridge.close()


if __name__ == "__main__":
    main()
