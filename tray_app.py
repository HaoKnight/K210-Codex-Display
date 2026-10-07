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
from AppKit import NSColor, NSFont, NSMakeRect, NSTextField, NSView
from PyObjCTools import AppHelper

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
    def __init__(self, on_menu_bar_visibility_changed=None):
        self.bridge, self.server, self.url = create_runtime(user_config_path())
        self.bridge.on_menu_bar_visibility_changed = on_menu_bar_visibility_changed
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


class ConnectionStatusItem(rumps.MenuItem):
    """Read-only status row whose color is preserved in a disabled menu item."""

    def __init__(self):
        super().__init__("K210：未连接")
        self.label = NSTextField.labelWithString_(self.title)
        self.label.setFont_(NSFont.menuFontOfSize_(0))
        self.label.sizeToFit()
        height = self.label.frame().size.height
        self.label.setFrame_(NSMakeRect(18, 4, self.label.frame().size.width, height))
        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 180, height + 8))
        view.addSubview_(self.label)
        self._menuitem.setView_(view)
        self.set_connected(False)

    def set_connected(self, connected, detail=None):
        self.title = "K210：已链接" if connected else "K210：未连接"
        self.label.setStringValue_(self.title)
        self.label.setTextColor_(NSColor.systemGreenColor() if connected
                                 else NSColor.systemRedColor())
        self._menuitem.setToolTip_(detail)
        self.label.setToolTip_(detail)


class MenuBarApp(rumps.App):
    def __init__(self):
        icon = resource_path("icons/menuTemplate.png")
        super().__init__(APP_NAME, icon=str(icon), template=True, quit_button=None)
        self.lock_file = self.acquire_instance_lock()
        self.runtime = None
        self.url = configured_http_url()
        self.start_error = None
        self.action_results = queue.Queue()
        self.action_busy = False

        self.status_item = ConnectionStatusItem()
        self.open_item = rumps.MenuItem("打开控制台", callback=self.open_console)
        self.sync_item = rumps.MenuItem("立即同步", callback=self.sync_now)
        self.reconnect_item = rumps.MenuItem("重新连接 K210", callback=self.reconnect)
        self.login_item = rumps.MenuItem("开机自动启动", callback=self.toggle_login)
        self.hide_icon_item = rumps.MenuItem("隐藏菜单栏图标", callback=self.hide_icon)
        self.login_item.state = 1 if LAUNCH_AGENT.exists() else 0
        self.menu = [self.status_item, None, self.open_item, self.sync_item,
                     self.reconnect_item, self.login_item, self.hide_icon_item, None,
                     rumps.MenuItem("退出软件", callback=self.quit_app)]

        try:
            self.runtime = HubRuntime(self.menu_bar_visibility_changed)
            self.runtime.start()
            self.url = self.runtime.url
            write_diagnostic("startup ok: " + self.url)
        except OSError as exc:
            self.start_error = str(exc)
            write_diagnostic(traceback.format_exc())
            self.status_item.set_connected(False, "后台：端口被占用")
            self.reconnect_item.set_callback(None)
            self.hide_icon_item.set_callback(None)
        except Exception as exc:
            self.start_error = type(exc).__name__ + ": " + str(exc)
            write_diagnostic(traceback.format_exc())
            self.status_item.set_connected(False, "后台：启动失败")
            self.reconnect_item.set_callback(None)
            self.hide_icon_item.set_callback(None)

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
                self.status_item.set_connected(True)
            elif serial["open"]:
                self.status_item.set_connected(False, "等待 K210 设备确认")
            elif serial.get("error"):
                self.status_item.set_connected(False, "串口异常：" + str(serial["error"]))
            else:
                self.status_item.set_connected(False)
        except Exception:
            self.status_item.set_connected(False, "后台：不可用")

    def menu_bar_visibility_changed(self):
        if threading.current_thread() is threading.main_thread():
            self.refresh_icon_visibility()
        else:
            AppHelper.callAfter(self.refresh_icon_visibility)

    @rumps.events.before_start
    def refresh_icon_visibility(self):
        """Apply saved visibility on the main thread, keeping the runtime alive."""
        status_item = getattr(getattr(self, "_nsapp", None), "nsstatusitem", None)
        if status_item is None or not self.runtime:
            return
        with self.runtime.bridge.lock:
            visible = self.runtime.bridge.config.get("show_menu_bar_icon", True)
        if bool(status_item.isVisible()) != visible:
            status_item.setVisible_(visible)

    def open_console(self, _):
        webbrowser.open(self.url)

    def hide_icon(self, _):
        if not self.runtime:
            return
        try:
            self.runtime.bridge.save_config({"show_menu_bar_icon": False})
        except Exception as exc:
            rumps.alert("隐藏图标失败", str(exc))
            return
        self.refresh_icon_visibility()

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
    if any(flag in sys.argv for flag in ("--bluetooth-serial", "--bluetooth-pair",
                                        "--bluetooth-select")):
        # PyInstaller's windowed app clears the Python stream objects even
        # when a helper is launched with stdin/stdout pipes.
        if sys.stdin is None:
            sys.stdin = os.fdopen(0, "r", encoding="utf-8")
        if sys.stdout is None:
            sys.stdout = os.fdopen(1, "w", encoding="utf-8", buffering=1)
    if "--bluetooth-serial" in sys.argv:
        from bridge.bluetooth_serial import main as run_bluetooth_serial
        position = sys.argv.index("--bluetooth-serial")
        sys.exit(run_bluetooth_serial(sys.argv[position + 1]))
    if "--bluetooth-pair" in sys.argv or "--bluetooth-select" in sys.argv:
        from bridge.bluetooth_pair import main as pair_bluetooth
        sys.exit(pair_bluetooth("select" if "--bluetooth-select" in sys.argv else "pair"))
    MenuBarApp().run()
