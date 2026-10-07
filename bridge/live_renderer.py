"""Load the user-edited CanMV renderer without running its hardware loop."""
import hashlib
from pathlib import Path
import traceback


class LiveRenderer:
    """Hot-reload ``EmbeddedDisplay`` while retaining the last valid version."""

    def __init__(self, path):
        self.path = Path(path)
        self._digest = None
        self._stat = None
        self._display_type = None
        self.load_error = None
        self.render_error = None

    @property
    def error(self):
        return self.load_error or self.render_error

    def display_type(self):
        try:
            stat = self.path.stat()
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size, stat.st_ino)
            font_path = self.path.with_name('font.bin')
            font_stat = font_path.stat() if font_path.exists() else None
            signature += (None if font_stat is None else
                          (font_stat.st_mtime_ns, font_stat.st_size, font_stat.st_ino),)
            if signature == self._stat:
                return self._display_type
            source = self.path.read_bytes()
            self._stat = signature
            digest = hashlib.sha256(source + (font_path.read_bytes() if font_stat else b'')).digest()
            if digest == self._digest:
                if self._display_type is not None:
                    self.load_error = None
                return self._display_type
            namespace = {
                "__name__": "canmv_preview_bridge",
                "__file__": str(self.path),
                "__package__": None,
            }
            exec(compile(source, str(self.path), "exec"), namespace)
            display_type = namespace.get("EmbeddedDisplay")
            if not callable(display_type):
                raise TypeError("CanMV_Preview.py 未定义 EmbeddedDisplay")
            self._display_type = display_type
            self._digest = digest
            self.load_error = None
            self.render_error = None
        except Exception:
            self._stat = None
            detail = traceback.format_exc().strip().splitlines()[-1]
            self.load_error = "新版界面载入失败，继续显示上一版：" + detail
        return self._display_type

    def report_render_error(self, exc):
        self.render_error = "新版界面绘制失败，已使用兼容画面：%s: %s" % (
            type(exc).__name__, exc)
