"""Application launches reuse one backend and open its existing console."""
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

if sys.platform == "darwin":
    import fcntl
    import tray_app


@unittest.skipUnless(sys.platform == "darwin", "macOS application launch behavior")
class TrayLaunchTests(unittest.TestCase):
    def test_helpers_skip_gui_and_backend_imports_and_restore_frozen_streams(self):
        script = '''
import json, runpy, sys, types
path, flag, frozen = sys.argv[1:]
class Guard:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('AppKit', 'rumps') or fullname == 'bridge.app':
            raise AssertionError('unneeded helper import: ' + fullname)
sys.meta_path.insert(0, Guard())
pair = types.ModuleType('bridge.bluetooth_pair')
def main(action):
    print(json.dumps({'action': action}))
    return 0
pair.main = main
sys.modules['bridge.bluetooth_pair'] = pair
sys.argv = [path, flag, 'invalid-address']
if frozen == '1':
    sys.frozen = True
    sys.stdin = sys.stdout = None
runpy.run_path(path, run_name='__main__')
'''
        path = str(Path(__file__).resolve().parents[1] / 'tray_app.py')
        for flag in ('--bluetooth-serial', '--bluetooth-pair', '--bluetooth-select'):
            for frozen in ('0', '1'):
                with self.subTest(flag=flag, frozen=frozen):
                    result = subprocess.run([sys.executable, '-B', '-c', script,
                                             path, flag, frozen], stdin=subprocess.DEVNULL,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, int(flag == '--bluetooth-serial'),
                                     result.stderr)
                    message = json.loads(result.stdout)
                    if flag == '--bluetooth-serial':
                        self.assertEqual(message, {'type': 'error', 'error': '蓝牙设备地址无效'})
                    else:
                        self.assertEqual(message['action'], flag.rsplit('-', 1)[-1])

    def test_launch_opens_console_and_reopen_uses_the_same_owner(self):
        app = Mock()
        app._nsapp = tray_app.rumps.rumps.NSApp.alloc().init()
        with patch.object(tray_app, "NSApplication") as native, \
             patch.object(tray_app, "HubRuntime") as runtime, \
             patch.object(sys, "argv", ["Hub"]):
            tray_app.MenuBarApp.configure_console_launch(app)
            app.open_console.assert_called_once_with(None)
            self.assertEqual(native.sharedApplication().setDelegate_.call_count, 2)
            self.assertEqual(native.sharedApplication().setDelegate_.call_args_list[0].args, (None,))
            native.sharedApplication().setDelegate_.assert_called_with(app._nsapp)
            self.assertFalse(app._nsapp.applicationShouldHandleReopen_hasVisibleWindows_(None, False))
            self.assertEqual(app.open_console.call_count, 2)
            runtime.assert_not_called()

    def test_background_start_still_accepts_manual_reopen(self):
        app = Mock()
        app._nsapp = tray_app.rumps.rumps.NSApp.alloc().init()
        with patch.object(tray_app, "NSApplication"), \
             patch.object(sys, "argv", ["Hub", "--background"]):
            tray_app.MenuBarApp.configure_console_launch(app)
            app.open_console.assert_not_called()
            app._nsapp.applicationShouldHandleReopen_hasVisibleWindows_(None, False)
            app.open_console.assert_called_once_with(None)

    def test_second_process_preserves_lock_and_opens_running_instances_port(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(tray_app, "APP_SUPPORT", Path(directory)), \
             patch.object(tray_app, "configured_http_url", return_value="http://127.0.0.1:2100"), \
             patch.object(tray_app.webbrowser, "open") as browser, \
             patch.object(tray_app, "HubRuntime") as runtime:
            owner = tray_app.MenuBarApp.acquire_instance_lock()
            try:
                owner.write("22123")
                owner.flush()
                with self.assertRaises(SystemExit) as caught:
                    tray_app.MenuBarApp.acquire_instance_lock()
                self.assertEqual(caught.exception.code, 0)
                browser.assert_called_once_with("http://127.0.0.1:22123")
                self.assertEqual((Path(directory) / "app.lock").read_text(), "22123")
                runtime.assert_not_called()
            finally:
                fcntl.flock(owner, fcntl.LOCK_UN)
                owner.close()
            owner = tray_app.MenuBarApp.acquire_instance_lock()
            owner.close()

    def test_empty_or_invalid_lock_metadata_falls_back_to_configured_port(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(tray_app, "APP_SUPPORT", Path(directory)), \
             patch.object(tray_app, "configured_http_url", return_value="http://127.0.0.1:2100"), \
             patch.object(tray_app.webbrowser, "open") as browser:
            owner = tray_app.MenuBarApp.acquire_instance_lock()
            try:
                for value in ("", "bad", "-1", "65536", "http://example.com"):
                    with self.subTest(value=value):
                        owner.seek(0)
                        owner.truncate()
                        owner.write(value)
                        owner.flush()
                        browser.reset_mock()
                        with self.assertRaises(SystemExit):
                            tray_app.MenuBarApp.acquire_instance_lock()
                        browser.assert_called_once_with("http://127.0.0.1:2100")
            finally:
                owner.close()

    def test_login_agent_launches_in_background(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(tray_app, "LAUNCH_AGENT", Path(directory) / "agent.plist"), \
             patch.object(tray_app, "launch_arguments", return_value=["/Applications/Hub"]):
            sender = Mock()
            tray_app.MenuBarApp.toggle_login(Mock(), sender)
            data = plistlib.loads(tray_app.LAUNCH_AGENT.read_bytes())
            self.assertEqual(data["ProgramArguments"], ["/Applications/Hub", "--background"])
            self.assertEqual(sender.state, 1)


if __name__ == "__main__":
    unittest.main()
