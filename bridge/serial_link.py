import base64
import hashlib
import json
import time
from bridge.device_support.protocol import LineBuffer


UPDATE_CHUNK_BYTES = 768
HEARTBEAT_SECONDS = 5
CONNECTION_TIMEOUT = 15


class SerialLink:
    def __init__(self, port, baud):
        self.port, self.baud = port, baud
        self.conn = None
        self.next_open = 0
        self.last_ack = 0
        self.last_ack_seq = None
        self.last_ack_at = None
        self.last_time_sync = 0
        self.last_time_sync_at = None
        self.device_flip = None
        self.device_flip_at = None
        self.device_page = "dashboard"
        self.device_page_at = None
        self.device_show_codex = True
        self.device_brightness = 100
        self.device_brightness_at = None
        self.device_program_update = None
        self.sync_request_seen = None
        self.sync_requested = False
        self.device_boot_id = None
        self.device_boot_at = None
        self.device_boot_count = 0
        self.device_info = {}
        self.device_info_at = None
        self.force_time_sync = False
        self.sent = {}
        self.error = None
        self.last_tx = None
        self.last_activity = None
        self.last_state = None
        self.pending_state_seq = None
        self.state_confirmed = False
        self.force_state = False
        self.heartbeat_supported = False
        self.parser = LineBuffer()

    def request_time_sync(self):
        """Force the next outbound packet to carry time_sync=true."""
        self.force_time_sync = True
        self.last_time_sync = 0

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None
        self.last_ack = 0
        self.last_time_sync = 0
        self.last_ack_seq = None
        self.sent.clear()
        self.last_tx = None
        self.last_activity = None
        self.last_state = None
        self.state_confirmed = False
        self.pending_state_seq = None
        self.heartbeat_supported = False

    def take_device_flip(self):
        value = self.device_flip
        self.device_flip = None
        return value

    def set_device_ui(self, page=None, brightness=None, show_codex=None):
        """Set UI values that the next host packet should request."""
        if show_codex is not None:
            if not isinstance(show_codex, bool):
                raise ValueError("invalid show_codex")
            self.device_show_codex = show_codex
        if page is not None:
            if page not in ("dashboard", "settings", "info"):
                raise ValueError("invalid device page")
            self.device_page = page
            self.device_page_at = time.time()
        if brightness is not None:
            if (not isinstance(brightness, int) or isinstance(brightness, bool)
                    or not 20 <= brightness <= 100):
                raise ValueError("invalid device brightness")
            self.device_brightness = brightness
            self.device_brightness_at = time.time()

    def ui_state(self):
        return {"page": self.device_page, "show_codex": self.device_show_codex,
                "brightness": self.device_brightness}

    def _accept_device_ui(self, message):
        changed = False
        if isinstance(message.get("show_codex"), bool):
            changed = self.device_show_codex != message["show_codex"]
            self.device_show_codex = message["show_codex"]
        if isinstance(message.get("flip"), bool):
            self.device_flip = message["flip"]
            self.device_flip_at = time.time()
            changed = True
        if message.get("page") in ("dashboard", "settings", "info"):
            changed = changed or self.device_page != message["page"]
            self.device_page = message["page"]
            self.device_page_at = time.time()
        brightness = message.get("brightness")
        if (isinstance(brightness, int) and not isinstance(brightness, bool)
                and 20 <= brightness <= 100):
            changed = changed or self.device_brightness != brightness
            self.device_brightness = brightness
            self.device_brightness_at = time.time()
        update_version = message.get("program_update")
        if isinstance(update_version, int) and not isinstance(update_version, bool):
            self.device_program_update = update_version
        boot_id = message.get("boot_id")
        if (isinstance(boot_id, int) and not isinstance(boot_id, bool)
                and boot_id >= 0 and boot_id != self.device_boot_id):
            self.device_info = {}
            self.device_info_at = None
            self.force_state = True
            self.state_confirmed = False
            self.device_boot_id = boot_id
            self.device_boot_at = time.time()
            self.device_boot_count += 1
            # A K210 reset does not always disconnect its USB serial device.
            # Force the next state packet to establish a fresh clock base.
            self.last_time_sync = 0
            self.force_time_sync = True
            changed = True
        request = message.get("sync_request")
        if type(request) is int and request > 0:
            token = (self.device_boot_id, request)
            if token != self.sync_request_seen:
                self.sync_request_seen = token
                self.sync_requested = True
                changed = True
        self.heartbeat_supported = message.get("heartbeat") == 1
        info = message.get("device_info")
        if isinstance(info, dict):
            version = info.get("version")
            numeric = ("uptime_ms", "rx_age_ms", "boot_ts", "updated_at")
            valid = isinstance(version, str) and 0 < len(version) <= 32
            valid = valid and all(info.get(key) is None or (
                isinstance(info[key], int) and not isinstance(info[key], bool)
                and 0 <= info[key] <= 10**15) for key in numeric)
            if valid:
                self.device_info = {key: info.get(key) for key in numeric}
                self.device_info["version"] = version
                self.device_info_at = time.monotonic()
                changed = True
        return changed

    def _accept_standard_response(self, message):
        if message.get("type") == "ui":
            changed = self._accept_device_ui(message)
            if changed:
                # Echo touch changes promptly; only its ACK proves the round trip.
                self.force_state = True
            return changed
        if message.get("type") != "ack" or message.get("seq") not in self.sent:
            return False
        seq = message["seq"]
        changed = self._accept_device_ui(message)
        if self.last_ack_seq is None or seq > self.last_ack_seq:
            self.last_ack, self.last_ack_seq = time.monotonic(), seq
            self.last_ack_at = time.time()
            self.last_activity = self.last_ack
            if seq == self.pending_state_seq:
                self.state_confirmed = True
            if self.sent[seq][1]:
                self.last_time_sync_at = self.last_ack_at
                self.last_time_sync = time.monotonic()
        return changed

    def _read_responses(self):
        changed = False
        for line in self.parser.feed(self.conn.read(4096)):
            try:
                message = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(message, dict) or message.get("v") != 1:
                continue
            changed = self._accept_standard_response(message) or changed
        return changed

    def _open(self):
        if not self.port:
            raise ConnectionError("未选择 K210 串口")
        if self.conn is not None:
            return
        import serial
        self.conn = serial.Serial(port=None, baudrate=self.baud, timeout=0,
                                  write_timeout=2)
        self.conn.dtr = False
        self.conn.rts = False
        self.conn.port = self.port
        self.conn.open()
        self.parser = LineBuffer()

    def _write_json(self, packet):
        data = json.dumps(packet, ensure_ascii=True,
                          separators=(",", ":")).encode() + b"\n"
        if len(data) > 2048:
            raise ValueError("串口升级数据包过大")
        if self.conn.write(data) != len(data):
            raise IOError("串口升级数据写入不完整")

    def _wait_update_ack(self, stage, expected_next=None, timeout=3):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = self.conn.read(4096)
            if not data:
                time.sleep(0.01)
                continue
            for line in self.parser.feed(data):
                try:
                    message = json.loads(line)
                except (ValueError, UnicodeError):
                    continue
                if not isinstance(message, dict) or message.get("v") != 1:
                    continue
                if message.get("type") != "update_ack":
                    self._accept_standard_response(message)
                    continue
                ack_stage = message.get("stage")
                if ack_stage != stage and not (stage == "complete" and ack_stage == "end" and not message.get("ok")):
                    continue
                if not message.get("ok"):
                    raise RuntimeError(message.get("error") or "K210 拒绝程序更新")
                if expected_next is not None and message.get("next") != expected_next:
                    continue
                return message
        raise TimeoutError("等待 K210 升级确认超时")

    def _update_exchange(self, packet, stage, expected_next=None, retries=3):
        timeout_error = None
        for _ in range(retries):
            self._write_json(packet)
            try:
                return self._wait_update_ack(stage, expected_next)
            except TimeoutError as exc:
                timeout_error = exc
        raise timeout_error

    def update_program(self, source, target="auto", progress=None):
        """Upload one verified main.py and let the device restart into it."""
        if not isinstance(source, bytes) or not source:
            raise ValueError("待更新的 main.py 内容为空")
        if len(source) > 512 * 1024:
            raise ValueError("main.py 超过 512 KB 限制")
        if target not in ("auto", "flash", "sd"):
            raise ValueError("无效的 K210 更新位置")
        self._open()
        if not self.last_ack or time.monotonic() - self.last_ack >= CONNECTION_TIMEOUT:
            raise ConnectionError("K210 尚未完成实时连接")
        if self.device_program_update != 1:
            raise RuntimeError(
                "K210 端还不支持在线更新；请先用 CanMV IDE 写入新版 main.py")

        digest = hashlib.sha256(source).hexdigest()
        total_chunks = (len(source) + UPDATE_CHUNK_BYTES - 1) // UPDATE_CHUNK_BYTES
        begin = {"v": 1, "type": "update_begin", "size": len(source),
                 "sha256": digest, "target": target}
        try:
            try:
                begin_ack = self._update_exchange(begin, "begin")
            except TimeoutError as exc:
                raise RuntimeError(
                    "K210 未确认开始更新，请检查连接、设备运行状态和存储空间") from exc
            if progress:
                progress(0, total_chunks)
            for index in range(total_chunks):
                block = source[index * UPDATE_CHUNK_BYTES:
                               (index + 1) * UPDATE_CHUNK_BYTES]
                packet = {"v": 1, "type": "update_chunk", "index": index,
                          "data": base64.b64encode(block).decode("ascii")}
                self._update_exchange(packet, "chunk", expected_next=index + 1)
                if progress:
                    progress(index + 1, total_chunks)
            complete = self._update_exchange(
                {"v": 1, "type": "update_end"}, "complete", retries=2)
            if complete.get("sha256") != digest or complete.get("size") != len(source):
                raise IOError("K210 返回的文件校验结果不一致")
            restart_ms = int(complete.get("restart_ms", 1200))
            result = {"target": complete.get("target", begin_ack.get("target")),
                      "size": len(source), "sha256": digest,
                      "backup": bool(complete.get("backup")),
                      "restart_ms": restart_ms}
            self.error = None
            self.close()
            # The device acknowledges the completed write before its delayed
            # reset. Do not reconnect early and accidentally sync the old run.
            self.next_open = time.monotonic() + restart_ms / 1000.0 + 0.35
            self.request_time_sync()
            return result
        except Exception as exc:
            try:
                self._write_json({"v": 1, "type": "update_abort"})
            except Exception:
                pass
            self.error = type(exc).__name__ + ": " + str(exc)
            raise

    def poll(self):
        """Read device-originated UI events without sending another state packet."""
        if self.conn is None:
            return False
        try:
            changed = self._read_responses()
            self.error = None
            return changed
        except Exception as exc:
            self.error = type(exc).__name__ + ": 检查串口、占用及接线"
            self.close()
            return False

    def exchange(self, packet, clock_sync_seconds=7200):
        outbound = dict(packet)
        outbound["time_sync"] = False
        if not self.port:
            return outbound
        try:
            if self.conn is None:
                if time.monotonic() < self.next_open:
                    return outbound
                self.next_open = time.monotonic() + 5
                self._open()
            now = time.monotonic()
            sync_due = (self.force_time_sync or not self.last_time_sync
                        or now - self.last_time_sync >= clock_sync_seconds)
            # Wall-clock seconds and sequence numbers are transport metadata.
            state = {key: value for key, value in packet.items()
                     if key not in ("seq", "ts", "time_sync")}
            changed = state != self.last_state
            idle_due = (self.last_tx is None or
                        now - max(self.last_tx, self.last_activity or 0) >= HEARTBEAT_SECONDS)
            retry_due = self.last_tx is None or now - self.last_tx >= HEARTBEAT_SECONDS
            if not (changed or self.force_state or self.force_time_sync or idle_due
                    or (not self.state_confirmed and retry_due)
                    or (sync_due and retry_due)):
                return outbound
            send_state = (changed or self.force_state or not self.state_confirmed
                          or sync_due or not self.heartbeat_supported)
            if send_state:
                outbound["time_sync"] = sync_due
            else:
                outbound = {"v": 1, "type": "heartbeat", "seq": packet["seq"]}
            data = json.dumps(outbound, ensure_ascii=True, separators=(",", ":")).encode() + b"\n"
            if len(data) > 2048:
                raise ValueError("packet too large")
            if self.conn.write(data) != len(data):
                raise IOError("short serial write")
            self.last_tx = now
            if send_state:
                self.last_state = state
                self.pending_state_seq = packet["seq"]
                self.state_confirmed = False
                self.force_state = False
            if sync_due:
                self.force_time_sync = False
            self.sent[packet["seq"]] = (now, sync_due)
            self.sent = {seq: meta for seq, meta in self.sent.items()
                         if time.monotonic() - meta[0] < 15}
            self._read_responses()
            self.error = None
        except Exception as exc:
            self.error = type(exc).__name__ + (": 请安装 requirements.txt" if isinstance(exc, ImportError) else ": 检查串口、占用及接线")
            self.close()
        # Preview always receives the full state, never the heartbeat envelope.
        result = dict(packet)
        result["time_sync"] = outbound.get("time_sync", False)
        return result

    def info(self):
        device_info = dict(self.device_info)
        if self.device_info_at is not None:
            elapsed = max(0, int((time.monotonic() - self.device_info_at) * 1000))
            for key in ("uptime_ms", "rx_age_ms"):
                if device_info.get(key) is not None:
                    device_info[key] += elapsed
        return {"port": self.port or "未选择（仅预览）", "open": self.conn is not None,
                "touch_sync_supported": True,
                "touch_sync_request": self.sync_request_seen,
                "connected": bool(self.last_ack and time.monotonic() - self.last_ack < CONNECTION_TIMEOUT),
                "ack_seq": self.last_ack_seq, "ack_at": self.last_ack_at,
                "time_sync_at": self.last_time_sync_at,
                "device_flip": self.device_flip,
                "device_flip_at": self.device_flip_at,
                "device_page": self.device_page,
                "device_page_at": self.device_page_at,
                "device_brightness": self.device_brightness,
                "device_show_codex": self.device_show_codex,
                "device_brightness_at": self.device_brightness_at,
                "device_program_update": self.device_program_update,
                "device_boot_id": self.device_boot_id,
                "device_boot_at": self.device_boot_at,
                "device_boot_count": self.device_boot_count,
                "device_info": device_info,
                "error": self.error}


def list_ports():
    try:
        from serial.tools.list_ports import comports
        return [{"device": p.device, "description": p.description} for p in comports()]
    except ImportError:
        return []
