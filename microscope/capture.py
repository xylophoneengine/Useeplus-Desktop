"""Background USB reader. Owns the Camera; emits frames and connection state to the GUI thread."""
from __future__ import annotations

import sys
import time
from collections import deque

import usb.core
from PySide6.QtCore import QThread, Signal

from .useeplus import Camera


def _default_camera():
    return Camera(timeout=1.0)   # short read timeout so stop() is responsive


class CaptureThread(QThread):
    frameReady = Signal(object, int)   # BGR ndarray, flags
    connected = Signal(str)            # serial
    disconnected = Signal(str)         # reason
    fps = Signal(float)

    def __init__(self, camera_factory=_default_camera, retry_s: float = 2.0, parent=None) -> None:
        super().__init__(parent)
        self._factory = camera_factory
        self._retry_s = retry_s
        self._run = False

    def start(self, *args, **kwargs) -> None:
        self._run = True
        super().start(*args, **kwargs)

    def stop(self) -> None:
        self._run = False
        if not self.wait(10000):
            print("CaptureThread: worker did not exit within 10 s", file=sys.stderr)

    def _sleep(self, s: float) -> None:
        end = time.monotonic() + s
        while self._run and time.monotonic() < end:
            time.sleep(0.02)

    def run(self) -> None:
        while self._run:
            cam = None
            try:
                cam = self._factory()
            except Exception as e:
                self.disconnected.emit(str(e))
                self._sleep(self._retry_s)
                continue
            self.connected.emit(cam.serial_number or "")
            stamps: deque[float] = deque(maxlen=64)
            last_fps_emit = 0.0
            try:
                while self._run:
                    ok, frame, flags = cam.read()
                    if not ok:
                        continue
                    self.frameReady.emit(frame, flags)
                    now = time.monotonic()
                    stamps.append(now)
                    if now - last_fps_emit > 0.5 and len(stamps) > 1:
                        dt = stamps[-1] - stamps[0]
                        if dt > 0:
                            self.fps.emit((len(stamps) - 1) / dt)
                            last_fps_emit = now
            except usb.core.USBError as e:
                self.disconnected.emit(f"USB error: {e}")
            except Exception as e:
                self.disconnected.emit(f"capture error: {e!r}")
            finally:
                try:
                    cam.release()
                except Exception:
                    pass
            if self._run:
                self._sleep(self._retry_s)
