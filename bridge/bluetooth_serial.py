"""A serial-like stream backed by an explicitly owned macOS RFCOMM channel.

The helper owns the native main run loop, so Bluetooth callbacks also work when
the bridge is run without the menu bar app. Its stdout carries framed messages;
the K210 protocol itself stays unchanged.
"""
import base64
import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


class BluetoothSerial:
    def __init__(self, address, timeout=15, write_timeout=3):
        root = Path(__file__).resolve().parents[1]
        args = ["--bluetooth-serial", address]
        command = ([sys.executable] + args if getattr(sys, "frozen", False)
                   else [sys.executable, str(root / "tray_app.py")] + args)
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                        text=True, bufsize=1)
        self.condition = threading.Condition()
        self.write_lock = threading.Lock()
        self.ready = threading.Event()
        self.received = bytearray()
        self.answers = {}
        self.request_id = 0
        self.error = None
        self.closed = False
        self.write_timeout = write_timeout
        self.reader = threading.Thread(target=self._read_output, daemon=True)
        self.reader.start()
        if not self.ready.wait(timeout):
            self.close()
            raise TimeoutError("蓝牙数据通道打开超时")
        if self.error:
            error = self.error
            self.close()
            raise ConnectionError(error)

    def _read_output(self):
        try:
            for line in self.process.stdout:
                message = json.loads(line)
                with self.condition:
                    kind = message.get("type")
                    if kind == "ready":
                        self.ready.set()
                    elif kind == "data":
                        self.received.extend(base64.b64decode(message["data"], validate=True))
                        if len(self.received) > 65536:
                            self.error = "蓝牙接收缓冲区溢出"
                    elif kind == "written":
                        self.answers[message["id"]] = message["count"]
                    elif kind in ("error", "closed"):
                        self.error = message.get("error") or "蓝牙数据通道已关闭"
                        self.ready.set()
                    self.condition.notify_all()
        except (ValueError, KeyError, TypeError, OSError):
            self.error = "蓝牙数据通道返回无效数据"
        finally:
            with self.condition:
                if not self.closed:
                    self.error = self.error or "蓝牙数据通道已关闭"
                self.ready.set()
                self.condition.notify_all()

    def read(self, size):
        with self.condition:
            if self.error or self.closed:
                raise ConnectionError(self.error or "蓝牙数据通道已关闭")
            result = bytes(self.received[:size])
            del self.received[:size]
            return result

    def _command(self, message):
        self.process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
        self.process.stdin.flush()

    def write(self, data):
        with self.write_lock:
            with self.condition:
                if self.error or self.closed:
                    raise ConnectionError(self.error or "蓝牙数据通道已关闭")
                self.request_id += 1
                request_id = self.request_id
            self._command({"op": "write", "id": request_id,
                           "data": base64.b64encode(data).decode("ascii")})
            deadline = time.monotonic() + self.write_timeout
            with self.condition:
                while request_id not in self.answers:
                    if self.error or self.closed:
                        raise ConnectionError(self.error or "蓝牙数据通道已关闭")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("蓝牙数据发送超时")
                    self.condition.wait(remaining)
                return self.answers.pop(request_id)

    def close(self):
        if self.closed:
            return
        self.closed = True
        try:
            self._command({"op": "close"})
        except (OSError, ValueError):
            pass
        try:
            self.process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            try:
                self.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=1)
        self.reader.join(timeout=1)
        self.process.stdin.close()
        self.process.stdout.close()


def run_channel(address):
    import ctypes
    import objc
    from Foundation import NSDate, NSObject, NSRunLoop
    from .bluetooth_devices import ADDRESS, _device_class

    if not ADDRESS.fullmatch(address):
        raise ValueError("蓝牙设备地址无效")
    device_class = _device_class()
    # Dynamic framework loading has no PyObjC metadata for these pointer args.
    objc.registerMetaDataForSelector(
        b"IOBluetoothSDPServiceRecord", b"getRFCOMMChannelID:",
        {"arguments": {2: {"type": b"o^C"}}})
    objc.registerMetaDataForSelector(
        b"IOBluetoothDevice", b"openRFCOMMChannelSync:withChannelID:delegate:",
        {"arguments": {2: {"type_modifier": b"o"}}})
    objc.registerMetaDataForSelector(
        b"IOBluetoothRFCOMMChannel", b"writeSync:length:",
        {"arguments": {2: {"type": b"n^c", "c_array_length_in_arg": 3}}})

    def emit(message):
        print(json.dumps(message, separators=(",", ":")), flush=True)

    class K210RFCOMMDelegate(NSObject):
        @objc.typedSelector(b"v@:@^vQ")
        def rfcommChannelData_data_length_(self, channel, data, length):
            try:
                # The native pointer is valid only for the duration of this callback.
                payload = ctypes.string_at(data, length)
                emit({"type": "data", "data": base64.b64encode(payload).decode("ascii")})
            except Exception:
                self.channel_closed = True
                emit({"type": "error", "error": "蓝牙接收数据失败"})

        @objc.typedSelector(b"v@:@")
        def rfcommChannelClosed_(self, channel):
            self.channel_closed = True
            emit({"type": "closed"})

    device = device_class.deviceWithAddressString_(address)
    if device is None:
        raise ConnectionError("找不到所选蓝牙设备")
    channel = None
    try:
        status = device.performSDPQuery_(None)
        if status != 0:
            raise ConnectionError("查询蓝牙串口服务失败（系统错误 %s）" % status)
        uuid = objc.lookUpClass("IOBluetoothSDPUUID").uuid16_(0x1101)
        record = device.getServiceRecordForUUID_(uuid)
        if record is None:
            raise ConnectionError("所选设备未提供蓝牙串口服务")
        status, channel_id = record.getRFCOMMChannelID_(None)
        if status != 0:
            raise ConnectionError("无法获取蓝牙串口通道")
        delegate = K210RFCOMMDelegate.alloc().init()
        delegate.channel_closed = False
        status, channel = device.openRFCOMMChannelSync_withChannelID_delegate_(
            None, channel_id, delegate)
        if status != 0 or channel is None:
            raise ConnectionError("打开蓝牙数据通道失败（系统错误 %s）" % status)
        commands = queue.Queue()

        def read_commands():
            try:
                for line in sys.stdin:
                    commands.put(json.loads(line))
            except (OSError, ValueError):
                pass
            finally:
                commands.put({"op": "close"})

        threading.Thread(target=read_commands, daemon=True).start()
        emit({"type": "ready"})
        run_loop = NSRunLoop.currentRunLoop()
        while not delegate.channel_closed:
            try:
                command = commands.get_nowait()
            except queue.Empty:
                with objc.autorelease_pool():
                    # ponytail: commands wait at most 100 ms; wake the run loop if lower latency is needed.
                    run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.1))
                continue
            if command.get("op") == "close":
                break
            if command.get("op") != "write":
                raise ValueError("蓝牙数据通道命令无效")
            payload = base64.b64decode(command["data"], validate=True)
            mtu = max(1, min(int(channel.getMTU()), 1024))
            for offset in range(0, len(payload), mtu):
                chunk = payload[offset:offset + mtu]
                with objc.autorelease_pool():
                    status = channel.writeSync_length_(chunk, len(chunk))
                if status != 0:
                    raise ConnectionError("蓝牙数据发送失败（系统错误 %s）" % status)
            emit({"type": "written", "id": command["id"], "count": len(payload)})
    finally:
        if channel is not None:
            channel.setDelegate_(None)
            channel.closeChannel()
        device.closeConnection()


def main(address):
    try:
        run_channel(address)
    except Exception as exc:
        print(json.dumps({"type": "error", "error": str(exc) or type(exc).__name__}),
              flush=True)
        return 1
    return 0
