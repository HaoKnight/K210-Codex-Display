"""K210 online main.py updater, loaded after the display buffer is allocated."""
import os
import time
try:
    import ujson as json
except ImportError:
    import json
try:
    import ubinascii as binascii
except ImportError:
    import binascii
try:
    import uhashlib as hashlib
except ImportError:
    import hashlib

PROGRAM_UPDATE_MAX_BYTES = 512 * 1024
PROGRAM_UPDATE_CHUNK_BYTES = 768

class ProgramUpdater:
    """Receive a verified main.py replacement over the existing JSON UART."""
    def __init__(self):
        self.output = None
        self.target = None
        self.temporary = None
        self.expected_size = 0
        self.expected_sha256 = None
        self.received = 0
        self.next_index = 0
        self.hasher = None
        self.reset_at = None
        self.last_complete = None

    @staticmethod
    def _exists(path):
        try:
            with open(path, "rb"):
                return True
        except OSError:
            return False

    @staticmethod
    def _remove(path):
        try:
            os.remove(path)
        except OSError:
            pass

    @staticmethod
    def _target_path(requested):
        roots = os.listdir("/")
        has_sd = "sd" in roots
        if requested == "sd" and not has_sd:
            raise OSError("SD card is not mounted")
        if requested == "flash":
            return "/flash/main.py"
        return "/sd/main.py" if has_sd else "/flash/main.py"

    @staticmethod
    def _send(uart, stage, ok=True, **fields):
        message = {"v": 1, "type": "update_ack", "stage": stage,
                   "ok": bool(ok)}
        message.update(fields)
        uart.write((json.dumps(message) + "\n").encode("utf-8"))

    def _close(self):
        if self.output is not None:
            try:
                self.output.close()
            except Exception:
                pass
        self.output = None

    def abort(self, remove_temporary=True):
        self._close()
        if remove_temporary and self.temporary:
            self._remove(self.temporary)
        self.target = None
        self.temporary = None
        self.expected_size = 0
        self.expected_sha256 = None
        self.received = 0
        self.next_index = 0
        self.hasher = None

    def begin(self, uart, packet):
        self.abort()
        self.target = self._target_path(packet.get("target", "auto"))
        self.temporary = self.target + ".new"
        self._remove(self.temporary)
        self.output = open(self.temporary, "wb")
        self.expected_size = packet["size"]
        self.expected_sha256 = packet["sha256"].lower()
        self.hasher = hashlib.sha256()
        self.last_complete = None
        self._send(uart, "begin", target=self.target, next=0)

    def chunk(self, uart, packet):
        if self.output is None:
            raise ValueError("no update in progress")
        index = packet["index"]
        if index < self.next_index:
            self._send(uart, "chunk", next=self.next_index,
                       received=self.received)
            return
        if index != self.next_index:
            raise ValueError("unexpected chunk index")
        block = binascii.a2b_base64(packet["data"].encode("ascii"))
        if not block or len(block) > PROGRAM_UPDATE_CHUNK_BYTES:
            raise ValueError("invalid chunk size")
        if self.received + len(block) > self.expected_size:
            raise ValueError("update is larger than announced")
        self.output.write(block)
        self.hasher.update(block)
        self.received += len(block)
        self.next_index += 1
        self._send(uart, "chunk", next=self.next_index,
                   received=self.received)

    def finish(self, uart):
        if self.output is None:
            if self.last_complete is not None:
                self._send(uart, "complete", **self.last_complete)
                return
            raise ValueError("no update in progress")
        self._close()
        if self.received != self.expected_size:
            raise ValueError("update size mismatch")
        actual_sha256 = binascii.hexlify(self.hasher.digest()).decode("ascii")
        if actual_sha256 != self.expected_sha256:
            raise ValueError("update checksum mismatch")

        target, temporary = self.target, self.temporary
        backup = target + ".bak"
        self._remove(backup)
        had_previous = self._exists(target)
        if had_previous:
            os.rename(target, backup)
        try:
            os.rename(temporary, target)
        except Exception:
            if had_previous and not self._exists(target) and self._exists(backup):
                os.rename(backup, target)
            raise
        completed = {"target": target, "size": self.received,
                     "sha256": actual_sha256, "backup": had_previous,
                     "restart_ms": 1200}
        self.last_complete = completed
        self.abort(remove_temporary=False)
        self.last_complete = completed
        self.reset_at = time.ticks_ms() + 1200
        self._send(uart, "complete", **completed)

    def handle(self, uart, packet):
        packet_type = packet.get("type")
        if packet_type not in ("update_begin", "update_chunk", "update_end",
                               "update_abort"):
            return False
        stage = packet_type[7:]
        try:
            if packet_type == "update_begin":
                self.begin(uart, packet)
            elif packet_type == "update_chunk":
                self.chunk(uart, packet)
            elif packet_type == "update_end":
                self.finish(uart)
            else:
                self.abort()
                self._send(uart, "abort")
        except Exception as exc:
            self.abort()
            self._send(uart, stage, ok=False,
                       error=type(exc).__name__ + ": " + str(exc))
        return True

    def restart_due(self):
        return (self.reset_at is not None and
                time.ticks_diff(time.ticks_ms(), self.reset_at) >= 0)

