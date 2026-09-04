"""com.useeplus.protocol driver for the Geek szitman "supercamera" (Wadeo microscope).

Derived from ProbeView upp_camera.py, MIT (c) 2026 Everitt Chase
(https://github.com/echase/ProbeView). See third_party/probeview/LICENSE.

Wire format, one logical packet per 1024-byte bulk read on EP 0x81:
    [AA BB][cid u8][len u16le][fid u8][cam u8][flags u8][gsensor i32le][JPEG chunk]
`len` counts the 7-byte cam header + chunk. A frame is complete when `fid` changes.
cid is 7 or 11 (device alternates head/tail of each frame). flags bit1 = device button.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, NamedTuple

import usb.core
import usb.util

KNOWN_DEVICES = [(0x2CE3, 0x3828), (0x0329, 0x2022)]
EP_OUT, EP_IN = 0x01, 0x81            # interface 1 (com.useeplus.protocol) bulk
EP_IAP_OUT, EP_IAP_IN = 0x02, 0x82    # interface 0 (iAP)
MAGIC_INIT = bytes([0xFF, 0x55, 0xFF, 0x55, 0xEE, 0x10])
CONNECT_CMD = bytes([0xBB, 0xAA, 0x05, 0x00, 0x00])

PKT_SIZE = 0x400
USB_HDR = 5
PAYLOAD_OFFSET = 12
VALID_CIDS = (7, 11)
JPEG_SOI = b"\xff\xd8"
JPEG_EOI = b"\xff\xd9"
BUTTON_FLAG = 0x02
RESOLUTION = (640, 480)
HOMEBREW_DYLIB = "/opt/homebrew/lib/libusb-1.0.dylib"


class Packet(NamedTuple):
    cid: int
    fid: int
    cam: int
    flags: int
    gsensor: int
    chunk: bytes


def parse_packet(pkt: bytes) -> Packet | None:
    """Parse one raw bulk packet. None if magic/cid/length invalid."""
    if len(pkt) < PAYLOAD_OFFSET or pkt[0] != 0xAA or pkt[1] != 0xBB or pkt[2] not in VALID_CIDS:
        return None
    length = pkt[3] | (pkt[4] << 8)
    return Packet(
        cid=pkt[2],
        fid=pkt[5],
        cam=pkt[6],
        flags=pkt[7],
        gsensor=int.from_bytes(pkt[8:12], "little", signed=True),
        chunk=bytes(pkt[PAYLOAD_OFFSET:USB_HDR + length]),
    )


class FrameAssembler:
    """Concatenate chunks until fid changes; emit the finished buffer if it is a full JPEG."""

    def __init__(self) -> None:
        self._buf = bytearray()
        self._fid: int | None = None
        self.last_flags = 0
        self.last_cam = 0
        self.last_gsensor = 0

    def feed(self, p: Packet) -> bytes | None:
        self.last_flags, self.last_cam, self.last_gsensor = p.flags, p.cam, p.gsensor
        out = None
        if self._fid is not None and p.fid != self._fid and self._buf:
            frame = bytes(self._buf)
            self._buf = bytearray()
            if frame.startswith(JPEG_SOI) and frame.endswith(JPEG_EOI):
                out = frame
        self._buf.extend(p.chunk)
        self._fid = p.fid
        return out


def iter_packet_file(path: str | Path) -> Iterator[bytes]:
    """Read the record file written by scripts/dump_packets.py (u16le length + payload)."""
    data = Path(path).read_bytes()
    i = 0
    while i + 2 <= len(data):
        n = int.from_bytes(data[i:i + 2], "little")
        yield data[i + 2:i + 2 + n]
        i += 2 + n


def _backend():
    import usb.backend.libusb1 as libusb1
    b = libusb1.get_backend()
    if b is None:
        b = libusb1.get_backend(find_library=lambda _: HOMEBREW_DYLIB)
    if b is None:
        raise RuntimeError("libusb not found; run `brew install libusb`")
    return b


def list_devices() -> list:
    found = []
    for vid, pid in KNOWN_DEVICES:
        found.extend(usb.core.find(find_all=True, idVendor=vid, idProduct=pid, backend=_backend()))
    return found


class Camera:
    def __init__(self, index: int = 0, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._index = index
        self._dev = None
        self._asm = FrameAssembler()
        self._open()

    # -- open / handshake -------------------------------------------------
    def _find(self):
        devs = list_devices()
        if not devs:
            raise RuntimeError("No supercamera device found (2ce3:3828 / 0329:2022)")
        if self._index >= len(devs):
            raise RuntimeError(f"index {self._index} out of range ({len(devs)} found)")
        return devs[self._index]

    def _open(self, attempts: int = 3) -> None:
        last = None
        for _ in range(attempts):
            dev = self._find()
            self._dev = dev
            try:
                self._init_device(dev)
                return
            except usb.core.USBError as e:
                last = e
                try:
                    dev.reset()
                except Exception:
                    pass
                usb.util.dispose_resources(dev)
                time.sleep(1.5)
        raise RuntimeError(f"could not open device after {attempts} attempts: {last}")

    def _init_device(self, dev) -> None:
        for intf in (0, 1):
            try:
                if dev.is_kernel_driver_active(intf):
                    dev.detach_kernel_driver(intf)
            except Exception:
                pass
        dev.set_configuration()
        usb.util.claim_interface(dev, 0)
        usb.util.claim_interface(dev, 1)
        for _ in range(30):  # drain pending iAP heartbeat
            try:
                dev.read(EP_IAP_IN, 512, timeout=100)
            except usb.core.USBError:
                break
        dev.set_interface_altsetting(interface=1, alternate_setting=1)
        dev.clear_halt(EP_OUT)
        dev.write(EP_IAP_OUT, MAGIC_INIT, timeout=1000)
        dev.write(EP_OUT, CONNECT_CMD, timeout=1000)
        time.sleep(0.3)
        self._asm = FrameAssembler()
        for _ in range(2):  # first frames after connect are partial
            self.read_jpeg()

    # -- reading -----------------------------------------------------------
    def read_jpeg(self) -> tuple[bytes, int] | None:
        """Return (jpeg_bytes, flags) for the next complete frame, or None on timeout.
        Raises usb.core.USBError on hard errors (caller decides on reconnect)."""
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            try:
                raw = bytes(self._dev.read(EP_IN, PKT_SIZE, timeout=1000))
            except usb.core.USBTimeoutError:
                continue
            p = parse_packet(raw)
            if p is None:
                continue
            frame = self._asm.feed(p)
            if frame is not None:
                return frame, self._asm.last_flags
        return None

    def read(self):
        """Return (ok, bgr_ndarray | None, flags)."""
        import cv2
        import numpy as np
        r = self.read_jpeg()
        if r is None:
            return False, None, 0
        jpeg, flags = r
        img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        return img is not None, img, flags

    # -- info / teardown --------------------------------------------------
    @property
    def resolution(self) -> tuple[int, int]:
        return RESOLUTION

    @property
    def serial_number(self) -> str | None:
        try:
            return self._dev.serial_number
        except Exception:
            return None

    def release(self) -> None:
        if self._dev is None:
            return
        try:
            self._dev.set_interface_altsetting(interface=1, alternate_setting=0)
        except Exception:
            pass
        for intf in (1, 0):
            try:
                usb.util.release_interface(self._dev, intf)
            except Exception:
                pass
        try:
            usb.util.dispose_resources(self._dev)
        except Exception:
            pass
        self._dev = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.release()

    def __del__(self):
        self.release()
