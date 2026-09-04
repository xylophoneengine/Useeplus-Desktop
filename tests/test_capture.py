import time
import numpy as np
import usb.core
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from microscope.capture import CaptureThread


def _app():
    return QCoreApplication.instance() or QCoreApplication([])


def spin(ms):
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class FakeCam:
    def __init__(self, frames=5, fail_after=None):
        self.n, self.fail_after, self.released = 0, fail_after, False
        self.serial_number = "S123"
        self._frames = frames

    def read(self):
        if self.fail_after is not None and self.n >= self.fail_after:
            raise usb.core.USBError("boom")
        if self.n >= self._frames:
            time.sleep(0.01)
            return False, None, 0
        self.n += 1
        return True, np.zeros((480, 640, 3), np.uint8), 0x02 if self.n == 3 else 0

    def release(self):
        self.released = True


def test_emits_frames_and_connected():
    _app()
    cam = FakeCam(frames=5)
    t = CaptureThread(camera_factory=lambda: cam, retry_s=0.05)
    got, conn = [], []
    t.frameReady.connect(lambda f, fl: got.append((f.shape, fl)))
    t.connected.connect(conn.append)
    t.start(); spin(300); t.stop()
    assert conn == ["S123"]
    assert len(got) == 5 and got[2][1] == 0x02 and got[0][0] == (480, 640, 3)
    assert cam.released


def test_no_device_retries_and_reports():
    _app()
    calls = []
    def factory():
        calls.append(1)
        raise RuntimeError("No supercamera device found")
    t = CaptureThread(camera_factory=factory, retry_s=0.05)
    dis = []
    t.disconnected.connect(dis.append)
    t.start(); spin(250); t.stop()
    assert len(calls) >= 3 and dis and "No supercamera" in dis[0]


def test_usb_error_midstream_reconnects():
    _app()
    cams = [FakeCam(frames=10, fail_after=2), FakeCam(frames=3)]
    t = CaptureThread(camera_factory=lambda: cams.pop(0), retry_s=0.05)
    got, dis, conn = [], [], []
    t.frameReady.connect(lambda f, fl: got.append(1))
    t.disconnected.connect(dis.append); t.connected.connect(conn.append)
    t.start(); spin(400); t.stop()
    assert len(got) == 5 and len(dis) == 1 and len(conn) == 2
