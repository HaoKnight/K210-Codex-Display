"""macOS Classic Bluetooth discovery and connection for serial modules."""
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from .serial_link import _bluetooth_serial_addresses_from_ioreg


ADDRESS = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}$")


def _device_class():
    if sys.platform != "darwin":
        raise RuntimeError("蓝牙设备连接目前仅支持 macOS")
    import objc
    objc.loadBundle("IOBluetooth", globals(),
                    bundle_path="/System/Library/Frameworks/IOBluetooth.framework")
    return objc.lookUpClass("IOBluetoothDevice")


def _serial_addresses():
    result = subprocess.run(
        ["ioreg", "-r", "-c", "IOBluetoothDevice", "-d", "1", "-l"],
        capture_output=True, text=True, timeout=3, check=True)
    return _bluetooth_serial_addresses_from_ioreg(result.stdout)


def address_for_port(port):
    if sys.platform != "darwin":
        return None
    try:
        return _serial_addresses().get(port)
    except (OSError, subprocess.SubprocessError):
        return None


def list_devices():
    """List active Bluetooth serial ports; stale saved devices stay hidden."""
    if sys.platform != "darwin":
        return []
    try:
        device_class = _device_class()
        return [{"device": port, "address": address,
                 "name": port.rsplit("/", 1)[-1].removeprefix("cu."),
                 "connected": bool(device and device.isConnected())}
                for port, address in _serial_addresses().items()
                for device in [device_class.deviceWithAddressString_(address)]
                if device and device.isConnected()
                and not port.endswith(".Bluetooth-Incoming-Port")]
    except (ImportError, OSError, subprocess.SubprocessError, RuntimeError):
        return []


def serial_port_for_device(address, timeout=8):
    """Resolve the UI port path; the native stream owns connection lifecycle."""
    if not isinstance(address, str) or not ADDRESS.fullmatch(address):
        raise ValueError("蓝牙设备地址无效")
    address = address.lower()
    deadline = time.monotonic() + timeout
    while True:
        for port, candidate in _serial_addresses().items():
            if candidate.lower() == address:
                return port
        if time.monotonic() >= deadline:
            raise ConnectionError("macOS 尚未提供所选设备的蓝牙串口信息")
        time.sleep(0.25)


def pick_device(action, timeout=300):
    """Open an app-owned picker for a nearby device or a new pairing."""
    if action not in ("select", "pair"):
        raise ValueError("蓝牙选择操作无效")
    root = Path(__file__).resolve().parents[1]
    flag = "--bluetooth-" + action
    command = ([sys.executable, flag] if getattr(sys, "frozen", False)
               else [sys.executable, str(root / "tray_app.py"), flag])
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout + 5)
    except subprocess.TimeoutExpired as exc:
        raise TimeoutError("蓝牙配对窗口等待超时") from exc
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        raise RuntimeError(result.stderr.strip()[-500:] or "蓝牙配对窗口未返回结果")
    try:
        response = json.loads(lines[-1])
    except (ValueError, TypeError) as exc:
        raise RuntimeError("蓝牙配对窗口返回了无效结果") from exc
    if result.returncode != 0 and not response.get("cancelled"):
        raise RuntimeError(response.get("error") or "蓝牙配对失败")
    return response


def pair_new_device(timeout=300):
    return pick_device("pair", timeout)


def select_device(timeout=300):
    return pick_device("select", timeout)
