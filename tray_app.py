"""macOS menu-bar owner for K210 Codex Display Hub."""
from pathlib import Path
import fcntl
import json
import os
import plistlib
import queue
import shutil
import sys
import threading
import traceback
import urllib.request
import webbrowser

import rumps

from bridge.app import create_runtime, ROOT


APP_NAME = "K210 Codex Display Hub"
BUNDLE_ID = "com.knight.k210-codex-display-hub"
APP_SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / (BUNDLE_ID + ".plist")
DIAGNOSTIC_LOG = APP_SUPPORT / "menu-bar.log"
DEFAULT_HTTP_PORT = 2100
LEGACY_HTTP_PORT = 8765


def write_diagnostic(message):
    """Keep the last menu-bar startup result where it can be inspected."""
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    DIAGNOSTIC_LOG.write_text(message.rstrip() + "\n", encoding="utf-8")


def resource_path(name):
    return ROOT / name


def user_config_path():
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent / "config.local.json"
    APP_SUPPORT.mkdir(parents=True, exist_ok=True)
    target = APP_SUPPORT / "config.json"
    if not target.exists():
        initial = resource_path("config.local.json")
        if not initial.exists():
            initial = resource_path("config.example.json")
        shutil.copy2(initial, target)
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
        if config.get("http_port") == LEGACY_HTTP_PORT:
            config["http_port"] = DEFAULT_HTTP_PORT
            target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n",
                              encoding="utf-8")
    except (OSError, ValueError, TypeError):
        pass
    return target


def configured_http_url():
    try:
        config = json.loads(user_config_path().read_text(encoding="utf-8"))
        port = int(config.get("http_port", DEFAULT_HTTP_PORT))
    except (OSError, ValueError, TypeError):
        port = DEFAULT_HTTP_PORT
    return "http://127.0.0.1:%d" % port


def launch_arguments():
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, str(Path(__file__).resolve())]


class HubRuntime:
    def __init__(self):
        self.bridge, self.server, self.url = create_runtime(user_config_path())
        self.thread = None

    def start(self):
        self.bridge.start()
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.25}, daemon=True)
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        if self.thread:
            self.thread.join(timeout=3)
        self.bridge.close()


class MenuBarApp(rumps.App):
    def __init__(self):
        icon = resource_path("assets/menuTemplate.png")
        super().__init__(APP_NAME, icon=str(icon), template=True, quit_button=None)
        self.lock_file = self.acquire_instance_lock()
        self.runtime = None
        self.url = configured_http_url()
        self.start_error = None
        self.action_results = queue.Queue()
        self.action_busy = False

        self.status_item = rumps.MenuItem("设备：正在启动…")
        self.open_item = rumps.MenuItem("打开控制台", callback=self.open_console)
        self.sync_item = rumps.MenuItem("立即同步", callback=self.sync_now)
        self.reconnect_item = rumps.MenuItem("重新连接 K210", callback=self.reconnect)
        self.login_item = rumps.MenuItem("开机自动启动", callback=self.toggle_login)
        self.login_item.state = 1 if LAUNCH_AGENT.exists() else 0
        self.menu = [self.status_item, None, self.open_item, self.sync_item,
                     self.reconnect_item, self.login_item, None,
                     rumps.MenuItem("退出软件", callback=self.quit_app)]

        try:
            self.runtime = HubRuntime()
            self.runtime.start()
            self.url = self.runtime.url
            write_diagnostic("startup ok: " + self.url)
        except OSError as exc:
            self.start_error = str(exc)
            write_diagnostic(traceback.format_exc())
            self.status_item.title = "后台：端口被占用"
            self.reconnect_item.set_callback(None)
        except Exception as exc:
            self.start_error = type(exc).__name__ + ": " + str(exc)
            write_diagnostic(traceback.format_exc())
            self.status_item.title = "后台：启动失败"
            self.reconnect_item.set_callback(None)

        self.timer = rumps.Timer(self.refresh_status, 2)
        self.timer.start()
        self.refresh_status(None)

    @staticmethod
    def acquire_instance_lock():
        APP_SUPPORT.mkdir(parents=True, exist_ok=True)
        lock_file = open(APP_SUPPORT / "app.lock", "w")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            webbrowser.open(configured_http_url())
            raise SystemExit(0)
        return lock_file

    def state(self):
        if self.runtime:
            return self.runtime.bridge.read(include_frame=False)
        with urllib.request.urlopen(self.url + "/api/state", timeout=1) as response:
            return json.load(response)

    def refresh_status(self, _):
        while not self.action_results.empty():
            title, state, error = self.action_results.get_nowait()
            self.action_busy = False
            self.sync_item.title = "立即同步"
            self.reconnect_item.title = "重新连接 K210"
            self.sync_item.set_callback(self.sync_now)
            if self.runtime:
                self.reconnect_item.set_callback(self.reconnect)
            if error:
                rumps.alert(title + "失败", error)
            elif title == "同步":
                sync = state.get("sync") or {}
                failed = [r["message"] for r in sync.get("results", {}).values()
                          if r["status"] == "failed"]
                rumps.notification(APP_NAME, "部分同步失败" if failed else "同步完成",
                                   "；".join(failed) if failed else "同步结果已更新，可在控制台查看")
        try:
            serial = self.state()["serial"]
            if serial["connected"]:
                self.status_item.title = "K210：已连接"
            elif serial["open"]:
                self.status_item.title = "K210：等待设备确认"
            elif serial.get("error"):
                self.status_item.title = "K210：串口异常"
            else:
                self.status_item.title = "K210：未连接"
        except Exception:
            self.status_item.title = "后台：不可用"

    def open_console(self, _):
        webbrowser.open(self.url)

    def sync_now(self, _):
        if self.action_busy:
            return
        self.sync_item.title = "正在同步…"
        def work():
            if self.runtime:
                return self.runtime.bridge.sync_now()
            else:
                request = urllib.request.Request(self.url + "/api/sync", data=b"{}",
                                                 method="POST")
                with urllib.request.urlopen(request, timeout=90) as response:
                    return json.load(response)["state"]
        self.run_action("同步", work)

    def run_action(self, title, work):
        """Only the timer on the main thread touches native menu UI."""
        self.action_busy = True
        self.sync_item.set_callback(None)
        self.reconnect_item.set_callback(None)
        def worker():
            try:
                self.action_results.put((title, work(), None))
            except Exception as exc:
                self.action_results.put((title, None, str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def reconnect(self, _):
        if not self.runtime or self.action_busy:
            return
        self.reconnect_item.title = "正在重连…"
        self.run_action("重连", self.runtime.bridge.reconnect_serial)

    def toggle_login(self, sender):
        if LAUNCH_AGENT.exists():
            LAUNCH_AGENT.unlink()
            sender.state = 0
            return
        LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
        data = {"Label": BUNDLE_ID, "ProgramArguments": launch_arguments(),
                "RunAtLoad": True, "ProcessType": "Interactive"}
        temporary = LAUNCH_AGENT.with_suffix(".plist.tmp")
        with temporary.open("wb") as output:
            plistlib.dump(data, output)
        os.replace(temporary, LAUNCH_AGENT)
        sender.state = 1

    def quit_app(self, _):
        self.timer.stop()
        if self.runtime:
            self.runtime.stop()
        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
        self.lock_file.close()
        rumps.quit_application()


if __name__ == "__main__":
    MenuBarApp().run()
