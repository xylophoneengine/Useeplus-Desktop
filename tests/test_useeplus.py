from pathlib import Path
import cv2
import numpy as np
import pytest
from microscope import useeplus
from microscope.useeplus import (Packet, parse_packet, FrameAssembler,
                                 iter_packet_file, BUTTON_FLAG)

DATA = Path(__file__).parent / "data" / "packets_640x480.bin"


def make_pkt(cid=7, fid=1, cam=0, flags=0, gsensor=0, chunk=b"\xff\xd8abc"):
    body = bytes([fid, cam, flags]) + gsensor.to_bytes(4, "little", signed=True) + chunk
    return b"\xaa\xbb" + bytes([cid]) + len(body).to_bytes(2, "little") + body


def test_parse_packet_fields():
    p = parse_packet(make_pkt(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz"))
    assert p == Packet(cid=11, fid=5, cam=1, flags=BUTTON_FLAG, gsensor=-3, chunk=b"xyz")


def test_parse_packet_rejects_bad_magic_cid_short():
    assert parse_packet(b"\xbb\xaa" + make_pkt()[2:]) is None
    assert parse_packet(make_pkt(cid=3)) is None
    assert parse_packet(b"\xaa\xbb\x07\x00") is None


def test_parse_packet_truncates_chunk_to_declared_length():
    pkt = make_pkt(chunk=b"12345") + b"PADDING"
    assert parse_packet(pkt).chunk == b"12345"


def test_assembler_emits_on_fid_change_and_validates_jpeg():
    a = FrameAssembler()
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"\xff\xd8AA"))) is None
    assert a.feed(parse_packet(make_pkt(fid=1, chunk=b"BB\xff\xd9"))) is None
    out = a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8CC", flags=BUTTON_FLAG)))
    assert out == b"\xff\xd8AABB\xff\xd9"
    assert a.last_flags == BUTTON_FLAG


def test_assembler_drops_frame_without_soi_eoi():
    a = FrameAssembler()
    a.feed(parse_packet(make_pkt(fid=1, chunk=b"garbage")))
    assert a.feed(parse_packet(make_pkt(fid=2, chunk=b"\xff\xd8"))) is None


def test_recorded_packets_yield_decodable_frames():
    a = FrameAssembler()
    frames = []
    for raw in iter_packet_file(DATA):
        p = parse_packet(raw)
        if p is None:
            continue
        jpeg = a.feed(p)
        if jpeg:
            frames.append(jpeg)
    assert len(frames) >= 10
    for j in frames:
        img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
        assert img is not None and img.shape == (480, 640, 3)


# --- teardown / open robustness -------------------------------------------

class FakeDev:
    """Minimal stand-in; any attribute touched during __del__ would be a regression."""

    def __init__(self):
        self.reset_calls = 0

    def reset(self):
        self.reset_calls += 1


def test_del_does_no_usb_work():
    """__del__ must not call into libusb: doing so aborts the process at shutdown
    ("Assertion failed: (refcnt >= 2), function libusb_ref_device")."""
    cam = object.__new__(useeplus.Camera)

    class Boom:
        def __getattr__(self, name):
            raise AssertionError(f"__del__ touched the USB handle ({name})")

    cam._dev = Boom()
    useeplus.Camera.__del__(cam)          # must not raise
    assert isinstance(cam._dev, Boom)     # and must not have cleared it either


def test_release_is_idempotent_and_clears_handle():
    cam = object.__new__(useeplus.Camera)
    cam._dev = None
    cam.release()                          # no handle: must be a no-op, not an error
    assert cam._dev is None


def test_open_failure_leaves_no_dangling_handle(monkeypatch):
    """On failure the handle is disposed, so _dev must not keep pointing at it -
    otherwise release()/__del__ disposes the same device twice."""
    dev = FakeDev()
    disposed = []
    monkeypatch.setattr(useeplus, "list_devices", lambda: [dev])
    monkeypatch.setattr(useeplus.usb.util, "dispose_resources", lambda d: disposed.append(d))
    monkeypatch.setattr(useeplus, "RESET_SETTLE_S", 0)
    monkeypatch.setattr(useeplus, "RETRY_BACKOFF_S", 0)

    def boom(self, d):
        raise useeplus.usb.core.USBError("boom")

    monkeypatch.setattr(useeplus.Camera, "_init_device", boom)

    cam = object.__new__(useeplus.Camera)
    cam._timeout, cam._index, cam._dev = 1.0, 0, None
    cam._asm = FrameAssembler()
    with pytest.raises(RuntimeError, match="another process may be holding it"):
        cam._open(attempts=2)
    assert cam._dev is None
    assert len(disposed) >= 2              # one per attempt


def test_open_resets_before_handshake(monkeypatch):
    """The iAP handshake only succeeds on a freshly reset device, so the reset must
    happen on the first attempt - not just in the retry path."""
    dev = FakeDev()
    monkeypatch.setattr(useeplus, "list_devices", lambda: [dev])
    monkeypatch.setattr(useeplus.usb.util, "dispose_resources", lambda d: None)
    monkeypatch.setattr(useeplus, "RESET_SETTLE_S", 0)
    monkeypatch.setattr(useeplus.Camera, "_init_device", lambda self, d: None)

    cam = object.__new__(useeplus.Camera)
    cam._timeout, cam._index, cam._dev = 1.0, 0, None
    cam._asm = FrameAssembler()
    cam._open(attempts=3)
    assert dev.reset_calls == 1            # exactly once, on the successful first try
    assert cam._dev is dev
